#!/usr/bin/env python3
"""One-shot, localhost-safe Qwen3.8 hardware qualification.

The report contains bounded metrics and pass/fail evidence only. It never stores
prompts, model output, credentials, or private conversation data.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import re
import socket
import subprocess
import time

MODEL = Path("/Users/jerson/AI/models/qwen38-27b-8bit")
REPORT = Path("/Users/jerson/AI/runtime/qualification/qwen38-v0.1.json")
REVISION = "815b83c0df8ffd1d1b5244cf75fd6ef14fca9ef9"
REQUIRED_FILES = {"config.json", "generation_config.json", "model.safetensors.index.json", "processor_config.json", "tokenizer.json", "tokenizer_config.json"}


def memory() -> dict[str, object]:
    pressure = subprocess.check_output(["memory_pressure", "-Q"], text=True)
    available = int(re.search(r"free percentage: (\d+)%", pressure).group(1))
    page_size = int(subprocess.check_output(["sysctl", "-n", "hw.pagesize"], text=True))
    pages: dict[str, int] = {}
    for line in subprocess.check_output(["vm_stat"], text=True).splitlines()[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            pages[key] = int(value.strip().rstrip("."))
    reclaimable = sum(pages.get(name, 0) for name in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
    swap_text = subprocess.check_output(["sysctl", "-n", "vm.swapusage"], text=True).strip()
    swap_match = re.search(r"used = ([0-9.]+)([MGT])", swap_text)
    swap_used_gib = 0.0
    if swap_match:
        swap_used_gib = float(swap_match.group(1))
        if swap_match.group(2) == "M":
            swap_used_gib /= 1024
        elif swap_match.group(2) == "T":
            swap_used_gib *= 1024
    return {"available_percent": available, "reclaimable_gib": round(reclaimable * page_size / 1024**3, 3), "swap_used_gib": round(swap_used_gib, 3)}


def require_complete() -> dict[str, object]:
    missing_config = sorted(name for name in REQUIRED_FILES if not (MODEL / name).is_file())
    if missing_config:
        raise RuntimeError("MODEL_SNAPSHOT_INCOMPLETE")
    index = json.loads((MODEL / "model.safetensors.index.json").read_text())
    shards = sorted(set(index.get("weight_map", {}).values()))
    if len(shards) != 6 or any(not (MODEL / name).is_file() for name in shards):
        raise RuntimeError("MODEL_SNAPSHOT_INCOMPLETE")
    from safetensors import safe_open
    for name in shards:
        with safe_open(MODEL / name, framework="numpy") as handle:
            if not handle.keys():
                raise RuntimeError("EMPTY_SAFETENSORS_SHARD")
    return {"shards": len(shards), "shard_bytes": sum((MODEL / name).stat().st_size for name in shards), "stale_incomplete_count": sum(1 for _ in MODEL.rglob("*.incomplete"))}


def port_8000_free() -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", 8000)) != 0


def context(processor, target: int) -> str:
    roots = (Path("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9"), Path("/usr/lib/python3.9"))
    parts: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                value = path.read_text(errors="strict")
            except (OSError, UnicodeError):
                continue
            parts.append(f"\nFILE {path.name}\n{value}")
            text = "".join(parts)
            if len(processor.tokenizer.encode(text)) >= target:
                return text
    raise RuntimeError("PUBLIC_STDLIB_CONTEXT_UNAVAILABLE")


def healthy_for_next_test(baseline_swap_gib: float) -> bool:
    current = memory()
    # A loaded 29.5 GB unified-memory model naturally lowers the reported free
    # percentage. Stop only at the genuine safety edge or on runaway swap; a
    # 25% in-residency gate skipped healthy requests despite flat swap.
    return bool(current["available_percent"] >= 8 and float(current["swap_used_gib"]) - baseline_swap_gib < 4.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--try-64k", action="store_true")
    args = parser.parse_args()
    snapshot = require_complete()
    if not port_8000_free():
        raise RuntimeError("CURRENT_OMLX_IS_RUNNING_REFUSE_SECOND_HEAVY_MODEL")
    start_memory = memory()
    # memory_pressure is authoritative on unified memory. Fixed free-RAM gates
    # reject healthy reclaimable/compressed states and are intentionally avoided.
    if start_memory["available_percent"] < 65 or start_memory["swap_used_gib"] > 8:
        raise RuntimeError("MEMORY_PREFLIGHT_FAILED")

    from mlx_vlm import load, stream_generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    import mlx.core as mx

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    image_path = REPORT.parent / "qwen38-vision-selftest.png"
    metrics: list[dict[str, object]] = []
    model = processor = None
    load_seconds = None
    end_memory = recovered = None
    resource_limited = False
    try:
        loaded = time.monotonic()
        model, processor = load(str(MODEL))
        load_seconds = time.monotonic() - loaded
        config = load_config(str(MODEL))

        def run(name: str, prompt: str, validator, *, image: str | None = None, max_tokens: int = 128) -> None:
            nonlocal resource_limited
            if resource_limited:
                metrics.append({"name": name, "ok": False, "error": "SKIPPED_AFTER_RESOURCE_LIMIT"})
                return
            if not healthy_for_next_test(float(start_memory["swap_used_gib"])):
                metrics.append({"name": name, "ok": False, "error": "RESOURCE_GUARD"})
                resource_limited = True
                return
            formatted = apply_chat_template(processor, config, prompt, num_images=1 if image else 0)
            started = time.monotonic()
            first_token_at = None
            text_parts: list[str] = []
            result = None
            try:
                for item in stream_generate(model, processor, formatted, image=image, max_tokens=max_tokens, temperature=0, enable_thinking=False, verbose=False):
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    text_parts.append(item.text)
                    result = item
            except RuntimeError as exc:
                category = "METAL_OUT_OF_MEMORY" if "Insufficient Memory" in str(exc) else "RUNTIME_ERROR"
                metrics.append({"name": name, "ok": False, "error": category, "total_seconds": round(time.monotonic() - started, 3)})
                resource_limited = True
                return
            output = "".join(text_parts).strip()
            ok = bool(result and validator(output, result))
            metrics.append({
                "name": name, "ok": ok, "finish_reason": result.finish_reason if result else None,
                "prompt_tokens": result.prompt_tokens if result else 0, "generation_tokens": result.generation_tokens if result else 0,
                "prompt_tps": round(result.prompt_tps, 3) if result else 0, "generation_tps": round(result.generation_tps, 3) if result else 0,
                "ttft_seconds": round(first_token_at - started, 3) if first_token_at else None,
                "peak_memory_gib": round(result.peak_memory, 3) if result else None,
                "total_seconds": round(time.monotonic() - started, 3), "output_chars": len(output),
            })

        run("INSTRUCTION", "只输出：LOCAL-OK", lambda value, _: value == "LOCAL-OK", max_tokens=24)
        run("CHINESE", "请用两句中文说明本地推理的一个优点和一个限制。", lambda value, _: bool(re.search(r"[\u4e00-\u9fff]", value)))
        run("ENGLISH", "In two sentences, explain one benefit and one limitation of local inference.", lambda value, _: bool(re.search(r"[A-Za-z]", value)))

        def exact_json(value, _):
            try:
                return json.loads(value) == {"status": "ok", "count": 2}
            except json.JSONDecodeError:
                return False

        def exact_tool(value, _):
            try:
                return json.loads(value) == {"name": "lookup", "arguments": {"query": "mlx"}}
            except json.JSONDecodeError:
                return False

        run("JSON", '只输出严格JSON对象，不要代码围栏：{"status":"ok","count":2}', exact_json, max_tokens=64)
        run("TOOL_SCHEMA", '只输出严格JSON，不要代码围栏：{"name":"lookup","arguments":{"query":"mlx"}}', exact_tool, max_tokens=80)
        from PIL import Image, ImageDraw
        canvas = Image.new("RGB", (320, 180), "white")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((25, 30, 135, 145), fill="blue")
        draw.ellipse((180, 35, 290, 145), fill="red")
        canvas.save(image_path)
        run("VISION", "描述图中两个主要形状和颜色。", lambda value, _: "蓝" in value and "红" in value and any(term in value for term in ("方", "矩形")) and any(term in value for term in ("圆", "椭圆")), image=str(image_path))
        run("CONTEXT_32K", context(processor, 30000) + "\n请用一句话说明这些是何种语言的标准库源码。", lambda value, result: result.prompt_tokens >= 30000 and "python" in value.lower(), max_tokens=64)
        if args.try_64k:
            run("CONTEXT_64K", context(processor, 60000) + "\n请用一句话说明这些是何种语言的标准库源码。", lambda value, result: result.prompt_tokens >= 60000 and "python" in value.lower(), max_tokens=64)
        end_memory = memory()
    finally:
        if model is not None:
            del model
        if processor is not None:
            del processor
        gc.collect()
        try:
            mx.clear_cache()
        except (NameError, RuntimeError):
            pass
        time.sleep(10)
        recovered = memory()
        image_path.unlink(missing_ok=True)

    required = {"INSTRUCTION", "CHINESE", "ENGLISH", "JSON", "TOOL_SCHEMA", "VISION", "CONTEXT_32K"}
    by_name = {item["name"]: item for item in metrics}
    unload_recovered = recovered["available_percent"] >= 60 and float(recovered["swap_used_gib"]) - float(start_memory["swap_used_gib"]) < 2.0
    main_qualified = bool(all(by_name.get(name, {}).get("ok") is True for name in required) and unload_recovered)
    report = {
        "model": "mlx-community/Qwen3.8-27B-8bit", "revision": REVISION, "snapshot": snapshot,
        "load_ok": load_seconds is not None, "load_seconds": round(load_seconds, 3) if load_seconds is not None else None,
        "start_memory": start_memory, "end_memory": end_memory, "recovered_memory": recovered,
        "unload_recovered": unload_recovered, "main_qualified": main_qualified, "tests": metrics,
    }
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(REPORT)
    print(REPORT)
    if not main_qualified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

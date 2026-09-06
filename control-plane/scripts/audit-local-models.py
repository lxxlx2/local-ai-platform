#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


HOME = Path.home()
LIVE_ROOT = Path("/Users/jerson/AI")
MODELS_ROOT = LIVE_ROOT / "models"
QUEUE_PATH = LIVE_ROOT / "config/model-download-queue-v0.1.json"
REGISTRY_PATH = LIVE_ROOT / "config/model-registry-v0.1.json"
QUAL_PATH = LIVE_ROOT / "config/qualification-evidence-v0.1.json"
MODELS_PY = LIVE_ROOT / "control-plane/src/local_ai_control/services/models.py"


def human_bytes(value: int) -> str:
    n = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024
        idx += 1
    return f"{n:.3f} {units[idx]}"


def safe_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def dir_stats(path: Path) -> dict[str, Any]:
    total = 0
    payload = 0
    partial = 0
    cache = 0

    files = 0
    safetensors = 0
    safetensor_bytes = 0
    gguf = 0
    gguf_bytes = 0

    config = False
    tokenizer = False
    marker = False
    incomplete_count = 0

    if not path.exists() or not path.is_dir():
        return {
            "exists": False,
            "total_bytes": 0,
            "payload_bytes": 0,
            "partial_bytes": 0,
            "cache_bytes": 0,
            "files": 0,
            "safetensors": 0,
            "safetensor_bytes": 0,
            "gguf": 0,
            "gguf_bytes": 0,
            "config": False,
            "tokenizer": False,
            "marker": False,
            "incomplete_count": 0,
        }

    for base, dirs, names in os.walk(path, followlinks=False):
        base_path = Path(base)

        # Never follow symlink dirs.
        dirs[:] = [
            d for d in dirs
            if not (base_path / d).is_symlink()
        ]

        try:
            rel_parts = base_path.relative_to(path).parts
        except Exception:
            rel_parts = ()

        in_cache = ".cache" in rel_parts

        for name in names:
            p = base_path / name

            try:
                if p.is_symlink() or not p.is_file():
                    continue
                size = p.stat().st_size
            except (OSError, FileNotFoundError):
                continue

            files += 1
            total += size

            if name == ".local-ai-download-complete.json":
                marker = True
                continue

            if name.endswith(".incomplete"):
                partial += size
                incomplete_count += 1
                continue

            if in_cache:
                cache += size
                continue

            payload += size

            low = name.lower()

            if low == "config.json":
                config = True

            if low in {
                "tokenizer.json",
                "tokenizer_config.json",
                "tokenizer.model",
            }:
                tokenizer = True

            if low.endswith(".safetensors"):
                safetensors += 1
                safetensor_bytes += size

            if low.endswith(".gguf"):
                gguf += 1
                gguf_bytes += size

    return {
        "exists": True,
        "total_bytes": total,
        "payload_bytes": payload,
        "partial_bytes": partial,
        "cache_bytes": cache,
        "files": files,
        "safetensors": safetensors,
        "safetensor_bytes": safetensor_bytes,
        "gguf": gguf,
        "gguf_bytes": gguf_bytes,
        "config": config,
        "tokenizer": tokenizer,
        "marker": marker,
        "incomplete_count": incomplete_count,
    }


def marker_valid(path: Path, spec: dict[str, Any], stats: dict[str, Any]) -> bool:
    marker_path = path / ".local-ai-download-complete.json"

    raw = safe_json(marker_path)
    if not isinstance(raw, dict):
        return False

    if raw.get("repo") != spec.get("repo"):
        return False

    if raw.get("revision") != spec.get("revision"):
        return False

    if raw.get("expected_bytes") != spec.get("expected_bytes"):
        return False

    manifest = raw.get("files")

    if not isinstance(manifest, list) or not manifest:
        return False

    for item in manifest:
        if not isinstance(item, dict):
            return False

        rel = item.get("path")
        size = item.get("size")

        if not isinstance(rel, str) or not isinstance(size, int):
            return False

        candidate = (path / rel).resolve()

        try:
            candidate.relative_to(path.resolve())
        except Exception:
            return False

        try:
            if not candidate.is_file() or candidate.stat().st_size != size:
                return False
        except OSError:
            return False

    expected = int(spec.get("expected_bytes", 0) or 0)

    if expected <= 0:
        return False

    return stats["payload_bytes"] >= expected * 0.98


def discover_model_dirs() -> list[Path]:
    found: set[Path] = set()

    if not MODELS_ROOT.exists():
        return []

    # Queue targets are always inventory candidates.
    queue = safe_json(QUEUE_PATH) or {}
    for spec in queue.get("models", []):
        try:
            found.add(Path(spec["local_dir"]))
        except Exception:
            pass

    # Paths explicitly registered in source code.
    if MODELS_PY.exists():
        text = MODELS_PY.read_text(errors="ignore")
        for match in re.findall(r'local_path="([^"]+)"', text):
            p = Path(match)
            if MODELS_ROOT == p or MODELS_ROOT in p.parents:
                found.add(p)

    # Discover model-shaped directories to catch old/manual downloads.
    for root, dirs, files in os.walk(MODELS_ROOT):
        p = Path(root)

        try:
            depth = len(p.relative_to(MODELS_ROOT).parts)
        except Exception:
            continue

        if depth > 3:
            dirs[:] = []
            continue

        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and not (p / d).is_symlink()
        ]

        names = set(files)

        model_signals = (
            "config.json" in names
            or "tokenizer_config.json" in names
            or ".local-ai-download-complete.json" in names
            or any(x.endswith(".safetensors") for x in names)
            or any(x.endswith(".gguf") for x in names)
        )

        if model_signals:
            found.add(p)

    # Also include substantial top-level directories because interrupted
    # downloads may not yet contain normal model metadata.
    for p in MODELS_ROOT.iterdir():
        if p.is_dir() and not p.is_symlink() and not p.name.startswith("."):
            found.add(p)

    return sorted(found, key=lambda x: str(x).lower())


def external_cache_inventory() -> list[dict[str, Any]]:
    roots = [
        HOME / ".cache/huggingface/hub",
        HOME / ".cache/modelscope/hub",
        HOME / ".ollama/models",
        HOME / ".lmstudio/models",
        HOME / "Library/Application Support/LM Studio/models",
    ]

    rows = []

    for root in roots:
        if not root.exists():
            continue

        stats = dir_stats(root)

        rows.append({
            "path": str(root),
            "stats": stats,
        })

        # Hugging Face cached repositories.
        if root.name == "hub" and "huggingface" in str(root):
            try:
                children = sorted(
                    p for p in root.iterdir()
                    if p.is_dir()
                    and p.name.startswith("models--")
                )
            except OSError:
                children = []

            for child in children:
                rows.append({
                    "path": str(child),
                    "stats": dir_stats(child),
                })

    return rows


def system_info() -> dict[str, Any]:
    result = {}

    try:
        mem = int(
            subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                text=True,
            ).strip()
        )
        result["memory_bytes"] = mem
    except Exception:
        result["memory_bytes"] = None

    try:
        result["swap"] = subprocess.check_output(
            ["sysctl", "-n", "vm.swapusage"],
            text=True,
        ).strip()
    except Exception:
        result["swap"] = None

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()

    queue_raw = safe_json(QUEUE_PATH) or {}
    registry_raw = safe_json(REGISTRY_PATH) or {}
    qualification_raw = safe_json(QUAL_PATH) or {}

    queue_by_path = {}

    for spec in queue_raw.get("models", []):
        try:
            queue_by_path[str(Path(spec["local_dir"]).resolve())] = spec
        except Exception:
            continue

    rows = []

    for path in discover_model_dirs():
        resolved = path.resolve()
        stats = dir_stats(resolved)

        spec = queue_by_path.get(str(resolved))
        expected = int(spec.get("expected_bytes", 0)) if spec else 0

        pct = (
            stats["payload_bytes"] / expected * 100
            if expected > 0
            else None
        )

        marker_ok = (
            marker_valid(resolved, spec, stats)
            if spec
            else False
        )

        if spec and marker_ok:
            state = "QUEUE_COMPLETE_VALIDATED"
        elif spec and stats["payload_bytes"] > 0:
            state = "QUEUE_PARTIAL_OR_UNVERIFIED"
        elif spec:
            state = "QUEUE_TARGET_EMPTY_OR_MISSING"
        elif stats["payload_bytes"] > 0:
            state = "FILES_PRESENT_NOT_QUEUE_VERIFIED"
        else:
            state = "EMPTY_OR_CACHE_ONLY"

        rows.append({
            "path": str(resolved),
            "name": resolved.name,
            "queue_id": spec.get("id") if spec else None,
            "queue_role": spec.get("role") if spec else None,
            "queue_repo": spec.get("repo") if spec else None,
            "expected_bytes": expected or None,
            "payload_percent": round(pct, 2) if pct is not None else None,
            "marker_valid": marker_ok,
            "state": state,
            "stats": stats,
        })

    external = external_cache_inventory()

    payload = {
        "schema_version": "0.1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": system_info(),
        "models_root": str(MODELS_ROOT),
        "models": rows,
        "external_caches": external,
        "production_aliases": registry_raw.get("production_aliases", {}),
        "qualification_records": qualification_raw.get("records", []),
        "safety": {
            "read_only": True,
            "models_started": False,
            "downloads_started": False,
            "files_deleted": False,
            "port_8199_touched": False,
        },
    }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lines = []

    lines += [
        "# Local Model Inventory",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "This is a live disk audit of the local model inventory on the 48 GB Mac.",
        "It is intentionally read-only. No model was started, stopped, downloaded, or deleted.",
        "Port 8199 was not accessed or modified.",
        "",
        "## Summary",
        "",
    ]

    queue_complete = sum(
        x["state"] == "QUEUE_COMPLETE_VALIDATED"
        for x in rows
    )
    queue_partial = sum(
        x["state"] == "QUEUE_PARTIAL_OR_UNVERIFIED"
        for x in rows
    )
    unmanaged = sum(
        x["state"] == "FILES_PRESENT_NOT_QUEUE_VERIFIED"
        for x in rows
    )

    total_payload = sum(
        x["stats"]["payload_bytes"]
        for x in rows
    )

    total_partial = sum(
        x["stats"]["partial_bytes"]
        for x in rows
    )

    lines += [
        f"- Inventory candidates: **{len(rows)}**",
        f"- Queue-complete and marker-validated: **{queue_complete}**",
        f"- Queue partial/unverified: **{queue_partial}**",
        f"- Files present outside queue verification: **{unmanaged}**",
        f"- Total discovered payload: **{human_bytes(total_payload)}**",
        f"- Total `.incomplete` bytes inside discovered model directories: **{human_bytes(total_partial)}**",
        "",
        "## Local `/Users/jerson/AI/models` inventory",
        "",
        "| Model / directory | Queue role | State | Payload | Expected | Progress | Partial cache | Weights | Marker |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]

    for row in rows:
        st = row["stats"]

        expected = (
            human_bytes(row["expected_bytes"])
            if row["expected_bytes"]
            else "—"
        )

        progress = (
            f"{row['payload_percent']:.2f}%"
            if row["payload_percent"] is not None
            else "—"
        )

        weights = []

        if st["safetensors"]:
            weights.append(
                f"{st['safetensors']} safetensors / {human_bytes(st['safetensor_bytes'])}"
            )

        if st["gguf"]:
            weights.append(
                f"{st['gguf']} GGUF / {human_bytes(st['gguf_bytes'])}"
            )

        if not weights:
            weights.append("—")

        role = row["queue_role"] or "unmanaged"

        marker = (
            "VALID"
            if row["marker_valid"]
            else ("present/unverified" if st["marker"] else "no")
        )

        lines.append(
            "| "
            + " | ".join([
                f"`{row['path']}`",
                role,
                row["state"],
                human_bytes(st["payload_bytes"]),
                expected,
                progress,
                human_bytes(st["partial_bytes"]),
                ", ".join(weights),
                marker,
            ])
            + " |"
        )

    lines += [
        "",
        "## Queue targets",
        "",
        "| ID | Role | Repository | Local path | Expected |",
        "|---|---|---|---|---:|",
    ]

    for spec in queue_raw.get("models", []):
        lines.append(
            f"| `{spec.get('id')}` | `{spec.get('role')}` | "
            f"`{spec.get('repo')}` | `{spec.get('local_dir')}` | "
            f"{human_bytes(int(spec.get('expected_bytes', 0)))} |"
        )

    lines += [
        "",
        "## Production registry state",
        "",
        "| Role | Profile | Registry status | Max context |",
        "|---|---|---|---:|",
    ]

    aliases = registry_raw.get("production_aliases", {})

    for role, item in aliases.items():
        lines.append(
            f"| `{role}` | `{item.get('profile')}` | "
            f"`{item.get('status')}` | "
            f"{item.get('max_context_tokens', '—')} |"
        )

    lines += [
        "",
        "## Recorded workload qualification evidence",
        "",
        "| Profile | Model | Workload | Status | Reason | Recorded date |",
        "|---|---|---|---|---|---|",
    ]

    records = qualification_raw.get("records", [])

    if records:
        for item in records:
            lines.append(
                f"| `{item.get('profile_id')}` | "
                f"`{item.get('model_id')}` | "
                f"`{item.get('workload_class')}` | "
                f"`{item.get('status')}` | "
                f"`{item.get('reason')}` | "
                f"`{item.get('recorded_date')}` |"
            )
    else:
        lines.append("| — | — | — | — | — | — |")

    lines += [
        "",
        "## Other common local model caches",
        "",
    ]

    if external:
        lines += [
            "| Path | Total | Payload | Partial |",
            "|---|---:|---:|---:|",
        ]

        for item in external:
            st = item["stats"]
            lines.append(
                f"| `{item['path']}` | "
                f"{human_bytes(st['total_bytes'])} | "
                f"{human_bytes(st['payload_bytes'])} | "
                f"{human_bytes(st['partial_bytes'])} |"
            )
    else:
        lines.append("No common external model cache roots were found.")

    lines += [
        "",
        "## Interpretation rules",
        "",
        "- `QUEUE_COMPLETE_VALIDATED` means the configured completion marker matches the pinned repository/revision/expected size and the recorded payload files are present.",
        "- `QUEUE_PARTIAL_OR_UNVERIFIED` means files exist but the configured queue completion proof is not currently valid.",
        "- `FILES_PRESENT_NOT_QUEUE_VERIFIED` means model-like files are on disk but this audit cannot claim download completeness or runtime qualification.",
        "- Download completeness does not imply runtime qualification.",
        "- Registry status and workload qualification evidence are listed separately from disk presence.",
        "",
        "## Safety",
        "",
        "- `PORT_8199_TOUCHED=false`",
        "- `MODELS_STARTED=false`",
        "- `DOWNLOADS_STARTED=false`",
        "- `FILES_DELETED=false`",
        "",
    ]

    args.markdown.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(json.dumps({
        "inventory_candidates": len(rows),
        "queue_complete_validated": queue_complete,
        "queue_partial_or_unverified": queue_partial,
        "files_present_not_queue_verified": unmanaged,
        "total_payload_gib": round(total_payload / 1024**3, 3),
        "total_partial_gib": round(total_partial / 1024**3, 3),
        "markdown": str(args.markdown),
        "json": str(args.json),
    }, indent=2))


if __name__ == "__main__":
    main()

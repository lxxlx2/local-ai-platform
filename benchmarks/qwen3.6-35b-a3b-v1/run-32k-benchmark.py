#!/usr/bin/env python3
"""One-shot 32K local context benchmark; no server/model configuration changes."""
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = "/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v3".replace("v3", "v1")
MODEL_PATH = "/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit"
PID = 58578
OUTCOME = os.path.join(ROOT, "context-32k-raw.json")
SAMPLES = os.path.join(ROOT, "memory-sampler-32k.jsonl")


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "header"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header"} and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "local-benchmark/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        parser = TextOnly(); parser.feed(response.read().decode("utf-8", "ignore"))
    return "\n".join(" ".join(part.split()) for part in parser.parts if part.strip())


def sample_once():
    return subprocess.check_output([
        "/usr/bin/python3", os.path.join(ROOT, "memory-sampler.py"),
        "--pid", str(PID), "--seconds", "0"
    ], text=True).strip()


def sampler(stop):
    with open(SAMPLES, "w", encoding="utf-8") as handle:
        while not stop.is_set():
            try:
                handle.write(sample_once() + "\n"); handle.flush()
            except Exception as exc:
                handle.write(json.dumps({"sampler_error": str(exc)}) + "\n"); handle.flush()
            stop.wait(1)
        try:
            handle.write(sample_once() + "\n"); handle.flush()
        except Exception:
            pass


def main():
    # Public, non-sensitive Chinese technical documentation, plus preserved code blocks.
    urls = [
        "https://docs.python.org/zh-cn/3/tutorial/introduction.html",
        "https://docs.python.org/zh-cn/3/tutorial/controlflow.html",
        "https://docs.python.org/zh-cn/3/tutorial/datastructures.html",
        "https://docs.python.org/zh-cn/3/tutorial/modules.html",
        "https://docs.python.org/zh-cn/3/tutorial/classes.html",
        "https://docs.python.org/zh-cn/3/tutorial/inputoutput.html",
        "https://docs.python.org/zh-cn/3/tutorial/errors.html",
    ]
    source = "\n\n".join(fetch(url) for url in urls)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=True)
    ids = tokenizer.encode(source, add_special_tokens=False)
    # 29,650 source tokens, then ten distinct facts and instructions: target total 30–32K.
    source = tokenizer.decode(ids[:29650], skip_special_tokens=True)
    facts = [
        "F1：北斗迁移批次的不可替代校验码为“CEDAR-041”。",
        "F2：文档镜像的签名轮换日固定为每月第 11 日。",
        "F3：灰鲸流水线的最小人工复核人数是 3 人。",
        "F4：南窗数据集的保留期限是 270 天。",
        "F5：青栎项目的回退版本标识为 ROLLBACK-8。",
        "F6：云港发布清单必须由值班负责人和审阅负责人共同签署。",
        "F7：银杉审计的告警阈值为连续 4 次失败。",
        "F8：赤道归档包的加密轮换周期为 45 天。",
        "F9：晨星索引的默认排序键是 `updated_at` 降序。",
        "F10：远帆验收报告的最终编号为 FINAL-203。",
    ]
    chunks = []
    positions = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    cursor = 0
    for fact, fraction in zip(facts, positions):
        target = int(len(source) * fraction)
        chunks.append(source[cursor:target])
        chunks.append("\n\n【基准事实记录】" + fact + "\n\n")
        cursor = target
    chunks.append(source[cursor:])
    context = "".join(chunks)
    prompt = """以下是公开中文技术文档和嵌入的十条基准事实。请把它当作唯一依据，不使用外部知识。\n\n任务：\n1. 写一份至少 1000 个中文 token 的结构化技术学习摘要，覆盖文档中的核心 Python 概念、代码组织、错误处理和输入输出实践；避免重复。\n2. 单独列出 F1 至 F10 的原文事实，保持所有编号、数值、日期、名称和代码完全准确。\n3. 回答两个跨段关系问题：(a) 若南窗数据集从开始保存到归档包第一次加密轮换，两个期限相差多少天？(b) 需要共同签署的发布清单涉及哪两个角色，并说明银杉告警会在第几次连续失败触发？\n\n上下文开始：\n""" + context
    prompt_tokens_local = len(tokenizer.encode(prompt, add_special_tokens=False))
    payload = {"model": "Qwen3.6-35B-A3B-4bit", "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1500, "temperature": 0, "stream": True,
               "stream_options": {"include_usage": True},
               "chat_template_kwargs": {"enable_thinking": False}}
    stop = threading.Event(); thread = threading.Thread(target=sampler, args=(stop,), daemon=True); thread.start()
    start = time.monotonic(); first = None; content = []; usage = None; error = None
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1200) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: ") or line == "data: [DONE]": continue
                event = json.loads(line[6:])
                if event.get("usage"): usage = event["usage"]
                choices = event.get("choices") or []
                if choices:
                    piece = choices[0].get("delta", {}).get("content")
                    if piece:
                        if first is None: first = time.monotonic()
                        content.append(piece)
    except Exception as exc:
        error = repr(exc)
    finally:
        end = time.monotonic(); stop.set(); thread.join(timeout=10)
    result = {"timestamp": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
              "source_urls": urls, "local_prompt_tokens": prompt_tokens_local, "usage": usage,
              "ttft_seconds": round((first - start), 3) if first else None,
              "total_time_seconds": round(end - start, 3), "api_error": error,
              "output": "".join(content), "output_complete": error is None}
    with open(OUTCOME, "w", encoding="utf-8") as handle: json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: result[k] for k in result if k != "output"}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()

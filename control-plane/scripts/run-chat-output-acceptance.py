#!/usr/bin/env python3
"""Self-acceptance harness: production chat/provider/renderer with capture transport.

The generated runtime report contains metrics only; it never writes prompts, answers, tokens, or credentials.
"""
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from local_ai_control.domain.identity import identity_from_telegram
from local_ai_control.services.chat import ChatService
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.output import CaptureTelegramTransport, TelegramOutputRenderer
from local_ai_control.services.storage import ScopedSQLiteRepository


ROOT = Path("/Users/jerson/AI")
REPORT = ROOT / "runtime/acceptance/chat-output-v0.2.json"


def terminal(text):
    stripped = text.rstrip()
    return bool(stripped) and (stripped[-1] in "。！？!?）)]』”" or stripped.endswith("```"))


def run_case(name, repo, identity, prompt, checks):
    session = repo.create_session(identity, name)
    started = time.monotonic()
    result = ChatService(repo, OmlxProvider()).reply(identity, session, prompt)
    duration = time.monotonic() - started
    renderer = TelegramOutputRenderer()
    package = renderer.package(result.text)
    capture = CaptureTelegramTransport()
    for index, chunk in enumerate(package.chunks, 1):
        capture.send(chunk, index, len(package.chunks), parse_mode=None)
    reconstructed = capture.reconstructed()
    status = result.complete and reconstructed == package.canonical_text and all(item["parse_mode"] is None for item in capture.messages)
    status = status and all(check(package.canonical_text) for check in checks)
    return {
        "test_name": name,
        "status": "PASS" if status else "FAIL",
        "duration_seconds": round(duration, 3),
        "char_count": len(package.canonical_text),
        "output_tokens": result.output_tokens,
        "finish_reason": result.finish_reason,
        "requested_max_output_tokens": result.requested_max_output_tokens,
        "chunk_count": len(package.chunks),
        "reconstructed_char_count": len(reconstructed),
        "error_category": None if status else "acceptance_assertion",
    }


def main():
    with tempfile.TemporaryDirectory(prefix="local-ai-chat-acceptance-") as directory:
        repo = ScopedSQLiteRepository(Path(directory) / "public.db", "public")
        repo.migrate()
        identity = identity_from_telegram(900001, "1")
        renderer = TelegramOutputRenderer()
        no_markdown = lambda text: not renderer.has_visible_markdown_artifacts(text)
        records = [
            run_case("REAL_QWEN_TEST_A", repo, identity, "你好，简单介绍一下你现在能帮我做什么。", [no_markdown, terminal, lambda text: len(text) <= 1600]),
            run_case("REAL_QWEN_TEST_B", repo, identity, "请详细解释Python中的装饰器，并给出示例。", [no_markdown, terminal]),
            run_case("REAL_QWEN_TEST_C", repo, identity, '解释下面代码中的星号是什么意思，并在回答中原样保留该代码：\n\nresult = "**test**"', [lambda text: "**test**" in text, no_markdown, terminal]),
        ]
        session = repo.create_session(identity, "REAL_QWEN_TEST_D")
        first = ChatService(repo, OmlxProvider()).reply(identity, session, "你好，简单介绍一下你现在能帮我做什么。")
        second = ChatService(repo, OmlxProvider()).reply(identity, session, "我刚才问了你什么？")
        records.append({
            "test_name": "REAL_QWEN_TEST_D",
            "status": "PASS" if first.complete and second.complete and any(term in second.text for term in ("简单介绍", "能帮我", "做什么")) else "FAIL",
            "duration_seconds": None,
            "char_count": len(second.text),
            "output_tokens": second.output_tokens,
            "finish_reason": second.finish_reason,
            "requested_max_output_tokens": second.requested_max_output_tokens,
            "chunk_count": len(TelegramOutputRenderer().package(second.text).chunks),
            "error_category": None,
        })
        repo.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "records": records, "overall": "PASS" if all(record["status"] == "PASS" for record in records) else "FAIL"}
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print("overall=" + payload["overall"])
    for record in records:
        print(f"{record['test_name']}={record['status']} chars={record['char_count']} tokens={record['output_tokens']} chunks={record['chunk_count']} finish={record['finish_reason']}")


if __name__ == "__main__":
    main()

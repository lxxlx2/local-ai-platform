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
from local_ai_control.services.code_quality import GOLDEN_CODE_002_SOURCE, CodeValidationLevel, GoldenFixtureSandboxedCodeValidator, check_python_block, python_blocks
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.output import CaptureTelegramTransport, TelegramOutputRenderer, is_safe_telegram_html
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
        capture.send(chunk, index, len(package.chunks), parse_mode=package.parse_mode)
    reconstructed = capture.reconstructed()
    artifact_free = not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
    html_safe = all(is_safe_telegram_html(item["text"]) for item in capture.messages)
    status = result.complete and reconstructed == package.canonical_text and all(item["parse_mode"] == "HTML" for item in capture.messages) and html_safe
    status = status and artifact_free and all(check(package.canonical_text) for check in checks)
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
        "content_loss": len(package.canonical_text) - len(reconstructed),
        "telegram_parse_mode": package.parse_mode,
        "html_safe": html_safe,
        "artifact_check": "PASS" if artifact_free else "FAIL",
        "protected_range_count": len(package.protected_ranges),
        "error_category": None if status else "acceptance_assertion",
    }


def run_python_case(repo, identity):
    prompt = "请详细解释 Python 中的装饰器，包括它是什么、为什么使用、工作原理，并给出两个完整、可独立运行的代码示例。每个示例都必须包含自己的 import。"
    session = repo.create_session(identity, "REAL_QWEN_PYTHON_COMPLETE")
    started = time.monotonic()
    result = ChatService(repo, OmlxProvider()).reply(identity, session, prompt)
    renderer = TelegramOutputRenderer()
    package = renderer.package(result.text)
    capture = CaptureTelegramTransport()
    for index, chunk in enumerate(package.chunks, 1):
        capture.send(chunk, index, len(package.chunks), parse_mode=package.parse_mode)
    reconstructed = capture.reconstructed()
    artifact_free = not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
    html_safe = all(is_safe_telegram_html(message["text"]) for message in capture.messages)
    raw = repo.recent_messages(identity, session)[-1]["content"] if result.complete else ""
    checks = [check_python_block(block) for block in python_blocks(raw)]
    valid = (
        result.complete
        and len(checks) >= 2
        and all(check.syntax_valid and check.standalone_claim_ok for check in checks)
        and artifact_free
        and reconstructed == package.canonical_text
        and all(message["parse_mode"] == "HTML" for message in capture.messages)
        and html_safe
    )
    return {
        "test_name": "REAL_QWEN_PYTHON_COMPLETE",
        "status": "PASS" if valid else "FAIL",
        "duration_seconds": round(time.monotonic() - started, 3),
        "char_count": len(package.canonical_text),
        "output_tokens": result.output_tokens,
        "finish_reason": result.finish_reason,
        "requested_max_output_tokens": result.requested_max_output_tokens,
        "chunk_count": len(package.chunks),
        "python_block_count": len(checks),
        "artifact_check": "PASS" if artifact_free else "FAIL",
        "reconstructed_char_count": len(reconstructed),
        "content_loss": len(package.canonical_text) - len(reconstructed),
        "telegram_parse_mode": package.parse_mode,
        "html_safe": html_safe,
        "error_category": None if valid else "python_static_validation",
    }


def run_golden_code_case():
    started = time.monotonic()
    result = GoldenFixtureSandboxedCodeValidator().validate_python(GOLDEN_CODE_002_SOURCE, "GOLDEN-CODE-002")
    return {
        "test_name": "GOLDEN-CODE-002",
        "status": "PASS" if result.level is CodeValidationLevel.SANDBOX_EXECUTION_VALIDATED and result.stdout_marker_seen else "FAIL",
        "duration_seconds": round(time.monotonic() - started, 3),
        "validation_level": result.level.value,
        "peak_rss_kib": result.peak_rss_kib,
        "error_category": result.issue,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="local-ai-chat-acceptance-") as directory:
        repo = ScopedSQLiteRepository(Path(directory) / "public.db", "public")
        repo.migrate()
        identity = identity_from_telegram(900001, "1")
        renderer = TelegramOutputRenderer()
        records = [
            run_case("REAL_QWEN_TEST_A", repo, identity, "你好，简单介绍一下你现在能帮我做什么。", [lambda text: len(text) <= 1600]),
            run_case("REAL_QWEN_TEST_B", repo, identity, "请详细解释Python中的装饰器，并给出示例。", [lambda text: "```" not in text and "〔代码：" not in text and "‹" not in text]),
            run_case("REAL_QWEN_TEST_C", repo, identity, '解释下面代码中的星号是什么意思，并在回答中原样保留该代码：\n\nresult = "**test**"', [lambda text: 'result = "**test**"' in text, lambda text: "〔代码：" not in text and "‹" not in text]),
        ]
        records.append(run_python_case(repo, identity))
        records.append(run_golden_code_case())
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
        print(
            f"{record['test_name']}={record['status']} chars={record.get('char_count')} "
            f"tokens={record.get('output_tokens')} chunks={record.get('chunk_count')} "
            f"finish={record.get('finish_reason')} validation={record.get('validation_level')}"
        )


if __name__ == "__main__":
    main()

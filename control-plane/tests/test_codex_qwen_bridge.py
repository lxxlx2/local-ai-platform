import json
import threading
import urllib.request
from dataclasses import dataclass
from http.server import HTTPServer

import pytest

from local_ai_control.services.codex_qwen_bridge import (
    BridgeProtocolError,
    CodexQwenBridge,
    compact_codex_request,
    handler_for,
    parse_model_action,
)


@dataclass(frozen=True)
class Reply:
    text: str
    complete: bool = True


class FakeBackend:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def health(self):
        return {"status": "healthy"}

    def generate(self, prompt, max_output_tokens=1024):
        self.prompts.append((prompt, max_output_tokens))
        return Reply(self.text)


def request_payload():
    return {
        "model": "mlx-community/Qwen3.8-27B-8bit",
        "stream": True,
        "instructions": "bootstrap-" + ("x" * 20000),
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "run command",
            }
        ],
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "fix the parser bug"}],
            },
            {
                "type": "function_call",
                "call_id": "call_old",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"pytest -q\"}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_old",
                "output": "1 failed, 8 passed",
            },
        ],
    }


def parse_sse(body):
    events = []
    for block in body.decode().strip().split("\n\n"):
        lines = block.splitlines()
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")
        events.append(json.loads(lines[1][6:]))
    return events


def test_compaction_omits_bootstrap_and_keeps_objective_and_tool_result():
    payload = request_payload()
    prompt = compact_codex_request(payload)
    assert "bootstrap-" not in prompt
    assert "fix the parser bug" in prompt
    assert "pytest -q" in prompt
    assert "1 failed, 8 passed" in prompt
    assert len(prompt.encode()) <= 48 * 1024


def test_compaction_requires_exec_command():
    payload = request_payload()
    payload["tools"] = []
    with pytest.raises(BridgeProtocolError, match="exec_command"):
        compact_codex_request(payload)


@pytest.mark.parametrize(
    "text",
    [
        "before <EXEC>pytest</EXEC>",
        "<EXEC>pytest</EXEC> after",
        "<EXEC></EXEC>",
        "<EXEC>pytest</EXEC><FINAL>done</FINAL>",
        "```<EXEC>pytest</EXEC>```",
    ],
)
def test_model_envelope_fails_closed(text):
    with pytest.raises(BridgeProtocolError):
        parse_model_action(text)


def test_exec_event_is_host_serialized_function_call():
    bridge = CodexQwenBridge(FakeBackend("<EXEC>pytest -q</EXEC>"))
    events = parse_sse(bridge.respond(request_payload()))
    assert [event["type"] for event in events] == [
        "response.created",
        "response.output_item.done",
        "response.completed",
    ]
    item = events[1]["item"]
    assert item["type"] == "function_call"
    assert item["name"] == "exec_command"
    assert item["call_id"].startswith("call_")
    assert json.loads(item["arguments"]) == {"cmd": "pytest -q"}


def test_final_event_is_assistant_message():
    bridge = CodexQwenBridge(FakeBackend("<FINAL>all tests pass</FINAL>"))
    events = parse_sse(bridge.respond(request_payload()))
    item = events[1]["item"]
    assert item["type"] == "message"
    assert item["role"] == "assistant"
    assert item["content"] == [{"type": "output_text", "text": "all tests pass"}]


def test_http_fake_backend_integration():
    bridge = CodexQwenBridge(FakeBackend("<FINAL>done</FINAL>"))
    server = HTTPServer(("127.0.0.1", 0), handler_for(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps(request_payload()).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/responses",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/event-stream"
            events = parse_sse(response.read())
        assert events[-1]["type"] == "response.completed"
    finally:
        server.shutdown()
        thread.join(timeout=5)

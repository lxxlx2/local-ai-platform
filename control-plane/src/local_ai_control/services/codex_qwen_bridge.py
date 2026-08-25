from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import argparse
import json
import re
import uuid
from typing import Any, Protocol

from local_ai_control.services.models import QWEN38
from local_ai_control.services.qwen38_runtime import Qwen38Provider


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_COMPACT_PROMPT_BYTES = 48 * 1024
MAX_TEXT_CHARS = 8000
MAX_TOOL_OUTPUT_CHARS = 12000
RECENT_ITEMS = 12
EXEC_TOOL = "exec_command"


class BridgeProtocolError(ValueError):
    pass


class BackendUnavailable(RuntimeError):
    pass


class TextBackend(Protocol):
    def generate(self, prompt: str, max_output_tokens: int = 1024): ...


@dataclass(frozen=True)
class ModelAction:
    kind: str
    content: str


def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    if len(value) <= limit:
        return value
    return value[-limit:]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"input_text", "output_text", "text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _compact_item(item: Any) -> str | None:
    if isinstance(item, str):
        return "user: " + _bounded_text(item, MAX_TEXT_CHARS)
    if not isinstance(item, dict):
        return None

    item_type = item.get("type")
    role = item.get("role")
    if role in {"user", "assistant", "system", "developer"}:
        text = _content_text(item.get("content"))
        if text:
            return f"{role}: {_bounded_text(text, MAX_TEXT_CHARS)}"

    if item_type == "message":
        text = _content_text(item.get("content"))
        if text:
            return f"{role or 'message'}: {_bounded_text(text, MAX_TEXT_CHARS)}"

    if item_type == "function_call":
        name = item.get("name")
        call_id = item.get("call_id")
        arguments = item.get("arguments")
        if isinstance(name, str) and isinstance(arguments, str):
            return (
                f"tool_call name={name} call_id={call_id or ''}\n"
                f"arguments={_bounded_text(arguments, MAX_TEXT_CHARS)}"
            )

    if item_type == "function_call_output":
        call_id = item.get("call_id")
        output = item.get("output")
        if isinstance(output, str):
            return (
                f"tool_result call_id={call_id or ''}\n"
                f"{_bounded_text(output, MAX_TOOL_OUTPUT_CHARS)}"
            )
    return None


def _latest_user_objective(items: list[Any]) -> str:
    for item in reversed(items):
        if isinstance(item, str) and item.strip():
            return _bounded_text(item.strip(), MAX_TEXT_CHARS)
        if isinstance(item, dict) and item.get("role") == "user":
            text = _content_text(item.get("content")).strip()
            if text:
                return _bounded_text(text, MAX_TEXT_CHARS)
    return "(continue the coding task using the recent tool results)"


def _tool_names(payload: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return ()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if isinstance(name, str):
            names.append(name)
            continue
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(function["name"])
    return tuple(names)


def compact_codex_request(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise BridgeProtocolError("request must be an object")
    if payload.get("stream") is not True:
        raise BridgeProtocolError("stream=true is required")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise BridgeProtocolError("model is required")

    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        items: list[Any] = [raw_input]
    elif isinstance(raw_input, list):
        items = raw_input
    else:
        raise BridgeProtocolError("input must be a string or list")

    available_tools = _tool_names(payload)
    if EXEC_TOOL not in available_tools:
        raise BridgeProtocolError("exec_command tool is required")

    recent: list[str] = []
    for item in items[-RECENT_ITEMS:]:
        compact = _compact_item(item)
        if compact:
            recent.append(compact)

    objective = _latest_user_objective(items)
    transcript = "\n\n".join(recent)
    prefix = f"""You are Qwen3.8 acting as the local coding planner behind Codex CLI.
Codex is the only execution shell. You do not execute commands yourself.
Your only allowed action tool is {EXEC_TOOL}.
Choose exactly one action for this turn.

To run one shell command, output exactly:
<EXEC>raw shell command</EXEC>

To finish and report to the user, output exactly:
<FINAL>final concise result</FINAL>

No Markdown fences. No surrounding prose. Never emit JSON for a tool call.
Do not claim a command ran unless a tool result below proves it.
Prefer inspection and tests before edits. Keep commands scoped to the approved Codex workspace.
Never request commit, push, merge, deploy, sudo, launchctl, process termination, credential access, or network access.

CURRENT OBJECTIVE:
{objective}

RECENT CODEX CONTEXT:
"""
    prompt = prefix + (transcript or "(none)")
    encoded = prompt.encode("utf-8")
    if len(encoded) > MAX_COMPACT_PROMPT_BYTES:
        overflow = len(encoded) - MAX_COMPACT_PROMPT_BYTES
        trim_chars = min(len(transcript), overflow + 4096)
        transcript = transcript[trim_chars:]
        prompt = prefix + transcript
    if len(prompt.encode("utf-8")) > MAX_COMPACT_PROMPT_BYTES:
        raise BridgeProtocolError("compacted request exceeds safe prompt budget")
    return prompt


_EXEC_RE = re.compile(r"\A<EXEC>([\s\S]+)</EXEC>\Z")
_FINAL_RE = re.compile(r"\A<FINAL>([\s\S]+)</FINAL>\Z")


def parse_model_action(text: str) -> ModelAction:
    if not isinstance(text, str):
        raise BridgeProtocolError("model output must be text")
    match = _EXEC_RE.fullmatch(text)
    if match:
        command = match.group(1).strip()
        if not command:
            raise BridgeProtocolError("empty exec command")
        if "<EXEC>" in command or "<FINAL>" in command:
            raise BridgeProtocolError("nested action envelope")
        return ModelAction("EXEC", command)
    match = _FINAL_RE.fullmatch(text)
    if match:
        final = match.group(1).strip()
        if not final:
            raise BridgeProtocolError("empty final response")
        if "<EXEC>" in final or "<FINAL>" in final:
            raise BridgeProtocolError("nested action envelope")
        return ModelAction("FINAL", final)
    raise BridgeProtocolError("malformed model action envelope")


def _usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "input_tokens_details": None,
        "output_tokens": 0,
        "output_tokens_details": None,
        "total_tokens": 0,
    }


def _events_for_action(action: ModelAction) -> list[dict[str, Any]]:
    response_id = "resp_" + uuid.uuid4().hex
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": response_id}}
    ]
    if action.kind == "EXEC":
        item = {
            "type": "function_call",
            "call_id": "call_" + uuid.uuid4().hex,
            "name": EXEC_TOOL,
            "arguments": json.dumps(
                {"cmd": action.content}, ensure_ascii=False, separators=(",", ":")
            ),
        }
    else:
        item = {
            "type": "message",
            "role": "assistant",
            "id": "msg_" + uuid.uuid4().hex,
            "content": [{"type": "output_text", "text": action.content}],
        }
    events.append({"type": "response.output_item.done", "item": item})
    events.append(
        {
            "type": "response.completed",
            "response": {"id": response_id, "usage": _usage()},
        }
    )
    return events


def encode_sse(events: list[dict[str, Any]]) -> bytes:
    chunks: list[str] = []
    for event in events:
        event_type = event["type"]
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        chunks.append(f"event: {event_type}\ndata: {payload}\n\n")
    return "".join(chunks).encode("utf-8")


class CodexQwenBridge:
    def __init__(self, backend: TextBackend | None = None):
        self.backend = backend or Qwen38Provider()

    def health(self) -> dict[str, Any]:
        health = (
            self.backend.health()
            if hasattr(self.backend, "health")
            else {"status": "injected"}
        )
        return {
            "status": "healthy",
            "backend": QWEN38.model_id,
            "backend_health": health,
            "tool": EXEC_TOOL,
        }

    def respond(self, payload: dict[str, Any]) -> bytes:
        prompt = compact_codex_request(payload)
        try:
            reply = self.backend.generate(prompt, max_output_tokens=1024)
        except Exception as error:
            raise BackendUnavailable(type(error).__name__) from error
        text = getattr(reply, "text", None)
        complete = getattr(reply, "complete", True)
        if complete is False:
            raise BackendUnavailable("incomplete backend response")
        action = parse_model_action(text)
        return encode_sse(_events_for_action(action))


def handler_for(bridge: CodexQwenBridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexQwenBridge/0.1"

        def log_message(self, format, *args):
            return

        def _json(self, status: int, payload: dict[str, Any]):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                self._json(404, {"error": "not_found"})
                return
            try:
                self._json(200, bridge.health())
            except Exception as error:
                self._json(
                    503,
                    {"error": "backend_unavailable", "category": type(error).__name__},
                )

        def do_POST(self):
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._json(403, {"error": "localhost_only"})
                return
            if self.path != "/v1/responses":
                self._json(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    raise BridgeProtocolError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                body = bridge.respond(payload)
            except (BridgeProtocolError, json.JSONDecodeError, TypeError, ValueError) as error:
                self._json(
                    400, {"error": "invalid_request", "category": type(error).__name__}
                )
                return
            except BackendUnavailable as error:
                self._json(
                    503, {"error": "backend_unavailable", "category": str(error)}
                )
                return
            except Exception as error:
                self._json(
                    503, {"error": "bridge_error", "category": type(error).__name__}
                )
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    backend: TextBackend | None = None,
):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("bridge must bind to loopback")
    server = HTTPServer((host, int(port)), handler_for(CodexQwenBridge(backend)))
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local Codex Responses bridge backed by Qwen3.8"
    )
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

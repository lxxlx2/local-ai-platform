import json
import re
from dataclasses import dataclass


TELEGRAM_SAFE_CHUNK_SIZE = 3600


@dataclass(frozen=True)
class RenderedOutput:
    canonical_text: str
    chunks: tuple[str, ...]


class TelegramOutputRenderer:
    """Shared safe plain-text renderer. It never sends model HTML to Telegram."""
    def render(self, text: str) -> str:
        protected, values = self._protect_literals(text)
        lines = []
        for line in protected.splitlines(keepends=True):
            line = re.sub(r"^(\s{0,3})#{1,6}\s+", r"\1", line)
            line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
            line = re.sub(r"__([^_\n]+)__", r"\1", line)
            lines.append(line)
        return self._restore("".join(lines), values).strip()

    def package(self, text: str) -> RenderedOutput:
        canonical = self.render(text)
        return RenderedOutput(canonical, tuple(chunk_text(canonical)))

    def has_visible_markdown_artifacts(self, rendered_text: str) -> bool:
        for line in rendered_text.splitlines():
            if line.startswith("    ") or line.startswith("代码：") or line.startswith("代码（") or re.match(r"\s*[A-Za-z_][\w.\[\]]*\s*=", line):
                continue
            line = re.sub(r"〔代码：[\s\S]*?〕", "", line)
            try:
                if line.strip().startswith(("{", "[")) and json.loads(line.strip()) is not None:
                    continue
            except (ValueError, json.JSONDecodeError):
                pass
            if re.search(r"^\s*#{1,6}\s+|\*\*[^*\n]+\*\*|__[^_\n]+__|```", line):
                return True
        return False

    def _protect_literals(self, text: str):
        values = []

        def hold(value):
            values.append(value)
            return f"\uE000{len(values)-1}\uE001"

        def code_block(match):
            language = match.group(1).strip()
            source = match.group(2).strip("\n")
            label = f"代码（{language}）：" if language else "代码："
            body = "\n".join("    " + line for line in source.splitlines())
            return hold(label + "\n" + body + "\n")

        text = re.sub(r"```([^\n`]*)\n([\s\S]*?)```", code_block, text)
        text = re.sub(r"`([^`\n]+)`", lambda match: hold("〔代码：" + match.group(1) + "〕"), text)
        preserved = []
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if line.startswith("    "):
                preserved.append(hold(line))
                continue
            try:
                if stripped.startswith(("{", "[")) and json.loads(stripped) is not None:
                    preserved.append(hold(line))
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            if re.match(r"\s*(?:\$\s+|echo\b|cd\b|ls\b|python\b|git\b|grep\b|[A-Za-z_][\w.\[\]]*\s*=)", line):
                preserved.append(hold(line))
            else:
                preserved.append(line)
        return "".join(preserved), values

    @staticmethod
    def _restore(text, values):
        return re.sub(r"\uE000(\d+)\uE001", lambda match: values[int(match.group(1))], text)


def chunk_text(text: str, limit=TELEGRAM_SAFE_CHUNK_SIZE):
    if not text:
        return ["模型没有返回可显示的内容，请稍后重试。"]
    chunks, remaining = [], text
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = max(window.rfind("\n\n") + 2, window.rfind("\n") + 1)
        if cut <= 0:
            cut = max(window.rfind(mark) + 1 for mark in "。！？.!?")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return chunks


class CaptureTelegramTransport:
    """No-network transport used to assert exactly what Telegram would receive."""
    def __init__(self):
        self.messages = []

    def send(self, text, chunk_index, chunk_count, parse_mode=None):
        self.messages.append({"text": text, "chunk_index": chunk_index, "chunk_count": chunk_count, "parse_mode": parse_mode})

    def reconstructed(self):
        return "".join(message["text"] for message in self.messages)

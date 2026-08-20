import json
import re
from dataclasses import dataclass


TELEGRAM_SAFE_CHUNK_SIZE = 3600


@dataclass(frozen=True)
class RenderedOutput:
    canonical_text: str
    chunks: tuple[str, ...]


class TelegramOutputRenderer:
    """Presentation-only safe plain-text renderer shared by Owner and Public chat."""
    def render(self, text: str) -> str:
        protected, values = self._protect_literals(text)
        lines = []
        for line in protected.splitlines(keepends=True):
            if re.match(r"^\s{0,3}#{1,6}\s+", line):
                line = re.sub(r"^(\s{0,3})#{1,6}\s+", r"\1", line)
            line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
            line = re.sub(r"__([^_\n]+)__", r"\1", line)
            lines.append(line)
        return self._restore("".join(lines), values).strip()

    def package(self, text: str) -> RenderedOutput:
        canonical = self.render(text)
        return RenderedOutput(canonical, tuple(chunk_text(canonical)))

    def has_visible_markdown_artifacts(self, rendered_text: str) -> bool:
        protected, _ = self._protect_literals(rendered_text)
        return bool(re.search(r"(^|\n)\s*#{1,6}\s+|\*\*[^*\n]+\*\*|__[^_\n]+__", protected))

    def _protect_literals(self, text: str):
        values = []
        def hold(match):
            values.append(match.group(0))
            return f"\uE000{len(values)-1}\uE001"
        text = re.sub(r"```[\s\S]*?```", hold, text)
        text = re.sub(r"`[^`\n]+`", hold, text)
        preserved_lines = []
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            try:
                if stripped.startswith(("{", "[")) and json.loads(stripped) is not None:
                    preserved_lines.append(hold(re.match(r"[\s\S]*", line)))
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            if re.match(r"\s*(?:\$\s+|echo\b|cd\b|ls\b|python\b|git\b|grep\b)", line):
                preserved_lines.append(hold(re.match(r"[\s\S]*", line)))
            else:
                preserved_lines.append(line)
        return "".join(preserved_lines), values

    @staticmethod
    def _restore(text, values):
        return re.sub(r"\uE000(\d+)\uE001", lambda match: values[int(match.group(1))], text)


def chunk_text(text: str, limit=TELEGRAM_SAFE_CHUNK_SIZE):
    if not text:
        return ["模型没有返回可显示的内容，请稍后重试。"]
    chunks, remaining = [], text
    while len(remaining) > limit:
        window = remaining[:limit + 1]
        cut = max(window.rfind("\n\n") + 2, window.rfind("\n") + 1)
        if cut <= 0:
            candidates = [window.rfind(mark) + 1 for mark in "。！？.!?"]
            cut = max(candidates)
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

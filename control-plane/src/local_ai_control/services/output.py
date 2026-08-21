import ast
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass


TELEGRAM_SAFE_CHUNK_SIZE = 3600
_PROTECTED_RANGE_KEY = secrets.token_bytes(32)
_PROTECTED_RANGE_KINDS = frozenset({"code_block", "inline_code", "structured_literal", "string_literal"})


@dataclass(frozen=True)
class _ProtectedRange:
    start: int
    end: int
    kind: str
    _signature: bytes


@dataclass(frozen=True)
class RenderedOutput:
    canonical_text: str
    chunks: tuple[str, ...]
    protected_ranges: tuple[_ProtectedRange, ...] = ()


class TelegramOutputRenderer:
    """Shared safe plain-text renderer. It never sends model HTML to Telegram."""
    def render(self, text: str) -> str:
        canonical, _ = self._render_with_metadata(text)
        return canonical

    def _render_with_metadata(self, text: str):
        protected, values, placeholder_prefix = self._protect_literals(text)
        lines = []
        for line in protected.splitlines(keepends=True):
            line = re.sub(r"^(\s{0,3})#{1,6}\s+", r"\1", line)
            line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
            line = re.sub(r"__([^_\n]+)__", r"\1", line)
            lines.append(line)
        return self._restore_with_ranges("".join(lines), values, placeholder_prefix)

    def package(self, text: str) -> RenderedOutput:
        canonical, ranges = self._render_with_metadata(text)
        return RenderedOutput(canonical, tuple(chunk_text(canonical)), ranges)

    def has_visible_markdown_artifacts(self, rendered_text: str, protected_ranges=()) -> bool:
        """Detect formatting residue only in user-visible prose.

        Ranges originate from the production renderer and cover fenced/inline code,
        JSON, and quoted string literals. Masking those exact spans keeps the
        strict prose policy without globally whitelisting Markdown characters.
        """
        scan_text = self._mask_protected_ranges(rendered_text, protected_ranges)
        for scan_line in scan_text.splitlines():
            if re.search(r"^\s*#{1,6}\s+|\*\*[^*\n]+\*\*|__[^_\n]+__|```", scan_line):
                return True
        return False

    def _protect_literals(self, text: str):
        values = []
        placeholder_prefix = f"\uE000{secrets.token_hex(16)}:"
        while placeholder_prefix in text:
            placeholder_prefix = f"\uE000{secrets.token_hex(16)}:"

        def hold(value, kind="literal"):
            values.append((value, kind))
            return f"{placeholder_prefix}{len(values)-1}\uE001"

        def code_block(match):
            language = match.group(1).strip()
            source = match.group(2).strip("\n")
            label = f"代码（{language}）：" if language else "代码："
            body = "\n".join("    " + line for line in source.splitlines())
            return hold(label + "\n" + body + "\n", "code_block")

        text = re.sub(r"```([^\n`]*)\n([\s\S]*?)```", code_block, text)
        text = self._protect_json_values(text, lambda value: hold(value, "structured_literal"))
        text = re.sub(r"`([^`\n]+)`", lambda match: hold(match.group(1), "inline_code"), text)
        string_literal = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
        protected_lines = []
        for line in text.splitlines(keepends=True):
            if self._is_source_context_line(line):
                line = string_literal.sub(lambda match: hold(match.group(0), "string_literal"), line)
            protected_lines.append(line)
        text = "".join(protected_lines)
        return text, values, placeholder_prefix

    @staticmethod
    def _is_source_context_line(line):
        stripped = line.strip()
        if re.match(r"^(?:\$\s+|(?:curl|wget|export|echo|cd|ls|python(?:3)?|git|grep|rg|sed|awk|cat|printf|chmod|find)\b)", stripped):
            return True
        if re.fullmatch(r"@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\([^\n]*\))?", stripped):
            return True
        try:
            tree = ast.parse(stripped)
        except (SyntaxError, ValueError):
            return False
        if len(tree.body) != 1:
            return False
        node = tree.body[0]
        return isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Raise, ast.Assert)) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        )

    @staticmethod
    def _protect_json_values(text, hold):
        """Hold complete JSON objects/arrays before line-oriented prose cleanup."""
        decoder = json.JSONDecoder()
        result, cursor = [], 0
        while cursor < len(text):
            match = re.search(r"(?m)^\s*(?=[{\[])", text[cursor:])
            if not match:
                result.append(text[cursor:])
                break
            start = cursor + match.start()
            value_start = cursor + match.end()
            try:
                _, end = decoder.raw_decode(text[value_start:])
            except json.JSONDecodeError:
                result.append(text[cursor:value_start])
                cursor = value_start
                continue
            result.append(text[cursor:start])
            result.append(hold(text[start:value_start + end]))
            cursor = value_start + end
        return "".join(result)

    @staticmethod
    def _restore_with_ranges(text, values, placeholder_prefix):
        parts, ranges, cursor, output_length = [], [], 0, 0
        placeholder_pattern = re.compile(re.escape(placeholder_prefix) + r"(\d+)\uE001")

        def expand(index, stack=()):
            if index < 0 or index >= len(values):
                raise ValueError("invalid protected output placeholder")
            if index in stack:
                raise ValueError("cyclic protected output placeholder")
            value, _kind = values[index]
            return placeholder_pattern.sub(
                lambda nested: expand(int(nested.group(1)), stack + (index,)),
                value,
            )

        for match in placeholder_pattern.finditer(text):
            prose = text[cursor:match.start()]
            parts.append(prose)
            output_length += len(prose)
            value_index = int(match.group(1))
            value, kind = values[value_index]
            value = expand(value_index)
            start = output_length
            parts.append(value)
            output_length += len(value)
            ranges.append((start, output_length, kind))
            cursor = match.end()
        parts.append(text[cursor:])
        restored = "".join(parts)
        left_trim = len(restored) - len(restored.lstrip())
        canonical = restored.strip()
        end_limit = left_trim + len(canonical)
        adjusted = tuple(
            TelegramOutputRenderer._signed_range(
                canonical,
                max(start, left_trim) - left_trim,
                min(end, end_limit) - left_trim,
                kind,
            )
            for start, end, kind in ranges
            if end > left_trim and start < end_limit
        )
        return canonical, adjusted

    @staticmethod
    def _mask_protected_ranges(text, protected_ranges):
        if not protected_ranges:
            return text
        characters = list(text)
        previous_end = 0
        for item in protected_ranges:
            if not isinstance(item, _ProtectedRange):
                raise ValueError("untrusted protected output range")
            if item.kind not in _PROTECTED_RANGE_KINDS:
                raise ValueError("unknown protected output range kind")
            if item.start < previous_end:
                raise ValueError("protected output ranges overlap or are unordered")
            if item.start < 0 or item.end <= item.start or item.end > len(characters):
                raise ValueError("invalid protected output range")
            expected = TelegramOutputRenderer._range_signature(text, item.start, item.end, item.kind)
            if not hmac.compare_digest(item._signature, expected):
                raise ValueError("protected output range integrity failure")
            for index in range(item.start, item.end):
                if characters[index] not in "\r\n":
                    characters[index] = " "
            previous_end = item.end
        return "".join(characters)

    @staticmethod
    def _range_signature(text, start, end, kind):
        canonical_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fragment_hash = hashlib.sha256(text[start:end].encode("utf-8")).hexdigest()
        payload = f"{canonical_hash}:{start}:{end}:{kind}:{fragment_hash}".encode("utf-8")
        return hmac.new(_PROTECTED_RANGE_KEY, payload, hashlib.sha256).digest()

    @staticmethod
    def _signed_range(text, start, end, kind):
        return _ProtectedRange(start, end, kind, TelegramOutputRenderer._range_signature(text, start, end, kind))


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

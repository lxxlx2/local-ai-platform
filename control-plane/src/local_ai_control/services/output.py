import ast
import hashlib
import hmac
import html
import json
import re
import secrets
from dataclasses import dataclass
from html.parser import HTMLParser


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
    visible_chunks: tuple[str, ...]
    parse_mode: str = "HTML"
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
            if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*(?:\r?\n)?", line):
                line = "\n" if line.endswith(("\n", "\r")) else ""
            else:
                line = re.sub(r"^(\s{0,3})#{1,6}\s+", r"\1", line)
                line = re.sub(r"^(\s*)[*+-]\s+", r"\1• ", line)
                line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
                line = re.sub(r"__([^_\n]+)__", r"\1", line)
            lines.append(line)
        return self._restore_with_ranges("".join(lines), values, placeholder_prefix)

    def package(self, text: str) -> RenderedOutput:
        canonical, ranges = self._render_with_metadata(text)
        chunks, visible_chunks = self._html_chunks(canonical, ranges)
        return RenderedOutput(canonical, chunks, visible_chunks, "HTML", ranges)

    def transform_unprotected_prose(self, text: str, transform):
        """Apply a text policy only to prose, preserving renderer literals exactly.

        This deliberately shares the production renderer's literal parser so a
        policy cannot rewrite fenced/inline code, JSON, or string literals on a
        syntactically source-like line.
        """
        protected, values, placeholder_prefix = self._protect_literals(
            text,
            preserve_code_fences=True,
            preserve_source_lines=True,
            preserve_inline_markers=True,
        )
        transformed = transform(protected)
        restored, _ranges = self._restore_with_ranges(transformed, values, placeholder_prefix)
        return restored

    def has_source_context(self, text: str) -> bool:
        """Return whether unfenced text contains a syntactically source-like line."""
        without_fences = re.sub(r"```[^\n`]*\n[\s\S]*?```", "", text)
        return any(self._is_source_context_line(line) for line in without_fences.splitlines())

    def has_visible_markdown_artifacts(self, rendered_text: str, protected_ranges=()) -> bool:
        """Detect formatting residue only in user-visible prose.

        Ranges originate from the production renderer and cover fenced/inline code,
        JSON, and quoted string literals. Masking those exact spans keeps the
        strict prose policy without globally whitelisting Markdown characters.
        """
        scan_text = self._mask_protected_ranges(rendered_text, protected_ranges)
        for scan_line in scan_text.splitlines():
            if re.search(r"^\s*(?:#{1,6}\s+|[*+-]\s+|(?:-{3,}|\*{3,}|_{3,})\s*$)|\*\*[^*\n]+\*\*|__[^_\n]+__|```", scan_line):
                return True
        return False

    def _protect_literals(
        self,
        text: str,
        preserve_code_fences=False,
        preserve_source_lines=False,
        preserve_inline_markers=False,
    ):
        values = []
        placeholder_prefix = f"\uE000{secrets.token_hex(16)}:"
        while placeholder_prefix in text:
            placeholder_prefix = f"\uE000{secrets.token_hex(16)}:"

        def hold(value, kind="literal"):
            values.append((value, kind))
            return f"{placeholder_prefix}{len(values)-1}\uE001"

        def code_block(match):
            value = match.group(0) if preserve_code_fences else match.group(2).strip("\n")
            return hold(value, "code_block")

        text = re.sub(r"```([^\n`]*)\n([\s\S]*?)```", code_block, text)
        text = self._protect_json_values(text, lambda value: hold(value, "structured_literal"))
        text = re.sub(
            r"`([^`\n]+)`",
            lambda match: hold(match.group(0) if preserve_inline_markers else match.group(1), "inline_code"),
            text,
        )
        string_literal = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
        source_lines = text.splitlines(keepends=True)
        source_flags = [self._is_source_context_line(line) for line in source_lines]
        changed = True
        while changed:
            changed = False
            for index, line in enumerate(source_lines):
                if not source_flags[index] and line.lstrip().startswith("#") and (
                    (index > 0 and source_flags[index - 1])
                    or (index + 1 < len(source_flags) and source_flags[index + 1])
                ):
                    source_flags[index] = True
                    changed = True
        protected_lines = []
        for line, is_source in zip(source_lines, source_flags):
            if is_source:
                if preserve_source_lines:
                    line = hold(line, "structured_literal")
                else:
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
        if not tree.body:
            return False
        allowed = (
            ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Raise, ast.Assert,
            ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
        )
        return all(
            isinstance(node, allowed)
            or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call))
            for node in tree.body
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
                # Preserve the complete structured-looking candidate verbatim.
                # The scanner handles balanced Python-like literals and
                # unterminated multi-line candidates while guaranteeing progress.
                candidate_end = TelegramOutputRenderer._structured_candidate_end(text, value_start)
                result.append(text[cursor:start])
                result.append(hold(text[start:candidate_end]))
                cursor = candidate_end
                continue
            result.append(text[cursor:start])
            result.append(hold(text[start:value_start + end]))
            cursor = value_start + end
        return "".join(result)

    @staticmethod
    def _structured_candidate_end(text, start):
        matching = {"{": "}", "[": "]", "(": ")"}
        opening = text[start:start + 1]
        if opening not in matching:
            return min(len(text), start + 1)
        stack, quote, escaped, index = [matching[opening]], None, False, start + 1
        while index < len(text):
            character = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                index += 1
                continue
            if character in {'"', "'"}:
                quote = character
            elif character in matching:
                stack.append(matching[character])
            elif character in "}])":
                if not stack or character != stack[-1]:
                    # A mismatched closer proves the candidate is malformed,
                    # but it is not a safe content boundary. Continue until a
                    # blank-line boundary or EOF so later literal lines remain
                    # protected.
                    index += 1
                    continue
                stack.pop()
                if not stack:
                    return index + 1
            elif character == "\n":
                blank = re.match(r"[ \t]*\r?\n", text[index + 1:])
                if blank:
                    return index
            index += 1
        return max(start + 1, len(text))

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

    def _html_chunks(self, canonical, protected_ranges):
        # Integrity validation happens before any metadata can affect HTML output.
        self._mask_protected_ranges(canonical, protected_ranges)
        segments, cursor = [], 0
        for item in protected_ranges:
            if cursor < item.start:
                segments.extend(self._payload_parts(canonical[cursor:item.start], None))
            segments.extend(self._payload_parts(canonical[item.start:item.end], item.kind))
            cursor = item.end
        if cursor < len(canonical):
            segments.extend(self._payload_parts(canonical[cursor:], None))
        if not segments:
            fallback = "模型没有返回可显示的内容，请稍后重试。"
            return (html.escape(fallback),), (fallback,)
        payload_chunks, visible_chunks = [], []
        payload_parts, visible_parts, visible_length = [], [], 0
        for visible, payload in segments:
            if visible_length and visible_length + len(visible) > TELEGRAM_SAFE_CHUNK_SIZE:
                payload_chunks.append("".join(payload_parts))
                visible_chunks.append("".join(visible_parts))
                payload_parts, visible_parts, visible_length = [], [], 0
            payload_parts.append(payload)
            visible_parts.append(visible)
            visible_length += len(visible)
        if payload_parts:
            payload_chunks.append("".join(payload_parts))
            visible_chunks.append("".join(visible_parts))
        return tuple(payload_chunks), tuple(visible_chunks)

    @staticmethod
    def _payload_parts(text, kind):
        if not text:
            return []
        parts = [text] if len(text) <= TELEGRAM_SAFE_CHUNK_SIZE else chunk_text(text)
        rendered = []
        for part in parts:
            escaped = html.escape(part, quote=False)
            if kind == "code_block" or (kind == "structured_literal" and "\n" in part):
                payload = f"<pre><code>{escaped}</code></pre>"
            elif kind in {"inline_code", "structured_literal", "string_literal"}:
                payload = f"<code>{escaped}</code>"
            else:
                payload = escaped
            rendered.append((part, payload))
        return rendered


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
        return "".join(
            telegram_html_to_text(message["text"]) if message["parse_mode"] == "HTML" else message["text"]
            for message in self.messages
        )

    def reconstructed_payload(self):
        return "".join(message["text"] for message in self.messages)


class _TelegramHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class _TelegramHTMLValidator(HTMLParser):
    allowed = {"code", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []
        self.valid = True

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed or attrs:
            self.valid = False
            return
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in self.allowed or not self.stack or self.stack.pop() != tag:
            self.valid = False

    def close(self):
        super().close()
        if self.stack:
            self.valid = False


def telegram_html_to_text(payload):
    parser = _TelegramHTMLTextExtractor()
    parser.feed(payload)
    parser.close()
    return "".join(parser.parts)


def is_safe_telegram_html(payload):
    parser = _TelegramHTMLValidator()
    try:
        parser.feed(payload)
        parser.close()
    except Exception:
        return False
    return parser.valid

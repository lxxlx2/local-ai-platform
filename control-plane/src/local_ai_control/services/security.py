import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityDecision:
    action: str
    category: str | None = None


class SecretFirewall:
    """Detect credentials before storage, logging, embedding, or model calls."""

    _patterns = (
        ("telegram_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b")),
        ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.I)),
        ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
        ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
        ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b", re.I)),
        ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b", re.I)),
        ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.I)),
        ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
        ("evm_private_key", re.compile(r"\b0x[a-fA-F0-9]{64}\b")),
        ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("password", re.compile(r"\b(?:password|passwd|密码)\s*[:=]\s*\S+", re.I)),
        (
            "generic_secret_assignment",
            re.compile(
                r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\s*[:=]\s*['\"]?[A-Za-z0-9._/-]{16,}",
                re.I,
            ),
        ),
    )
    _bip39_common = {"abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse", "access", "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act", "action", "actor", "actress", "actual", "adapt", "add", "address", "adjust", "admit", "adult", "advance", "advice", "aerobic", "affair", "afford", "afraid", "again", "age", "agent", "agree", "ahead", "aim", "air", "airport", "aisle", "alarm", "album", "alcohol", "alert", "alien", "all", "alley", "allow", "almost", "alone", "alpha", "already", "also", "alter", "always", "amateur", "amazing", "among", "amount", "amused", "analyst", "anchor", "ancient", "anger", "angle", "angry", "animal", "ankle", "announce", "annual", "another", "answer", "antenna", "antique", "anxiety", "any", "apart", "apology", "appear", "apple", "approve", "april", "arch", "arctic", "area", "arena", "argue", "arm", "armed", "armor", "army", "around", "arrange", "arrest", "arrive", "arrow", "art", "article", "artist", "artwork", "ask", "aspect", "assault", "asset", "assist", "assume", "asthma", "athlete", "atom", "attack", "attend", "attitude", "attract", "auction", "audit", "august", "aunt", "author", "auto", "autumn", "average", "avocado", "avoid", "awake", "aware", "away", "awesome", "awful", "awkward", "axis"}

    def inspect(self, text: str) -> SecurityDecision:
        for category, pattern in self._patterns:
            if pattern.search(text):
                return SecurityDecision("BLOCK", category)
        words = re.findall(r"[a-z]+", text.lower())
        if len(words) in {12, 15, 18, 21, 24} and all(word in self._bip39_common for word in words):
            return SecurityDecision("BLOCK", "mnemonic")
        if any(word in text.lower() for word in ("seed phrase", "助记词", "私钥")):
            return SecurityDecision("WARN", "possible_sensitive")
        return SecurityDecision("ALLOW")


SECRET_BLOCK_MESSAGE = "⚠️ 检测到疑似敏感凭据。为了安全，该内容没有发送给 AI，也不会保存到对话记录。请不要通过 Telegram 发送助记词、私钥、密码或 API Token。"

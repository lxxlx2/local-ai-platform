from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re

from .provider_router import PrivacyMode
from .security import SecretFirewall


MAX_CLOUD_EGRESS_BYTES = 256_000


class EgressAction(StrEnum):
    ALLOW = "ALLOW"
    SANITIZED = "SANITIZED"
    DENY = "DENY"


@dataclass(frozen=True)
class CloudEgressDecision:
    action: EgressAction
    privacy: PrivacyMode
    material: str
    redactions: tuple[str, ...]
    reason: str
    original_sha256: str
    material_sha256: str

    @property
    def sanitized_for_egress(self) -> bool:
        return self.action in {EgressAction.ALLOW, EgressAction.SANITIZED}


class CloudEgressDenied(PermissionError):
    pass


class CloudEgressGate:
    """Fail-closed boundary for material leaving the Mac.

    The gate never grants network or model authority. It only decides whether a
    bounded text payload may be handed to a cloud provider. Secrets are blocked
    for every privacy class. PRIVATE never leaves the Mac. RESTRICTED is minimized
    with conservative PII/path redaction before it may be routed to cloud review.
    """

    _email = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w-])", re.I)
    _mac_user_path = re.compile(r"/Users/[^/\s]+")
    _windows_user_path = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.I)
    _phone_candidate = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")

    def __init__(self, *, secret_firewall: SecretFirewall | None = None):
        self.secret_firewall = secret_firewall or SecretFirewall()

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _replace_phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 9 <= len(digits) <= 15:
            return "<redacted:phone>"
        return raw

    def _restricted_minimize(self, material: str) -> tuple[str, tuple[str, ...]]:
        redactions: list[str] = []
        value = material

        value, count = self._email.subn("<redacted:email>", value)
        if count:
            redactions.append("email")

        before = value
        value = self._phone_candidate.sub(self._replace_phone, value)
        if value != before:
            redactions.append("phone")

        value, count = self._mac_user_path.subn("/Users/<redacted>", value)
        if count:
            redactions.append("mac_user_path")

        value, count = self._windows_user_path.subn(r"C:\\Users\\<redacted>", value)
        if count:
            redactions.append("windows_user_path")

        return value, tuple(redactions)

    def prepare(self, material: str, privacy: PrivacyMode) -> CloudEgressDecision:
        if not isinstance(material, str) or not material.strip():
            raise ValueError("cloud egress material is empty")
        if len(material.encode("utf-8")) > MAX_CLOUD_EGRESS_BYTES:
            raise ValueError("cloud egress material exceeds safe size bound")

        original_sha = self._sha(material)
        secret = self.secret_firewall.inspect(material)
        if secret.action == "BLOCK":
            return CloudEgressDecision(
                action=EgressAction.DENY,
                privacy=privacy,
                material="",
                redactions=(),
                reason=f"secret_firewall:{secret.category or 'unknown'}",
                original_sha256=original_sha,
                material_sha256=self._sha(""),
            )

        if privacy is PrivacyMode.PRIVATE:
            return CloudEgressDecision(
                action=EgressAction.DENY,
                privacy=privacy,
                material="",
                redactions=(),
                reason="private_cloud_egress_denied",
                original_sha256=original_sha,
                material_sha256=self._sha(""),
            )

        if privacy is PrivacyMode.PUBLIC:
            return CloudEgressDecision(
                action=EgressAction.ALLOW,
                privacy=privacy,
                material=material,
                redactions=(),
                reason="public_material",
                original_sha256=original_sha,
                material_sha256=original_sha,
            )

        minimized, redactions = self._restricted_minimize(material)
        if self.secret_firewall.inspect(minimized).action == "BLOCK":
            return CloudEgressDecision(
                action=EgressAction.DENY,
                privacy=privacy,
                material="",
                redactions=redactions,
                reason="post_minimization_secret_detected",
                original_sha256=original_sha,
                material_sha256=self._sha(""),
            )
        return CloudEgressDecision(
            action=EgressAction.SANITIZED,
            privacy=privacy,
            material=minimized,
            redactions=redactions,
            reason="restricted_minimized",
            original_sha256=original_sha,
            material_sha256=self._sha(minimized),
        )

    def require(self, material: str, privacy: PrivacyMode) -> CloudEgressDecision:
        decision = self.prepare(material, privacy)
        if decision.action is EgressAction.DENY:
            raise CloudEgressDenied(decision.reason)
        return decision

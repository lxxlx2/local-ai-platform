from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Mapping

from .provider_router import InvocationPurpose, PrivacyMode, ProviderSelection, QuotaClass


@dataclass(frozen=True)
class ProviderUsageEvent:
    timestamp: str
    task_id: str
    provider_id: str
    model_id: str | None
    purpose: InvocationPurpose
    privacy: PrivacyMode
    quota_class: QuotaClass
    consumes_codex_quota: bool
    status: str
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    metadata: Mapping[str, str | int | bool] | None = None

    @classmethod
    def from_selection(
        cls,
        *,
        task_id: str,
        selection: ProviderSelection,
        purpose: InvocationPurpose,
        privacy: PrivacyMode,
        status: str,
        model_id: str | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        metadata: Mapping[str, str | int | bool] | None = None,
    ) -> "ProviderUsageEvent":
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            task_id=task_id,
            provider_id=selection.provider.provider_id,
            model_id=model_id,
            purpose=purpose,
            privacy=privacy,
            quota_class=selection.quota_class,
            consumes_codex_quota=selection.consumes_codex_quota,
            status=status,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata=metadata,
        )


class ProviderUsageLedger:
    """Append-only metadata ledger; raw prompts/content are intentionally excluded."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, event: ProviderUsageEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = asdict(event)
        payload["purpose"] = event.purpose.value
        payload["privacy"] = event.privacy.value
        payload["quota_class"] = event.quota_class.value
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.path.chmod(0o600)

    def codex_quota_event_count(self) -> int:
        if not self.path.exists():
            return 0
        count = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("consumes_codex_quota") is True:
                count += 1
        return count

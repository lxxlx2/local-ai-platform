from dataclasses import dataclass
from pathlib import Path
import time


@dataclass(frozen=True)
class RetentionPolicy:
    public_message_retention_days: int = 30
    public_file_retention_hours: int = 24
    public_output_retention_days: int = 7
    public_session_retention_days: int = 30
    owner_message_retention: str = "user-managed"
    owner_file_retention: str = "user-managed"


class StorageQuotaService:
    def __init__(self, root: Path, max_bytes: int):
        self.root, self.max_bytes = root, max_bytes

    def used_bytes(self):
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file() and not path.is_symlink())

    def allows(self, incoming_bytes: int):
        return self.used_bytes() + incoming_bytes <= self.max_bytes

    def evict_synced_cache_only(self, records):
        """Records must explicitly be public/cache and synced; no broad filesystem cleanup."""
        removed = []
        for record in records:
            path = Path(record["path"])
            if record.get("plane") != "public" or not record.get("synced") or record.get("kind") not in {"cache", "output"}:
                continue
            if path.exists() and self.root.resolve() in path.resolve().parents:
                path.unlink()
                removed.append(path)
        return removed

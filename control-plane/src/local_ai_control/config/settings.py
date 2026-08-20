from dataclasses import dataclass
from pathlib import Path
import os


ROOT = Path("/Users/jerson/AI")
SECRET_FILE = ROOT / "runtime/secrets/telegram-bot.env"


def secret_values():
    values = {}
    if SECRET_FILE.exists():
        for line in SECRET_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    token: str | None
    owner_id: str | None
    private_db_path: Path
    public_db_path: Path
    private_memory_db_path: Path
    private_database_url: str | None
    public_database_url: str | None
    local_cache_max_bytes: int = 2 * 1024**3
    local_public_spool_max_bytes: int = 512 * 1024**2
    public_messages_per_minute: int = 12
    public_messages_per_hour: int = 120
    public_messages_per_day: int = 600
    public_concurrent_jobs: int = 1
    public_max_message_bytes: int = 16000
    public_max_file_bytes: int = 10 * 1024**2
    public_max_video_bytes: int = 50 * 1024**2

    @property
    def db_path(self):
        """Compatibility alias for the existing private control-plane database."""
        return self.private_db_path

    @classmethod
    def load(cls):
        values = secret_values()
        get = lambda key: values.get(key) or os.getenv(key)
        return cls(
            get("TELEGRAM_BOT_TOKEN"),
            get("TELEGRAM_OWNER_ID"),
            ROOT / "runtime/control-plane/control-plane.db",
            ROOT / "runtime/public-ai/public-ai.db",
            ROOT / "runtime/control-plane/private-memory.db",
            get("PRIVATE_DATABASE_URL"),
            get("PUBLIC_DATABASE_URL"),
        )

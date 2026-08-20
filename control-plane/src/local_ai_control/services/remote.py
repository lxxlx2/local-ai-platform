from dataclasses import dataclass
from pathlib import Path


class ObjectStorage:
    def put(self, key: str, data: bytes):
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key, data):
        if "/" in key or ".." in key:
            raise ValueError("unsafe object key")
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("unsafe object key")
        target.write_bytes(data)

    def get(self, key):
        return (self.root / key).read_bytes()


class S3CompatibleObjectStorage(ObjectStorage):
    """Configuration-ready adapter. It intentionally cannot connect without a supplied client."""
    def __init__(self, endpoint=None, region=None, bucket=None, client=None):
        self.endpoint, self.region, self.bucket, self.client = endpoint, region, bucket, client

    def _ready(self):
        if not self.client or not self.bucket:
            raise RuntimeError("REMOTE_OBJECT_STORAGE_NOT_CONFIGURED")

    def put(self, key, data):
        self._ready()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key):
        self._ready()
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


@dataclass(frozen=True)
class PostgresAdapter:
    database_url: str | None
    status: str = "NOT_CONFIGURED"

    def migration_sql(self):
        return "CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE IF NOT EXISTS embeddings (id uuid primary key, owner_id text not null, embedding vector);"

import os
from pathlib import Path
from uuid import uuid4


class UnsafeFile(ValueError):
    pass


ALLOWED_EXTENSIONS = {".txt", ".md"}
BLOCKED_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".app", ".dmg", ".pkg", ".sh", ".py", ".command"}


def safe_public_upload(root: Path, supplied_name: str, payload: bytes, mime: str | None = None) -> Path:
    name = Path(supplied_name)
    expected_mime = {".txt": "text/plain", ".md": "text/markdown"}
    if root.is_symlink() or name.is_absolute() or ".." in name.parts or name.suffix.lower() in BLOCKED_EXTENSIONS or name.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise UnsafeFile("unsupported file")
    if mime is not None and mime != expected_mime[name.suffix.lower()]:
        raise UnsafeFile("mime mismatch")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / (str(uuid4()) + name.suffix.lower())).resolve()
    if root not in target.parents or target.exists() or target.is_symlink():
        raise UnsafeFile("unsafe path")
    target.write_bytes(payload)
    return target

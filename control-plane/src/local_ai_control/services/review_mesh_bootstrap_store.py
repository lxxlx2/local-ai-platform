"""Durable, append-only storage for a BOOTSTRAP_V1 journal.

The store persists the complete hash-chained journal after every transition.
Callers must retain the returned ``journal_digest`` in independent Owner-
private state and present it as the compare-and-swap value on the next write;
the digest is a checkpoint, not a signature.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterator

from .review_mesh_bootstrap import (
    BootstrapGuardError,
    BootstrapStateV1,
    BootstrapV1,
)
from .review_mesh_protocol import canonical_json_bytes


class BootstrapJournalStoreV1:
    """Crash-safe single-epoch bootstrap journal with CAS advancement."""

    def __init__(self, journal_path: Path | str):
        raw = Path(journal_path)
        if not raw.is_absolute():
            raise ValueError("bootstrap journal path must be absolute")
        if raw.resolve(strict=False) != raw:
            raise ValueError("bootstrap journal path must be canonical and symlink-free")
        parent = raw.parent
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("bootstrap journal parent must be a non-symlink directory")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PermissionError("bootstrap journal parent must be mode 0700")
        self.path = raw
        self.lock_path = raw.with_name(raw.name + ".lock")

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("bootstrap journal lock must be a regular file")
            if stat.S_IMODE(opened.st_mode) != 0o600:
                raise PermissionError("bootstrap journal lock must be mode 0600")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _strict_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise BootstrapGuardError("bootstrap journal contains duplicate JSON keys")
            value[key] = item
        return value

    def _read_unlocked(self) -> BootstrapV1:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError as error:
            raise BootstrapGuardError("bootstrap journal is not initialized") from error
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise BootstrapGuardError("bootstrap journal must be a regular file")
            if stat.S_IMODE(opened.st_mode) != 0o600:
                raise PermissionError("bootstrap journal must be mode 0600")
            chunks: list[bytes] = []
            remaining = 4_000_001
            while remaining:
                chunk = os.read(descriptor, min(1_048_576, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        if not payload or len(payload) > 4_000_000:
            raise BootstrapGuardError("bootstrap journal size is invalid")
        try:
            decoded = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=self._strict_object_pairs,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    BootstrapGuardError(f"invalid JSON constant: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise BootstrapGuardError("bootstrap journal JSON is invalid") from error
        journal = BootstrapV1.from_mapping(decoded)
        if canonical_json_bytes(journal.to_mapping()) != payload:
            raise BootstrapGuardError("bootstrap journal is not canonical JSON")
        return journal

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write_unlocked(self, journal: BootstrapV1) -> None:
        payload = canonical_json_bytes(journal.to_mapping())
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            written = 0
            while written < len(payload):
                written += os.write(descriptor, view[written:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            self._fsync_directory(self.path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def initialize(self) -> BootstrapV1:
        """Create one empty journal; existing state is never adopted/replaced."""

        with self._writer_lock():
            if self.path.exists() or self.path.is_symlink():
                raise BootstrapGuardError("bootstrap journal already exists")
            journal = BootstrapV1()
            self._atomic_write_unlocked(journal)
            persisted = self._read_unlocked()
            if persisted != journal:
                raise BootstrapGuardError("bootstrap journal initialization verification failed")
            return persisted

    def load(self, *, expected_journal_digest: str) -> BootstrapV1:
        with self._writer_lock():
            journal = self._read_unlocked()
            if journal.journal_digest != expected_journal_digest:
                raise BootstrapGuardError("bootstrap journal checkpoint mismatch")
            return journal

    def advance(
        self,
        *,
        expected_journal_digest: str,
        next_journal: BootstrapV1,
    ) -> BootstrapV1:
        """Persist exactly one valid transition from the trusted checkpoint."""

        if not isinstance(next_journal, BootstrapV1):
            raise BootstrapGuardError("next bootstrap journal type is invalid")
        if next_journal.state is BootstrapStateV1.COMPLETE:
            raise BootstrapGuardError(
                "BOOTSTRAP_COMPLETE is durable only through the ledger genesis API"
            )
        with self._writer_lock():
            current = self._read_unlocked()
            if current.journal_digest != expected_journal_digest:
                raise BootstrapGuardError("bootstrap journal checkpoint mismatch")
            if current.state in {BootstrapStateV1.COMPLETE, BootstrapStateV1.ABORTED}:
                raise BootstrapGuardError("terminal bootstrap journal cannot advance")
            if (
                len(next_journal.events) != len(current.events) + 1
                or next_journal.events[:-1] != current.events
            ):
                raise BootstrapGuardError("bootstrap journal update is not one-event append")
            retained_fields = (
                "authorization",
                "material_pins",
                "inspections",
                "executions",
                "seed_proposal",
                "seed_authorization",
                "complete_payload",
            )
            for field in retained_fields:
                old = getattr(current, field)
                if old not in (None, ()) and getattr(next_journal, field) != old:
                    raise BootstrapGuardError("bootstrap journal rewrites retained payload")
            self._atomic_write_unlocked(next_journal)
            persisted = self._read_unlocked()
            if persisted != next_journal:
                raise BootstrapGuardError("bootstrap journal post-write verification failed")
            return persisted


__all__ = ["BootstrapJournalStoreV1"]

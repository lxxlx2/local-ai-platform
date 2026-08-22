"""Durable, owner-scoped media jobs; payloads stay in private runtime storage."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
import threading
import uuid


class MediaJobKind(StrEnum):
    IMAGE_GENERATION="IMAGE_GENERATION"; IMAGE_EDIT="IMAGE_EDIT"; TTS="TTS"; STT="STT"
    VIDEO_GENERATION="VIDEO_GENERATION"; VIDEO_UNDERSTANDING="VIDEO_UNDERSTANDING"
class MediaJobStatus(StrEnum):
    QUEUED="QUEUED"; PREPARING="PREPARING"; LOADING_MODEL="LOADING_MODEL"; RUNNING="RUNNING"
    ENCODING="ENCODING"; COMPLETED="COMPLETED"; FAILED="FAILED"; CANCELED="CANCELED"

TERMINAL={MediaJobStatus.COMPLETED,MediaJobStatus.FAILED,MediaJobStatus.CANCELED}
TRANSITIONS={
    MediaJobStatus.QUEUED:{MediaJobStatus.PREPARING,MediaJobStatus.CANCELED,MediaJobStatus.FAILED},
    MediaJobStatus.PREPARING:{MediaJobStatus.LOADING_MODEL,MediaJobStatus.RUNNING,MediaJobStatus.CANCELED,MediaJobStatus.FAILED},
    MediaJobStatus.LOADING_MODEL:{MediaJobStatus.RUNNING,MediaJobStatus.CANCELED,MediaJobStatus.FAILED},
    MediaJobStatus.RUNNING:{MediaJobStatus.ENCODING,MediaJobStatus.COMPLETED,MediaJobStatus.CANCELED,MediaJobStatus.FAILED},
    MediaJobStatus.ENCODING:{MediaJobStatus.COMPLETED,MediaJobStatus.CANCELED,MediaJobStatus.FAILED},
}

@dataclass(frozen=True)
class MediaJob:
    job_id:str; owner_id:str; kind:MediaJobKind; status:MediaJobStatus; progress:int
    created_at:str; updated_at:str; input_refs:tuple[str,...]; output_refs:tuple[str,...]
    model_role:str; error_category:str|None=None

class MediaJobRepository:
    def __init__(self,path:Path|str):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path); self.db.row_factory=sqlite3.Row; self._lock=threading.RLock(); self.migrate()
    def migrate(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS media_jobs(
          job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
          progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100), created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, input_refs TEXT NOT NULL, output_refs TEXT NOT NULL,
          model_role TEXT NOT NULL, error_category TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS media_jobs_owner ON media_jobs(owner_id,updated_at DESC)"); self.db.commit()
    def create(self,owner_id,kind,model_role,input_refs=()):
        now=datetime.now(UTC).isoformat(); job_id=f"media:{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO media_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (job_id,owner_id,MediaJobKind(kind),MediaJobStatus.QUEUED,0,now,now,json.dumps(list(input_refs)),"[]",model_role,None)); self.db.commit()
        return self.get(owner_id,job_id)
    def get(self,owner_id,job_id):
        row=self.db.execute("SELECT * FROM media_jobs WHERE owner_id=? AND job_id=?",(owner_id,job_id)).fetchone()
        if not row: raise KeyError("media job not found")
        return MediaJob(row["job_id"],row["owner_id"],MediaJobKind(row["kind"]),MediaJobStatus(row["status"]),row["progress"],row["created_at"],row["updated_at"],tuple(json.loads(row["input_refs"])),tuple(json.loads(row["output_refs"])),row["model_role"],row["error_category"])
    def transition(self,owner_id,job_id,status,*,progress=None,output_refs=(),error_category=None):
        with self._lock:
            current=self.get(owner_id,job_id); target=MediaJobStatus(status)
            if target not in TRANSITIONS.get(current.status,set()): raise ValueError("illegal media job transition")
            value=current.progress if progress is None else progress
            if value<current.progress or not 0<=value<=100: raise ValueError("invalid progress")
            if target is MediaJobStatus.COMPLETED: value=100
            self.db.execute("UPDATE media_jobs SET status=?,progress=?,updated_at=?,output_refs=?,error_category=? WHERE job_id=? AND owner_id=?",
                            (target,value,datetime.now(UTC).isoformat(),json.dumps(list(output_refs)),error_category,job_id,owner_id)); self.db.commit()
            return self.get(owner_id,job_id)
    def cancel(self,owner_id,job_id): return self.transition(owner_id,job_id,MediaJobStatus.CANCELED)
    def close(self): self.db.close()

class MediaJobRunner:
    """Runs an injected bounded job outside Telegram handlers; never shells out."""
    def __init__(self,repository,handlers): self.repository=repository; self.handlers=handlers
    def run(self,owner_id,job_id):
        job=self.repository.transition(owner_id,job_id,MediaJobStatus.PREPARING,progress=1)
        try:
            job=self.repository.transition(owner_id,job_id,MediaJobStatus.LOADING_MODEL,progress=5)
            job=self.repository.transition(owner_id,job_id,MediaJobStatus.RUNNING,progress=10)
            outputs=self.handlers[job.kind](job)
            return self.repository.transition(owner_id,job_id,MediaJobStatus.COMPLETED,output_refs=outputs)
        except Exception as exc:
            return self.repository.transition(owner_id,job_id,MediaJobStatus.FAILED,error_category=type(exc).__name__)

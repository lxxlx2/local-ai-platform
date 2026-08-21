import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from local_ai_control.services.authorization import ensure_owned


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ScopedSQLiteRepository:
    """A deliberately separate SQLite development store for one security plane."""

    def __init__(self, path: Path, plane: str):
        self.path = path
        self.plane = plane
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row

    def migrate(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS summaries(id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, scope TEXT NOT NULL, category TEXT NOT NULL, subject TEXT NOT NULL, content TEXT NOT NULL, source_ref TEXT, confidence REAL, status TEXT NOT NULL, created_at TEXT, updated_at TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS user_settings(owner_id TEXT PRIMARY KEY, memory_opt_in INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT, updated_at TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS usage_events(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT);
            """
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def create_session(self, identity, title="新对话"):
        session_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL)", (session_id, identity.internal_user_id, title, utc_now(), utc_now()))
        self.db.commit()
        return session_id

    def _session(self, identity, session_id):
        row = self.db.execute("SELECT * FROM sessions WHERE id=? AND deleted_at IS NULL", (session_id,)).fetchone()
        if not row:
            raise KeyError("session not found")
        ensure_owned(identity, row["owner_id"])
        return row

    def add_message(self, identity, session_id, role, content):
        self._session(identity, session_id)
        message_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,NULL)", (message_id, session_id, identity.internal_user_id, role, content, utc_now()))
        self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utc_now(), session_id))
        self.db.commit()
        return message_id

    def message_pair_for_feedback(self, identity, assistant_message_id):
        assistant = self.db.execute(
            "SELECT * FROM messages WHERE id=? AND role='assistant' AND deleted_at IS NULL",
            (assistant_message_id,),
        ).fetchone()
        if not assistant:
            raise KeyError("assistant message not found")
        ensure_owned(identity, assistant["owner_id"])
        prompt = self.db.execute(
            """SELECT * FROM messages WHERE session_id=? AND owner_id=? AND role='user'
               AND deleted_at IS NULL AND created_at<=? ORDER BY created_at DESC LIMIT 1""",
            (assistant["session_id"], assistant["owner_id"], assistant["created_at"]),
        ).fetchone()
        if not prompt:
            raise KeyError("source prompt not found")
        return prompt, assistant

    def recent_messages(self, identity, session_id, limit=12):
        self._session(identity, session_id)
        rows = self.db.execute("SELECT * FROM messages WHERE session_id=? AND owner_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT ?", (session_id, identity.internal_user_id, limit)).fetchall()
        return list(reversed(rows))

    def list_sessions(self, identity, limit=10):
        return self.db.execute("SELECT * FROM sessions WHERE owner_id=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?", (identity.internal_user_id, limit)).fetchall()

    def delete_session(self, identity, session_id):
        self._session(identity, session_id)
        now = utc_now()
        self.db.execute("UPDATE sessions SET deleted_at=? WHERE id=?", (now, session_id))
        self.db.execute("UPDATE messages SET deleted_at=? WHERE session_id=?", (now, session_id))
        self.db.commit()

    def set_summary(self, identity, session_id, content):
        self._session(identity, session_id)
        self.db.execute("INSERT INTO summaries VALUES (?,?,?,?,?,NULL) ON CONFLICT(session_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at,deleted_at=NULL", (str(uuid.uuid4()), session_id, identity.internal_user_id, content, utc_now()))
        self.db.commit()

    def get_summary(self, identity, session_id):
        self._session(identity, session_id)
        return self.db.execute("SELECT * FROM summaries WHERE session_id=? AND owner_id=? AND deleted_at IS NULL", (session_id, identity.internal_user_id)).fetchone()

    def set_memory_opt_in(self, identity, enabled: bool):
        self.db.execute("INSERT INTO user_settings VALUES (?,?,?) ON CONFLICT(owner_id) DO UPDATE SET memory_opt_in=excluded.memory_opt_in,updated_at=excluded.updated_at", (identity.internal_user_id, int(enabled), utc_now()))
        self.db.commit()

    def memory_opted_in(self, identity):
        row = self.db.execute("SELECT memory_opt_in FROM user_settings WHERE owner_id=?", (identity.internal_user_id,)).fetchone()
        return bool(row and row["memory_opt_in"])

    def add_memory(self, identity, category, subject, content, source_ref=None, confidence=1.0):
        if self.plane == "public" and not self.memory_opted_in(identity):
            raise PermissionError("public memory requires consent")
        memory_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)", (memory_id, identity.internal_user_id, identity.scope, category, subject, content, source_ref, confidence, "ACTIVE", utc_now(), utc_now()))
        self.db.commit()
        return memory_id

    def list_memories(self, identity, query=None, limit=10):
        sql = "SELECT * FROM memories WHERE owner_id=? AND deleted_at IS NULL AND status='ACTIVE'"
        values = [identity.internal_user_id]
        if query:
            sql += " AND (subject LIKE ? OR content LIKE ?)"
            values.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        return self.db.execute(sql, values).fetchall()

    def delete_memory(self, identity, memory_id):
        row = self.db.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (memory_id,)).fetchone()
        if not row:
            raise KeyError("memory not found")
        ensure_owned(identity, row["owner_id"])
        self.db.execute("UPDATE memories SET deleted_at=?,status='DELETED',updated_at=? WHERE id=?", (utc_now(), utc_now(), memory_id))
        self.db.commit()

    def create_task(self, identity, kind):
        task_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,NULL)", (task_id, identity.internal_user_id, kind, "QUEUED", utc_now(), utc_now()))
        self.db.commit()
        return task_id

    def get_task(self, identity, task_id):
        row = self.db.execute("SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if not row:
            raise KeyError("task not found")
        ensure_owned(identity, row["owner_id"])
        return row

    def record_usage(self, identity, kind):
        self.db.execute("INSERT INTO usage_events VALUES (?,?,?,?)", (str(uuid.uuid4()), identity.internal_user_id, kind, utc_now()))
        self.db.commit()

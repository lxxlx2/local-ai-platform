"""Restart-safe Owner-only Telegram Media Product Workflow wizard."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
import threading

from local_ai_control.domain.identity import Role
from local_ai_control.services.media_workflow import CompletionMode, IntakeMode, new_media_workspace, utc_now


class WizardStep(StrEnum):
    TASK_NAME="TASK_NAME"; SOURCE_MODE="SOURCE_MODE"; MATERIALS="MATERIALS"
    EXECUTION_MODE="EXECUTION_MODE"; LANGUAGE="LANGUAGE"; VOICE="VOICE"
    COMPLETION_MODE="COMPLETION_MODE"; CONFIRMATION="CONFIRMATION"; CREATED="CREATED"


@dataclass(frozen=True)
class WizardSession:
    owner_id:str; step:WizardStep; values:dict; updated_at:str


class MediaWizardStore:
    def __init__(self,path:Path|str="/Users/jerson/AI/runtime/control-plane/media-wizard.db"):
        self.path=Path(path); self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,check_same_thread=False); self.db.row_factory=sqlite3.Row; self.lock=threading.RLock()
        self.db.execute("CREATE TABLE IF NOT EXISTS media_wizard(owner_id TEXT PRIMARY KEY,step TEXT NOT NULL,values_json TEXT NOT NULL,updated_at TEXT NOT NULL)"); self.db.commit()
    def _save(self,owner_id,step,values):
        with self.lock:
            self.db.execute("INSERT INTO media_wizard VALUES(?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET step=excluded.step,values_json=excluded.values_json,updated_at=excluded.updated_at",(owner_id,WizardStep(step),json.dumps(values,ensure_ascii=False),utc_now())); self.db.commit()
        return self.get(owner_id)
    def start(self,owner_id): return self._save(owner_id,WizardStep.TASK_NAME,{})
    def get(self,owner_id):
        row=self.db.execute("SELECT * FROM media_wizard WHERE owner_id=?",(owner_id,)).fetchone()
        return None if not row else WizardSession(row["owner_id"],WizardStep(row["step"]),json.loads(row["values_json"]),row["updated_at"])
    def update(self,owner_id,step,**values):
        current=self.get(owner_id)
        if current is None: raise KeyError("wizard not active")
        merged={**current.values,**values}; return self._save(owner_id,step,merged)
    def cancel(self,owner_id):
        with self.lock: self.db.execute("DELETE FROM media_wizard WHERE owner_id=?",(owner_id,)); self.db.commit()
    def close(self): self.db.close()


class MediaWizardController:
    """Deterministic steps; every mutation is persisted before the next prompt."""
    def __init__(self,store:MediaWizardStore,*,job_root="/Users/jerson/AI/runtime/media-jobs"):
        self.store=store; self.job_root=Path(job_root)
    @staticmethod
    def require_owner(role):
        if role is not Role.OWNER: raise PermissionError("owner media wizard only")
    def start(self,role,owner_id): self.require_owner(role); return self.store.start(owner_id)
    def text(self,role,owner_id,text):
        self.require_owner(role); session=self.store.get(owner_id)
        if not session: raise KeyError("wizard not active")
        value=text.strip()
        if not value or len(value)>10_000: raise ValueError("wizard text invalid")
        if session.step is WizardStep.TASK_NAME:
            if len(value)>80: raise ValueError("task name too long")
            return self.store.update(owner_id,WizardStep.SOURCE_MODE,task_name=value)
        if session.step is WizardStep.MATERIALS:
            key="source_url" if session.values.get("source_mode") in {"LINKS","UPLOADS_AND_LINKS"} and value.startswith(("http://","https://")) else "brief"
            return self.store.update(owner_id,WizardStep.EXECUTION_MODE,**{key:value})
        raise ValueError("wizard is not waiting for text")
    def choice(self,role,owner_id,key,value):
        self.require_owner(role); session=self.store.get(owner_id)
        if not session: raise KeyError("wizard not active")
        expected={
            "source_mode":(WizardStep.SOURCE_MODE,WizardStep.MATERIALS,{item.value for item in IntakeMode}),
            "execution_mode":(WizardStep.EXECUTION_MODE,WizardStep.LANGUAGE,{"AUTO","REVIEW_SCRIPT"}),
            "language":(WizardStep.LANGUAGE,WizardStep.VOICE,{"auto","zh","en"}),
            "voice":(WizardStep.VOICE,WizardStep.COMPLETION_MODE,{"auto","zh-male-25-default","en-male-25-default"}),
            "completion_mode":(WizardStep.COMPLETION_MODE,WizardStep.CONFIRMATION,{item.value for item in CompletionMode}),
        }
        current,next_step,allowed=expected[key]
        if session.step is not current or value not in allowed: raise ValueError("wizard choice invalid")
        return self.store.update(owner_id,next_step,**{key:value})
    def confirm(self,role,owner_id):
        self.require_owner(role); session=self.store.get(owner_id)
        if not session or session.step is not WizardStep.CONFIRMATION: raise ValueError("wizard not ready")
        values=session.values
        required={"task_name","source_mode","execution_mode","language","voice","completion_mode"}
        if not required<=set(values) or not ({"brief","source_url"}&set(values)): raise ValueError("wizard fields incomplete")
        workspace=new_media_workspace(values["task_name"],owner_id,intake_mode=values["source_mode"],completion_mode=values["completion_mode"],root=self.job_root)
        return self.store.update(owner_id,WizardStep.CREATED,job_ref=workspace.path.name)


def wizard_summary(session:WizardSession)->str:
    values=session.values
    sources={"UPLOADS":"上传材料","LINKS":"链接","UPLOADS_AND_LINKS":"上传材料与链接","DIRECT_BRIEF":"直接说明"}
    modes={"AUTO":"自动完成","REVIEW_SCRIPT":"先审文稿","AUTO_COMPLETE":"自动完成后审视频","SCRIPT_REVIEW_FIRST":"先审文稿再生成"}
    return ("请确认视频任务\n\n"
            f"名称：{values.get('task_name','未设置')}\n"
            f"材料：{sources.get(values.get('source_mode'),'未设置')}\n"
            f"语言：{values.get('language','auto')}\n"
            f"声音：{values.get('voice','auto')}\n"
            f"流程：{modes.get(values.get('completion_mode'),'未设置')}")

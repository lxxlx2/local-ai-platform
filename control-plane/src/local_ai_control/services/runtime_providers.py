"""Authoritative provider selection with one-heavy-runtime enforcement."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import plistlib
import subprocess
import threading
import time

from local_ai_control.services.models import MemoryPreflight,ModelRegistry,ModelRole,QWEN36,QWEN38
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.qwen38_runtime import Qwen38Provider,RuntimeUnavailable


class HeavyModelConflict(RuntimeError): pass


@dataclass(frozen=True)
class RuntimeState:
    main_healthy: bool
    fast_healthy: bool
    active_profile_id: str|None


def _healthy(provider):
    try: provider.health(); return True
    except Exception: return False


class LaunchdHeavyRuntimeLifecycle:
    """Switches only the two model services owned by this platform."""
    def __init__(self,runtime_root=Path("/Users/jerson/AI/runtime/model-services"),sleep=time.sleep):
        self.runtime_root=Path(runtime_root); self.sleep=sleep; self.uid=str(subprocess.check_output(["id","-u"],text=True).strip())
        self.labels={"local-qwen38":"local-ai.qwen38-runtime","local-qwen36":"local-ai.omlx-qwen36"}

    def _plist(self,profile_id):
        self.runtime_root.mkdir(parents=True,exist_ok=True)
        if profile_id=="local-qwen38": args=["/Users/jerson/AI/runtime/qwen38-venv/bin/python","/Users/jerson/AI/control-plane/scripts/qwen38-sidecar.py","--port","8001"]
        elif profile_id=="local-qwen36": args=["/Users/jerson/AI/runtime/omlx-venv/bin/omlx","serve","--model-dir","/Users/jerson/AI/models","--host","127.0.0.1","--port","8000","--max-concurrent-requests","1","--memory-guard-gb","28","--no-cache","--initial-cache-blocks","64"]
        else: raise ValueError("unknown managed profile")
        label=self.labels[profile_id]; path=self.runtime_root/f"{label}.plist"
        payload={"Label":label,"ProgramArguments":args,"WorkingDirectory":"/Users/jerson/AI","KeepAlive":True,"RunAtLoad":True,"ProcessType":"Interactive","StandardOutPath":str(self.runtime_root/f"{label}.stdout.log"),"StandardErrorPath":str(self.runtime_root/f"{label}.stderr.log")}
        temporary=path.with_suffix(".tmp")
        with temporary.open("wb") as handle: plistlib.dump(payload,handle)
        temporary.replace(path); return path

    def stop(self,profile_id):
        label=self.labels[profile_id]
        subprocess.run(["launchctl","bootout",f"gui/{self.uid}/{label}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)

    def start(self,profile_id):
        path=self._plist(profile_id); label=self.labels[profile_id]
        result=subprocess.run(["launchctl","bootstrap",f"gui/{self.uid}",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        if result.returncode: subprocess.run(["launchctl","kickstart",f"gui/{self.uid}/{label}"],check=True)

    def activate(self,profile_id):
        other="local-qwen36" if profile_id=="local-qwen38" else "local-qwen38"
        self.stop(other); self.start(profile_id)


class RuntimeProviderFactory:
    def __init__(self,registry=None,*,main=None,fast=None,preflight=None,lifecycle=None,sleep=time.sleep):
        self.registry=registry or ModelRegistry()
        main_alias=self.registry.alias(ModelRole.MAIN)
        self.main=main or Qwen38Provider(max_context_tokens=main_alias.max_context_tokens)
        self.fast=fast or OmlxProvider()
        self.preflight=preflight or MemoryPreflight(); self.lifecycle=lifecycle or LaunchdHeavyRuntimeLifecycle(); self.sleep=sleep; self.lock=threading.RLock()

    def _eligible(self,role,profile_id): return any(profile.profile_id==profile_id for profile in self.registry.eligible(role))

    def state(self):
        main=_healthy(self.main); fast=_healthy(self.fast)
        if main and fast: raise HeavyModelConflict("two heavy model runtimes detected")
        return RuntimeState(main,fast,QWEN38.profile_id if main else (QWEN36.profile_id if fast else None))

    def _wait(self,provider,attempts=30):
        for _ in range(attempts):
            if _healthy(provider): return
            self.sleep(1)
        raise RuntimeUnavailable("managed runtime failed health gate")

    def _wait_down(self,provider,attempts=10):
        for _ in range(attempts):
            if not _healthy(provider): return
            self.sleep(1)
        raise RuntimeUnavailable("owned previous runtime did not stop; target was not started")

    def _preflight(self,target,current=None):
        owned=(current.expected_memory_gib or 0) if current else 0
        return self.preflight.check(target.expected_memory_gib or 0,owned_reclaimable_gib=owned)

    def _switch(self,target,provider,*,current=None,current_provider=None):
        check=self._preflight(target,current)
        if not check.allowed: raise RuntimeUnavailable(f"resource preflight denied: {check.reason}")
        target_started=False
        try:
            if current and current_provider:
                self.lifecycle.stop(current.profile_id); self._wait_down(current_provider)
            self.lifecycle.start(target.profile_id); target_started=True; self._wait(provider)
        except Exception:
            if target_started: self.lifecycle.stop(target.profile_id)
            if current and current_provider:
                if not _healthy(current_provider):
                    self.lifecycle.start(current.profile_id); self._wait(current_provider)
            raise

    @contextmanager
    def session(self,task_type="CHAT"):
        with self.lock:
            state=self.state(); explicit_fast=task_type in {"FAST","CHAT_FAST"}
            restore_main=False
            if explicit_fast:
                if not self._eligible(ModelRole.FAST,QWEN36.profile_id): raise RuntimeUnavailable("FAST is not eligible")
                if state.main_healthy:
                    self._switch(QWEN36,self.fast,current=QWEN38,current_provider=self.main); restore_main=True
                elif not state.fast_healthy:
                    self._switch(QWEN36,self.fast)
                provider=self.fast
            else:
                required_role=ModelRole.VISION if task_type=="VISION" else ModelRole.MAIN
                main_eligible=self._eligible(required_role,QWEN38.profile_id)
                if state.main_healthy and main_eligible: provider=self.main
                elif main_eligible:
                    try:
                        self._switch(QWEN38,self.main,current=QWEN36 if state.fast_healthy else None,current_provider=self.fast if state.fast_healthy else None)
                        provider=self.main
                    except Exception as main_error:
                        if task_type=="VISION": raise RuntimeUnavailable("VISION runtime unavailable") from main_error
                        if not self._eligible(ModelRole.FALLBACK,QWEN36.profile_id): raise RuntimeUnavailable("MAIN unavailable and fallback ineligible") from main_error
                        if not _healthy(self.fast): self._switch(QWEN36,self.fast)
                        provider=self.fast
                elif task_type!="VISION" and self._eligible(ModelRole.FALLBACK,QWEN36.profile_id):
                    if not state.fast_healthy: self._switch(QWEN36,self.fast)
                    provider=self.fast
                else: raise RuntimeUnavailable("no healthy chat runtime")
            try: yield provider
            finally:
                if restore_main:
                    self._switch(QWEN38,self.main,current=QWEN36,current_provider=self.fast)

    def runtime_health(self):
        try: state=self.state()
        except HeavyModelConflict: return {"MAIN":"CONFLICT","FAST":"CONFLICT","FALLBACK":"CONFLICT","VISION":"CONFLICT"}
        return {"MAIN":"HEALTHY" if state.main_healthy else "NOT_RUNNING","VISION":"HEALTHY" if state.main_healthy else "NOT_RUNNING","FAST":"HEALTHY" if state.fast_healthy else "NOT_RUNNING","FALLBACK":"HEALTHY" if state.fast_healthy else "NOT_RUNNING"}

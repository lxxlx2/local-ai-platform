"""Authoritative provider selection with one-heavy-runtime enforcement."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import subprocess
import threading
import time
import urllib.error

from local_ai_control.services.heavy_process_identity import expected_identity,identity_status,listener_pids,process_snapshot,write_identity
from local_ai_control.services.models import MemoryPreflight,ModelRegistry,ModelRole,QWEN36,QWEN38
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.qwen38_runtime import ContextLimitExceeded,Qwen38Provider,RuntimeUnavailable


class HeavyModelConflict(RuntimeError): pass


@dataclass(frozen=True)
class RuntimeState:
    main_healthy: bool
    fast_healthy: bool
    active_profile_id: str|None


@dataclass(frozen=True)
class HeavyRuntimeEvidence:
    profile_id: str
    identity_status: str
    identity_pid: int|None
    listener_pids: tuple[int,...]
    service_present: bool
    endpoint_healthy: bool


def _healthy(provider):
    try: provider.health(); return True
    except Exception: return False


class LaunchdHeavyRuntimeLifecycle:
    """Switches exact labels and proves the owned process actually exited."""
    def __init__(self,runtime_root=Path("/Users/jerson/AI/runtime/model-services"),sleep=time.sleep,*,runner=subprocess.run,snapshot=process_snapshot,listeners=listener_pids):
        self.runtime_root=Path(runtime_root); self.sleep=sleep; self.uid=str(subprocess.check_output(["id","-u"],text=True).strip())
        self.runner=runner; self.snapshot=snapshot; self.listeners=listeners
        self.labels={"local-qwen38":"local-ai.qwen38-runtime","local-qwen36":"local-ai.omlx-qwen36"}
        self._start_lock=threading.RLock(); self._authorized_start=None; self._authorized_probes=None

    def _launch_args(self,profile_id):
        if profile_id=="local-qwen38": return ["/Users/jerson/AI/runtime/qwen38-venv/bin/python","/Users/jerson/AI/control-plane/scripts/qwen38-sidecar.py","--port","8001"]
        if profile_id=="local-qwen36": return ["/Users/jerson/AI/runtime/omlx-venv/bin/omlx","serve","--model-dir","/Users/jerson/AI/models","--host","127.0.0.1","--port","8000","--max-concurrent-requests","1","--memory-guard-gb","28","--no-cache","--initial-cache-blocks","64"]
        raise ValueError("unknown managed profile")

    def _process_signature(self,profile_id):
        launch=self._launch_args(profile_id)
        if profile_id=="local-qwen36":
            interpreter="/Users/jerson/AI/runtime/omlx-venv/bin/python"
            argv=(interpreter,launch[0],*launch[1:])
        else:
            interpreter=launch[0]; argv=tuple(launch)
        return str(Path(interpreter).resolve()),tuple(argv)

    def _port(self,profile_id): return 8001 if profile_id=="local-qwen38" else 8000
    def _identity_path(self,profile_id): return self.runtime_root/f"{profile_id}.identity.json"

    def _service_present(self,profile_id):
        result=self.runner(["launchctl","print",f"gui/{self.uid}/{self.labels[profile_id]}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        return result.returncode==0

    def inspect(self,profile_id,endpoint_healthy):
        """Collect exact evidence without treating endpoint health as ownership."""
        status,pid=identity_status(self._identity_path(profile_id),snapshot=self.snapshot)
        try:
            listeners=tuple(self.listeners(self._port(profile_id)))
            service=bool(self._service_present(profile_id))
            endpoint=bool(endpoint_healthy())
        except Exception as error:
            raise HeavyModelConflict("heavy runtime evidence inspection failed") from error
        return HeavyRuntimeEvidence(profile_id,status,pid,listeners,service,endpoint)

    @staticmethod
    def _validate_evidence(evidence):
        if evidence.listener_pids:
            if evidence.identity_status!="MATCH" or set(evidence.listener_pids)!={evidence.identity_pid}:
                raise HeavyModelConflict("unknown fixed-port listener; no process was controlled")
        if evidence.identity_status in {"DEAD","MISMATCH"}:
            if evidence.endpoint_healthy or evidence.service_present or evidence.listener_pids:
                raise HeavyModelConflict("stale identity conflicts with live runtime evidence")
            return
        if evidence.identity_status in {"MISSING","INVALID"}:
            if evidence.endpoint_healthy or evidence.service_present or evidence.listener_pids:
                raise HeavyModelConflict("runtime evidence exists without exact saved ownership")
            return
        if evidence.identity_status!="MATCH":
            raise HeavyModelConflict("unknown heavy runtime identity state")

    def _wait_absent(self,profile_id,endpoint_healthy,attempts=20):
        for _ in range(attempts):
            evidence=self.inspect(profile_id,endpoint_healthy)
            if (evidence.identity_status in {"DEAD","MISMATCH"} and
                    not evidence.endpoint_healthy and not evidence.service_present and not evidence.listener_pids):
                return
            if evidence.identity_status in {"MISSING","INVALID"}:
                raise HeavyModelConflict("runtime identity became ambiguous while stopping")
            self.sleep(0.5)
        raise HeavyModelConflict("owned heavy runtime did not become fully absent")

    def reconcile_before_start(self,target_profile_id,endpoint_probes):
        """Prove both heavy profiles absent before authorizing one start.

        The first pass validates every profile before any owned label is
        controlled.  This prevents an unknown listener on one port from being
        hidden by stopping the other profile first.
        """
        if target_profile_id not in self.labels or set(endpoint_probes)!=set(self.labels):
            raise HeavyModelConflict("complete managed-profile probes are required")
        with self._start_lock:
            self._authorized_start=None; self._authorized_probes=None
            evidence={profile:self.inspect(profile,endpoint_probes[profile]) for profile in self.labels}
            for item in evidence.values(): self._validate_evidence(item)
            for profile,item in evidence.items():
                if item.identity_status=="MATCH":
                    self.safe_stop(profile,endpoint_probes[profile])
                    self._wait_absent(profile,endpoint_probes[profile])
            # Close the observation/control race before issuing bootstrap.
            final={profile:self.inspect(profile,endpoint_probes[profile]) for profile in self.labels}
            for item in final.values():
                self._validate_evidence(item)
                if item.identity_status=="MATCH":
                    raise HeavyModelConflict("heavy runtime reappeared before start")
            self._authorized_start=target_profile_id; self._authorized_probes=dict(endpoint_probes)

    def _plist(self,profile_id):
        self.runtime_root.mkdir(parents=True,exist_ok=True)
        self.runtime_root.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(self.runtime_root,0o700)
        args=self._launch_args(profile_id)
        label=self.labels[profile_id]; path=self.runtime_root/f"{label}.plist"
        payload={"Label":label,"ProgramArguments":args,"WorkingDirectory":"/Users/jerson/AI","KeepAlive":True,"RunAtLoad":True,"ProcessType":"Interactive","StandardOutPath":str(self.runtime_root/f"{label}.stdout.log"),"StandardErrorPath":str(self.runtime_root/f"{label}.stderr.log")}
        temporary=path.with_suffix(".tmp")
        with temporary.open("wb") as handle: plistlib.dump(payload,handle)
        temporary.replace(path); return path

    def safe_stop(self,profile_id,endpoint_healthy):
        """Boot out only when saved ownership is exact; never kill by PID."""
        path=self._identity_path(profile_id); before,saved_pid=identity_status(path,snapshot=self.snapshot)
        endpoint_up=bool(endpoint_healthy())
        try: listeners=tuple(self.listeners(self._port(profile_id)))
        except Exception as error: raise HeavyModelConflict("listener ownership inspection failed") from error
        if listeners and (before!="MATCH" or set(listeners)!={saved_pid}):
            raise HeavyModelConflict("unknown fixed-port listener; no process was controlled")
        if before!="MATCH":
            if endpoint_up or before in {"MISSING","INVALID"} or self._service_present(profile_id):
                raise HeavyModelConflict("runtime ownership is ambiguous; reconciliation required")
            if before in {"DEAD","MISMATCH"}:
                return "ALREADY_STOPPED"
            raise HeavyModelConflict("runtime identity is not controllable")
        result=self.runner(["launchctl","bootout",f"gui/{self.uid}/{self.labels[profile_id]}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        if result.returncode:
            after,_=identity_status(path,snapshot=self.snapshot)
            if self._service_present(profile_id) or bool(endpoint_healthy()) or after not in {"DEAD","MISMATCH"}:
                raise HeavyModelConflict("launchctl bootout failed while owned runtime may be live")
        return "STOP_REQUESTED"

    def wait_stopped(self,profile_id,endpoint_healthy,attempts=20):
        path=self._identity_path(profile_id)
        for _ in range(attempts):
            status,_pid=identity_status(path,snapshot=self.snapshot)
            if status in {"DEAD","MISMATCH"} and not endpoint_healthy():
                return
            if status in {"MISSING","INVALID"}:
                raise HeavyModelConflict("runtime identity became ambiguous")
            self.sleep(0.5)
        raise HeavyModelConflict("owned heavy process did not exit")

    def capture_started(self,profile_id):
        pids=self.listeners(self._port(profile_id))
        if len(pids)!=1:
            raise HeavyModelConflict("owned runtime listener is not unique")
        identity=self.snapshot(pids[0])
        executable,argv=self._process_signature(profile_id)
        if identity is None or not expected_identity(identity,executable,argv):
            raise HeavyModelConflict("listener process does not match managed profile")
        write_identity(self._identity_path(profile_id),identity)
        return identity

    def start(self,profile_id):
        with self._start_lock:
            probes=self._authorized_probes
            if self._authorized_start!=profile_id or probes is None:
                raise HeavyModelConflict("heavy runtime start was not reconciled")
            self._authorized_start=None; self._authorized_probes=None
            for managed in self.labels:
                evidence=self.inspect(managed,probes[managed]); self._validate_evidence(evidence)
                if evidence.identity_status=="MATCH":
                    raise HeavyModelConflict("heavy runtime appeared after reconciliation")
            path=self._plist(profile_id); label=self.labels[profile_id]
            result=self.runner(["launchctl","bootstrap",f"gui/{self.uid}",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            if result.returncode:
                kicked=self.runner(["launchctl","kickstart",f"gui/{self.uid}/{label}"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                if kicked.returncode: raise RuntimeUnavailable("managed runtime start failed")

    def stop(self,profile_id):
        raise HeavyModelConflict("unsafe stop API disabled; use safe_stop with endpoint proof")


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

    def _stop_and_prove(self,profile,provider):
        if hasattr(self.lifecycle,"safe_stop"):
            self.lifecycle.safe_stop(profile.profile_id,lambda:_healthy(provider))
            self.lifecycle.wait_stopped(profile.profile_id,lambda:_healthy(provider))
        else:
            # Dependency-injected legacy test doubles never control real PIDs.
            self.lifecycle.stop(profile.profile_id); self._wait_down(provider)

    def _start_and_capture(self,profile,provider):
        if hasattr(self.lifecycle,"reconcile_before_start"):
            self.lifecycle.reconcile_before_start(profile.profile_id,{
                QWEN38.profile_id:lambda:_healthy(self.main),
                QWEN36.profile_id:lambda:_healthy(self.fast),
            })
        self.lifecycle.start(profile.profile_id); self._wait(provider)
        if hasattr(self.lifecycle,"capture_started"):
            self.lifecycle.capture_started(profile.profile_id)

    def _preflight(self,target,current=None):
        owned=(current.expected_memory_gib or 0) if current else 0
        return self.preflight.check(target.expected_memory_gib or 0,owned_reclaimable_gib=owned)

    def _switch(self,target,provider,*,current=None,current_provider=None):
        check=self._preflight(target,current)
        if not check.allowed: raise RuntimeUnavailable(f"resource preflight denied: {check.reason}")
        target_started=False; current_stop_proven=False
        try:
            if current and current_provider:
                self._stop_and_prove(current,current_provider)
                current_stop_proven=True
            # Treat start as potentially partial even when it raises. Cleanup
            # must prove the target down before any rollback is attempted.
            target_started=True; self._start_and_capture(target,provider)
        except Exception:
            if target_started:
                try: self._stop_and_prove(target,provider)
                except Exception as cleanup_error:
                    raise HeavyModelConflict("failed target could not be confirmed down") from cleanup_error
            if current and current_provider and current_stop_proven:
                if not _healthy(current_provider):
                    self._start_and_capture(current,current_provider)
            raise

    def _fallback_target(self,state):
        if not self._eligible(ModelRole.FALLBACK,QWEN36.profile_id):
            raise RuntimeUnavailable("FALLBACK is not eligible")
        if state.fast_healthy: return self.fast
        self._switch(QWEN36,self.fast,current=QWEN38 if state.main_healthy else None,
                     current_provider=self.main if state.main_healthy else None)
        return self.fast

    @contextmanager
    def session(self,task_type="CHAT"):
        with self.lock:
            state=self.state(); explicit_fast=task_type in {"FAST","CHAT_FAST"}
            restore_main=False
            if explicit_fast:
                if not self._eligible(ModelRole.FAST,QWEN36.profile_id): raise RuntimeUnavailable("FAST is not eligible")
                restore_main=self._eligible(ModelRole.MAIN,QWEN38.profile_id)
                if state.fast_healthy:
                    provider=self.fast
                elif state.main_healthy:
                    self._switch(QWEN36,self.fast,current=QWEN38,current_provider=self.main)
                    provider=self.fast
                else:
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
                    except HeavyModelConflict:
                        raise
                    except Exception as main_error:
                        if task_type=="VISION": raise RuntimeUnavailable("VISION runtime unavailable") from main_error
                        provider=self._fallback_target(self.state())
                elif task_type!="VISION":
                    # Eligibility selects Qwen3.6, but observed Qwen3.8 health
                    # still governs the physical stop-before-start transition.
                    provider=self._fallback_target(state)
                else: raise RuntimeUnavailable("no healthy chat runtime")
            try: yield provider
            finally:
                if restore_main:
                    try:
                        self._switch(QWEN38,self.main,current=QWEN36,current_provider=self.fast)
                    except HeavyModelConflict:
                        raise
                    except Exception:
                        state=self.state()
                        if state.main_healthy or not state.fast_healthy:
                            raise HeavyModelConflict("MAIN restore did not preserve exactly one fallback")

    @contextmanager
    def failover_session(self):
        """One infrastructure failover after a selected MAIN dies."""
        with self.lock:
            if not self._eligible(ModelRole.FALLBACK,QWEN36.profile_id):
                raise RuntimeUnavailable("FALLBACK is not eligible")
            # MAIN may be unhealthy while its managed process still owns Metal
            # resources. Stop the exact owned label and confirm its endpoint down.
            self._stop_and_prove(QWEN38,self.main)
            state=self.state()
            provider=self.fast if state.fast_healthy else None
            if provider is None:
                self._switch(QWEN36,self.fast)
                provider=self.fast
            yield provider

    def generate(self,task_type,prompt,max_output_tokens=1024):
        """Select once, then retry once only for MAIN infrastructure death."""
        selected_main=False
        try:
            with self.session(task_type) as provider:
                selected_main=provider is self.main
                return provider.generate(prompt,max_output_tokens=max_output_tokens)
        except ContextLimitExceeded:
            raise
        except (PermissionError,ValueError):
            # PermissionError is an OSError subclass; keep policy/validation
            # rejection outside the infrastructure-failover boundary.
            raise
        except (RuntimeUnavailable,ConnectionError,urllib.error.URLError,OSError):
            if task_type in {"FAST","CHAT_FAST"} or not selected_main: raise
        with self.failover_session() as fallback:
            return fallback.generate(prompt,max_output_tokens=max_output_tokens)

    def runtime_health(self):
        try: state=self.state()
        except HeavyModelConflict: return {"MAIN":"CONFLICT","FAST":"CONFLICT","FALLBACK":"CONFLICT","VISION":"CONFLICT"}
        return {"MAIN":"HEALTHY" if state.main_healthy else "NOT_RUNNING","VISION":"HEALTHY" if state.main_healthy else "NOT_RUNNING","FAST":"HEALTHY" if state.fast_healthy else "NOT_RUNNING","FALLBACK":"HEALTHY" if state.fast_healthy else "NOT_RUNNING"}

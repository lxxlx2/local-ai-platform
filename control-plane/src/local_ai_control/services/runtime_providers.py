"""Authoritative provider selection with one-heavy-runtime enforcement."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import threading
import time
import urllib.error

from local_ai_control.services.heavy_process_identity import expected_identity,identity_status,listener_pids,process_snapshot,write_identity
from local_ai_control.services.models import MemoryPreflight,ModelRegistry,ModelRole,QWEN36,QWEN38
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.qwen38_runtime import ContextLimitExceeded,Qwen38Provider,RuntimeUnavailable


class HeavyModelConflict(RuntimeError): pass


class ResourcePreflightDenied(RuntimeUnavailable):
    """Deterministic resource-policy failure with a stable transition stage."""
    def __init__(self,category,reason):
        self.category=category; self.reason=reason
        super().__init__(f"{category}: {reason}")


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

    def _service_pid(self,profile_id):
        """Return one exact launchd job PID or fail closed.

        This parser is used only for initial qwen36 post-title capture.  The
        queried domain, reported label header, plist path, program, and job PID
        must all bind to the one platform-owned launchd job.
        """
        label=self.labels[profile_id]
        domain=f"gui/{self.uid}/{label}"
        result=self.runner(
            ["launchctl","print",domain],capture_output=True,text=True,check=False,
        )
        output=getattr(result,"stdout","")
        if result.returncode or not isinstance(output,str):
            raise HeavyModelConflict("managed launchd service details unavailable")
        lines=output.splitlines()
        if not lines or lines[0].strip()!=f"{domain} = {{":
            raise HeavyModelConflict("managed launchd label proof failed")
        expected_path=self.runtime_root/f"{label}.plist"
        expected_program=self._launch_args(profile_id)[0]
        if not re.search(rf"^\s*path = {re.escape(str(expected_path))}\s*$",output,re.MULTILINE):
            raise HeavyModelConflict("managed launchd plist binding failed")
        if not re.search(rf"^\s*program = {re.escape(expected_program)}\s*$",output,re.MULTILINE):
            raise HeavyModelConflict("managed launchd program binding failed")
        pids=re.findall(r"^\s*pid = ([1-9][0-9]*)\s*$",output,re.MULTILINE)
        if len(pids)!=1:
            raise HeavyModelConflict("managed launchd PID proof is ambiguous")
        return int(pids[0])

    def _validate_managed_plist(self,profile_id):
        """Validate the exact platform plist without following a symlink."""
        label=self.labels[profile_id]
        path=self.runtime_root/f"{label}.plist"
        try:
            if path.parent.resolve()!=self.runtime_root.resolve():
                raise HeavyModelConflict("managed plist escaped runtime root")
            descriptor=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
            try:
                metadata=os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise HeavyModelConflict("managed plist is not a regular file")
                with os.fdopen(descriptor,"rb",closefd=False) as handle:
                    payload=plistlib.load(handle)
            finally:
                os.close(descriptor)
        except HeavyModelConflict:
            raise
        except (OSError,ValueError,TypeError,plistlib.InvalidFileException) as error:
            raise HeavyModelConflict("managed plist validation failed") from error
        if not isinstance(payload,dict):
            raise HeavyModelConflict("managed plist payload is invalid")
        if payload.get("Label")!=label:
            raise HeavyModelConflict("managed plist label mismatch")
        if payload.get("ProgramArguments")!=self._launch_args(profile_id):
            raise HeavyModelConflict("managed plist arguments mismatch")
        if payload.get("WorkingDirectory")!="/Users/jerson/AI":
            raise HeavyModelConflict("managed plist working directory mismatch")
        return path

    def _prove_qwen38_absent_for_omlx_capture(self):
        if self.listeners(self._port(QWEN38.profile_id)):
            raise HeavyModelConflict("Qwen3.8 listener conflicts with oMLX capture")
        qwen38_status,_=identity_status(
            self._identity_path(QWEN38.profile_id),snapshot=self.snapshot,
        )
        if qwen38_status=="MATCH" or self._service_present(QWEN38.profile_id):
            raise HeavyModelConflict("Qwen3.8 runtime conflicts with oMLX capture")

    def _capture_qwen36_posttitle_identity(self,identity,listener_pid):
        """Prove and return the exact observed oMLX post-title identity.

        oMLX uses setproctitle after launch, so its observed executable/argv no
        longer retain the spawn signature.  This one-time capture exception is
        therefore gated by independent launchd PID and immutable plist proof.
        Later ownership remains exact saved ProcessIdentity equality.
        """
        if identity.pid!=listener_pid or not identity.start_identity:
            raise HeavyModelConflict("observed oMLX identity is incomplete")
        self._prove_qwen38_absent_for_omlx_capture()
        existing_status,existing_pid=identity_status(
            self._identity_path(QWEN36.profile_id),snapshot=self.snapshot,
        )
        if existing_status=="INVALID" or (existing_status=="MATCH" and existing_pid!=listener_pid):
            raise HeavyModelConflict("conflicting saved oMLX identity")
        self._validate_managed_plist(QWEN36.profile_id)
        if self._service_pid(QWEN36.profile_id)!=listener_pid:
            raise HeavyModelConflict("launchd job PID does not own oMLX listener")
        # Close the read/validation race before persisting ownership.
        if self.listeners(self._port(QWEN36.profile_id))!=(listener_pid,):
            raise HeavyModelConflict("oMLX listener changed during capture")
        current=self.snapshot(listener_pid)
        if current!=identity:
            raise HeavyModelConflict("oMLX process identity changed during capture")
        # Revalidate all mutable evidence at the commit boundary.  The saved
        # identity must never outlive the proof that authorized its capture.
        self._validate_managed_plist(QWEN36.profile_id)
        if self._service_pid(QWEN36.profile_id)!=listener_pid:
            raise HeavyModelConflict("launchd job changed during oMLX capture")
        self._prove_qwen38_absent_for_omlx_capture()
        return current

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

    def admit_owned_transition(self,current_profile_id,endpoint_probes):
        """Read-only proof that one exact owned runtime may be stopped.

        Admission never authorizes a start.  It proves both managed profiles
        before the current label is controlled, so an unknown second listener
        cannot be hidden by stopping the known runtime first.
        """
        if current_profile_id not in self.labels or set(endpoint_probes)!=set(self.labels):
            raise HeavyModelConflict("complete managed-profile probes are required")
        with self._start_lock:
            evidence={
                profile:self.inspect(profile,endpoint_probes[profile])
                for profile in self.labels
            }
            for item in evidence.values(): self._validate_evidence(item)
            if evidence[current_profile_id].identity_status!="MATCH":
                raise HeavyModelConflict("current heavy runtime is not exactly owned")
            other=[item for profile,item in evidence.items() if profile!=current_profile_id]
            if any(item.identity_status=="MATCH" for item in other):
                raise HeavyModelConflict("second heavy runtime is live")
            return evidence

    def transition_source_state(self,current_profile_id,endpoint_probes):
        """Classify a failed endpoint's exact process state without control."""
        if current_profile_id not in self.labels or set(endpoint_probes)!=set(self.labels):
            raise HeavyModelConflict("complete managed-profile probes are required")
        with self._start_lock:
            evidence={
                profile:self.inspect(profile,endpoint_probes[profile])
                for profile in self.labels
            }
            for item in evidence.values(): self._validate_evidence(item)
            other=[item for profile,item in evidence.items() if profile!=current_profile_id]
            if any(item.identity_status=="MATCH" for item in other):
                raise HeavyModelConflict("second heavy runtime is live")
            return "OWNED" if evidence[current_profile_id].identity_status=="MATCH" else "ABSENT"

    def prove_all_absent(self,endpoint_probes):
        """Read-only proof that neither managed heavy runtime is resident."""
        if set(endpoint_probes)!=set(self.labels):
            raise HeavyModelConflict("complete managed-profile probes are required")
        with self._start_lock:
            evidence={
                profile:self.inspect(profile,endpoint_probes[profile])
                for profile in self.labels
            }
            for item in evidence.values():
                self._validate_evidence(item)
                if item.identity_status=="MATCH":
                    raise HeavyModelConflict("heavy runtime remains live before resource gate")
            return evidence

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
        if identity is None:
            raise HeavyModelConflict("listener process does not match managed profile")
        if profile_id==QWEN36.profile_id:
            self._prove_qwen38_absent_for_omlx_capture()
        if not expected_identity(identity,executable,argv):
            if profile_id!=QWEN36.profile_id:
                raise HeavyModelConflict("listener process does not match managed profile")
            identity=self._capture_qwen36_posttitle_identity(identity,pids[0])
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
    def __init__(self,registry=None,*,main=None,fast=None,preflight=None,lifecycle=None,sleep=time.sleep,
                 post_stop_preflight_attempts=30,post_stop_preflight_interval=1):
        self.registry=registry or ModelRegistry()
        main_alias=self.registry.alias(ModelRole.MAIN)
        self.main=main or Qwen38Provider(max_context_tokens=main_alias.max_context_tokens)
        self.fast=fast or OmlxProvider()
        self.preflight=preflight or MemoryPreflight(); self.lifecycle=lifecycle or LaunchdHeavyRuntimeLifecycle(); self.sleep=sleep; self.lock=threading.RLock()
        self.post_stop_preflight_attempts=max(1,int(post_stop_preflight_attempts))
        self.post_stop_preflight_interval=max(0,float(post_stop_preflight_interval))

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

    def _endpoint_probes(self):
        return {
            QWEN38.profile_id:lambda:_healthy(self.main),
            QWEN36.profile_id:lambda:_healthy(self.fast),
        }

    def _preflight(self,target):
        # Every start is authorized from a snapshot taken after competing
        # runtime absence.  Owned memory is never a waiver for a second model.
        return self.preflight.check(target.expected_memory_gib or 0)

    def _admit_owned_transition(self,current,target):
        if hasattr(self.lifecycle,"admit_owned_transition"):
            self.lifecycle.admit_owned_transition(
                current.profile_id,self._endpoint_probes(),
            )
        if hasattr(self.preflight,"admit_owned_transition"):
            check=self.preflight.admit_owned_transition(target.expected_memory_gib or 0)
        else:
            # Legacy dependency-injected test doubles contain no real resource
            # probe.  Production MemoryPreflight always uses the explicit
            # admission method above.
            check=self.preflight.check(target.expected_memory_gib or 0,
                                       owned_reclaimable_gib=current.expected_memory_gib or 0)
        if not check.allowed:
            raise ResourcePreflightDenied("TRANSITION_ADMISSION_DENIED",check.reason)

    def _authorize_cold_start(self,target):
        check=self._preflight(target)
        if not check.allowed:
            raise ResourcePreflightDenied("COLD_START_RESOURCE_PREFLIGHT_DENIED",check.reason)

    def _prove_all_absent(self):
        if hasattr(self.lifecycle,"prove_all_absent"):
            self.lifecycle.prove_all_absent(self._endpoint_probes())

    def _wait_for_post_stop_preflight(self,target,*,category="POST_STOP_RESOURCE_PREFLIGHT_DENIED"):
        last=None
        for attempt in range(self.post_stop_preflight_attempts):
            last=self._preflight(target)
            if last.allowed:
                return last
            if last.reason in {"MEMORY_PRESSURE_CRITICAL","SWAP_RUNAWAY"}:
                break
            if attempt+1<self.post_stop_preflight_attempts:
                self.sleep(self.post_stop_preflight_interval)
        raise ResourcePreflightDenied(category,last.reason if last else "NO_RESOURCE_SNAPSHOT")

    def _restore_previous_after_failure(self,current,current_provider):
        """Resource-gated non-recursive restore; failure leaves zero-heavy."""
        self._prove_all_absent()
        try:
            self._wait_for_post_stop_preflight(
                current,category="PREVIOUS_RUNTIME_RESTORE_PREFLIGHT_DENIED",
            )
        except ResourcePreflightDenied as error:
            raise HeavyModelConflict(str(error)) from error
        restore_started=False
        try:
            restore_started=True
            self._start_and_capture(current,current_provider)
        except Exception as restore_error:
            if restore_started:
                try:
                    self._stop_and_prove(current,current_provider)
                except Exception as cleanup_error:
                    raise HeavyModelConflict(
                        "previous runtime restore failed and could not be confirmed down"
                    ) from cleanup_error
            raise HeavyModelConflict(
                "previous runtime restore failed safely; zero-heavy-runtime retained"
            ) from restore_error

    def _switch(self,target,provider,*,current=None,current_provider=None):
        target_started=False; current_stop_proven=False
        try:
            if current and current_provider:
                self._admit_owned_transition(current,target)
                self._stop_and_prove(current,current_provider)
                current_stop_proven=True
                self._prove_all_absent()
                self._wait_for_post_stop_preflight(target)
            else:
                self._authorize_cold_start(target)
            # Treat start as potentially partial even when it raises. Cleanup
            # must prove the target down before any rollback is attempted.
            target_started=True; self._start_and_capture(target,provider)
        except Exception:
            if target_started:
                try: self._stop_and_prove(target,provider)
                except Exception as cleanup_error:
                    raise HeavyModelConflict("failed target could not be confirmed down") from cleanup_error
            if current and current_provider and current_stop_proven and not _healthy(current_provider):
                self._restore_previous_after_failure(current,current_provider)
            raise

    def _fallback_target(self,state):
        if not self._eligible(ModelRole.FALLBACK,QWEN36.profile_id):
            raise RuntimeUnavailable("FALLBACK is not eligible")
        if state.fast_healthy: return self.fast
        self._switch(QWEN36,self.fast,current=QWEN38 if state.main_healthy else None,
                     current_provider=self.main if state.main_healthy else None)
        return self.fast

    @staticmethod
    def _execution_roles(task_type):
        normalized=str(task_type).strip().upper()
        if normalized in {"CHAT","MAIN"}:
            return (
                ModelRole.MAIN,
                ModelRole.FALLBACK,
                ModelRole.FAST,
            )
        mapping={
            "FAST":(ModelRole.FAST,),
            "CHAT_FAST":(ModelRole.FAST,),
            "CODE":(ModelRole.CODE,),
            "REVIEW":(ModelRole.REVIEW,),
            "VISION":(ModelRole.VISION,),
            "VIDEO_UNDERSTANDING":(
                ModelRole.VIDEO_UNDERSTANDING,
            ),
            "DEEP_REASONING":(ModelRole.DEEP,),
        }
        roles=mapping.get(normalized)
        if roles is None:
            raise RuntimeUnavailable(
                "unsupported task type for exact heavy runtime"
            )
        return roles

    def _prove_healthy_target_owned(self,profile_id):
        """A healthy endpoint is not ownership proof."""
        if not hasattr(
            self.lifecycle,
            "transition_source_state",
        ):
            raise HeavyModelConflict(
                "exact ownership proof unavailable"
            )
        source=self.lifecycle.transition_source_state(
            profile_id,
            self._endpoint_probes(),
        )
        if source!="OWNED":
            raise HeavyModelConflict(
                "healthy target runtime is not exactly owned"
            )

    @contextmanager
    def exact_profile_session(self,profile_id,task_type="CHAT"):
        """Use one exact planner-selected heavy profile.

        This is a policy-neutral physical lifecycle primitive.
        It does not accept a WorkloadRoutingPlan and does not
        decide which model should run.

        Healthy resident targets are reused only after exact
        ownership proof. Starts and switches continue through
        the existing resource/ownership gated _switch path.
        """
        with self.lock:
            roles=self._execution_roles(task_type)
            if not any(
                self._eligible(role,profile_id)
                for role in roles
            ):
                raise RuntimeUnavailable(
                    "profile is not eligible for execution task"
                )

            state=self.state()

            if profile_id==QWEN38.profile_id:
                target=QWEN38
                provider=self.main
                target_healthy=state.main_healthy
                current=QWEN36 if state.fast_healthy else None
                current_provider=(
                    self.fast if state.fast_healthy else None
                )
            elif profile_id==QWEN36.profile_id:
                target=QWEN36
                provider=self.fast
                target_healthy=state.fast_healthy
                current=QWEN38 if state.main_healthy else None
                current_provider=(
                    self.main if state.main_healthy else None
                )
            else:
                raise RuntimeUnavailable(
                    "unsupported heavy execution profile"
                )

            if target_healthy:
                self._prove_healthy_target_owned(
                    profile_id
                )
            else:
                self._switch(
                    target,
                    provider,
                    current=current,
                    current_provider=current_provider,
                )

            yield provider

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
            # MAIN may be unhealthy while its exact managed process still owns
            # Metal resources.  Use the same owned transition admission, stop,
            # reclaim-settle, and fresh final start gate as every other switch.
            source_state="OWNED"
            if hasattr(self.lifecycle,"transition_source_state"):
                source_state=self.lifecycle.transition_source_state(
                    QWEN38.profile_id,self._endpoint_probes(),
                )
            if source_state=="OWNED":
                self._switch(QWEN36,self.fast,current=QWEN38,current_provider=self.main)
            elif source_state=="ABSENT":
                self._switch(QWEN36,self.fast)
            else:
                raise HeavyModelConflict("unknown failover source state")
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

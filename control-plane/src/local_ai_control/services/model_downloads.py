"""Manual, parallel, resumable Hugging Face model download manager."""
from __future__ import annotations

from concurrent.futures import Future,ThreadPoolExecutor
from dataclasses import asdict,dataclass
from datetime import UTC,datetime
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Callable

from local_ai_control.services.heavy_process_identity import ProcessIdentity,expected_spawn_identity,identity_status,process_snapshot,read_identity,write_identity

REVISION_RE=re.compile(r"^[0-9a-f]{40}$")
DEFAULT_ROOT=Path("/Users/jerson/AI")
DEFAULT_CONFIG=DEFAULT_ROOT/"config/model-download-queue-v0.1.json"
DEFAULT_RUNTIME=DEFAULT_ROOT/"runtime/model-downloads"
DEFAULT_MODELS=DEFAULT_ROOT/"models"
LABEL="local-ai.model-download-queue"
HF_EXECUTABLE=Path("/Users/jerson/AI/runtime/qwen38-venv/bin/hf")
QUARANTINE_SCHEMA_VERSION="0.1"
QUARANTINE_REASON="WORKER_CLEANUP_UNCONFIRMED"
SAFE_MODEL_ID_RE=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class DownloadSpec:
    id: str; role: str; repo: str; revision: str; local_dir: Path
    expected_bytes: int; license: str; runtime: str; include: tuple[str,...]=()


@dataclass(frozen=True)
class QueueConfig:
    models: tuple[DownloadSpec,...]; max_attempts: int; reserve_bytes: int; default_parallel: int=3


@dataclass(frozen=True)
class StorageBytes:
    payload_bytes: int; partial_cache_bytes: int


class WorkerIdentityConflict(RuntimeError):
    """A spawned worker cannot be proven to be the exact fixed command."""


class WorkerCleanupUnconfirmed(WorkerIdentityConflict):
    """An unverifiable spawned child could not be safely stopped and reaped."""


class WorkerQuarantinePersistenceFailure(RuntimeError):
    """No durable cleanup evidence could be written; manager must fail hard."""


def utc_now(): return datetime.now(UTC).isoformat()


def exact_pid_exists(pid):
    """Read-only existence probe for one PID; never sends a signal."""
    result=subprocess.run(["/bin/ps","-p",str(int(pid)),"-o","pid="],capture_output=True,text=True,shell=False,timeout=5,check=False)
    if result.returncode not in {0,1}: raise RuntimeError("exact PID existence check failed")
    return result.returncode==0 and result.stdout.strip()==str(int(pid))


def load_queue_config(path: Path=DEFAULT_CONFIG,*,models_root: Path=DEFAULT_MODELS) -> QueueConfig:
    payload=json.loads(path.read_text())
    if payload.get("serial_only") not in {False,None}: raise ValueError("parallel download manager required")
    maximum=int(payload.get("max_attempts",0)); reserve=int(payload.get("reserve_bytes",0)); parallel=int(payload.get("default_parallel",3))
    if not 1<=maximum<=5 or reserve<0 or not 2<=parallel<=5: raise ValueError("invalid bounded queue settings")
    root=models_root.resolve(); seen=set(); models=[]
    for raw in payload.get("models",[]):
        local_dir=Path(raw["local_dir"]).resolve()
        if local_dir==root or root not in local_dir.parents: raise ValueError("model local_dir escapes models root")
        if raw["id"] in seen or not SAFE_MODEL_ID_RE.fullmatch(raw["id"]) or not REVISION_RE.fullmatch(raw["revision"]): raise ValueError("invalid id, duplicate id, or unpinned revision")
        if "/" not in raw["repo"] or int(raw["expected_bytes"])<=0: raise ValueError("invalid model metadata")
        includes=tuple(raw.get("include",()))
        if any(not item or item.startswith("/") or ".." in item for item in includes): raise ValueError("unsafe include pattern")
        seen.add(raw["id"]); models.append(DownloadSpec(raw["id"],raw["role"],raw["repo"],raw["revision"],local_dir,int(raw["expected_bytes"]),raw["license"],raw["runtime"],includes))
    if not models: raise ValueError("empty queue")
    return QueueConfig(tuple(models),maximum,reserve,parallel)


def storage_bytes(path: Path) -> StorageBytes:
    payload=partial=0
    if not path.exists(): return StorageBytes(0,0)
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                size=item.stat().st_size; relative=item.relative_to(path)
                if item.name==".local-ai-download-complete.json": continue
                if item.name.endswith(".incomplete"): partial+=size
                elif ".cache" not in relative.parts: payload+=size
        except FileNotFoundError: continue
    return StorageBytes(payload,partial)


def directory_bytes(path: Path) -> int: return storage_bytes(path).payload_bytes


class ModelDownloadQueue:
    def __init__(self,config: QueueConfig,runtime_dir: Path=DEFAULT_RUNTIME,*,parallel_limit: int|None=None,downloader: Callable[[DownloadSpec,Path],int]|None=None,sleeper: Callable[[float],None]=time.sleep,waiter: Callable[[float],bool]|None=None,snapshot=process_snapshot,process_exists=exact_pid_exists,disk_usage=shutil.disk_usage):
        self.config=config; self.runtime_dir=Path(runtime_dir); self.parallel_limit=parallel_limit or config.default_parallel
        if not 2<=self.parallel_limit<=5: raise ValueError("parallel limit must be 2..5")
        self.state_path=self.runtime_dir/"state.json"; self.log_path=self.runtime_dir/"manager.log"; self.lock_path=self.runtime_dir/"manager.lock"
        self.pid_path=self.runtime_dir/"manager.pid"; self.identity_path=self.runtime_dir/"manager.identity.json"; self.worker_root=self.runtime_dir/"workers"; self.quarantine_root=self.runtime_dir/"quarantine"
        self.downloader=downloader; self.sleeper=sleeper; self.snapshot=snapshot; self.process_exists=process_exists; self.disk_usage=disk_usage
        self.stop_event=threading.Event(); self.state_lock=threading.RLock(); self.statuses={}; self.active_processes={}; self.active_reservations={}; self.state={}
        self.quarantine_status={"quarantine_count":0,"quarantined_model_ids":[]}; self.cleanup_blockers=[]
        self.waiter=waiter or self.stop_event.wait

    def _private_runtime(self):
        self.runtime_dir.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(self.runtime_dir,0o700)
        self.worker_root.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(self.worker_root,0o700)
        self.quarantine_root.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(self.quarantine_root,0o700)

    def _atomic_json(self,path,payload):
        self._private_runtime(); temporary=Path(path).with_name(f".{Path(path).name}.{os.getpid()}.{threading.get_ident()}.tmp")
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,"w") as handle: json.dump(payload,handle,ensure_ascii=False,indent=2); handle.write("\n")
        os.replace(temporary,path); os.chmod(path,0o600)

    def _quarantine_path(self,model_id):
        if not isinstance(model_id,str) or not SAFE_MODEL_ID_RE.fullmatch(model_id):
            raise ValueError("unsafe quarantine model id")
        return self.quarantine_root/f"{model_id}.json"

    def _quarantine_payload(self,spec,pid,observed_identity):
        observed=None
        if observed_identity is not None:
            observed=asdict(observed_identity); observed["argv"]=list(observed_identity.argv)
        return {
            "schema_version":QUARANTINE_SCHEMA_VERSION,
            "model_id":spec.id,
            "pid":int(pid),
            "created_at":utc_now(),
            "reason":QUARANTINE_REASON,
            "identity_state":"OBSERVED" if observed_identity is not None else "UNAVAILABLE",
            "observed_identity":observed,
        }

    def _write_quarantine(self,spec,pid,observed_identity):
        payload=self._quarantine_payload(spec,pid,observed_identity)
        self._atomic_json(self._quarantine_path(spec.id),payload)
        with self.state_lock:
            model_ids=set(self.quarantine_status["quarantined_model_ids"]); model_ids.add(spec.id)
            self.quarantine_status={"quarantine_count":len(model_ids),"quarantined_model_ids":sorted(model_ids)}

    def _validated_quarantine_payload(self,raw,*,filename=None):
        expected={"schema_version","model_id","pid","created_at","reason","identity_state","observed_identity"}
        if not isinstance(raw,dict) or set(raw)!=expected:
            raise ValueError("invalid quarantine schema")
        model_ids={spec.id for spec in self.config.models}
        if (raw["schema_version"]!=QUARANTINE_SCHEMA_VERSION or raw["reason"]!=QUARANTINE_REASON or
                raw["model_id"] not in model_ids or not SAFE_MODEL_ID_RE.fullmatch(raw["model_id"]) or
                (filename is not None and filename!=f"{raw['model_id']}.json") or
                not isinstance(raw["pid"],int) or isinstance(raw["pid"],bool) or raw["pid"]<=0):
            raise ValueError("invalid quarantine metadata")
        created=datetime.fromisoformat(raw["created_at"])
        if created.tzinfo is None or created.utcoffset() is None:
            raise ValueError("quarantine timestamp must include timezone")
        if raw["identity_state"]=="UNAVAILABLE":
            if raw["observed_identity"] is not None: raise ValueError("unavailable identity must be null")
            observed=None
        elif raw["identity_state"]=="OBSERVED":
            item=raw["observed_identity"]
            if not isinstance(item,dict) or set(item)!={"pid","executable","argv","start_identity"}:
                raise ValueError("invalid observed identity schema")
            if (item["pid"]!=raw["pid"] or not isinstance(item["executable"],str) or not Path(item["executable"]).is_absolute() or
                    not isinstance(item["argv"],list) or not item["argv"] or not all(isinstance(value,str) and value for value in item["argv"]) or
                    not isinstance(item["start_identity"],str) or not item["start_identity"]):
                raise ValueError("invalid observed process identity")
            observed=ProcessIdentity(item["pid"],item["executable"],tuple(item["argv"]),item["start_identity"])
        else:
            raise ValueError("invalid quarantine identity state")
        return raw,observed

    def _validated_quarantine(self,path):
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode)!=0o600:
            raise ValueError("unsafe quarantine record file")
        return self._validated_quarantine_payload(json.loads(path.read_text()),filename=path.name)

    def _quarantine_is_unresolved(self,raw,observed):
        current=self.snapshot(raw["pid"])
        if current is not None and not isinstance(current,ProcessIdentity):
            raise ValueError("invalid current process snapshot")
        if current is None:
            return bool(self.process_exists(raw["pid"]))
        if observed is None:
            return True
        return current==observed

    def _scan_quarantine(self,*,clear_resolved=False):
        blocked=[]; fallback_blockers=[]
        if self.quarantine_root.exists():
            try: entries=sorted(self.quarantine_root.iterdir())
            except OSError: entries=None
        else: entries=[]
        if entries is None:
            blocked.append("UNKNOWN")
            entries=[]
        for path in entries:
            try:
                raw,observed=self._validated_quarantine(path)
                unresolved=self._quarantine_is_unresolved(raw,observed)
                if unresolved:
                    blocked.append(raw["model_id"]); continue
                if clear_resolved:
                    path.unlink()
            except Exception:
                candidate=path.stem if SAFE_MODEL_ID_RE.fullmatch(path.stem) else "UNKNOWN"
                blocked.append(candidate)
        try:
            state=json.loads(self.state_path.read_text())
            if not isinstance(state,dict): raise ValueError("invalid manager state")
        except FileNotFoundError:
            state={}
        except (OSError,json.JSONDecodeError,TypeError,ValueError):
            blocked.append("UNKNOWN"); state={"cleanup_blockers":[{"invalid":True}]}
        raw_blockers=state.get("cleanup_blockers",[])
        if not isinstance(raw_blockers,list):
            blocked.append("UNKNOWN"); fallback_blockers=[{"invalid":True}]
        else:
            for raw in raw_blockers:
                try:
                    payload,observed=self._validated_quarantine_payload(raw)
                    if self._quarantine_is_unresolved(payload,observed):
                        blocked.append(payload["model_id"]); fallback_blockers.append(payload)
                except Exception:
                    candidate=raw.get("model_id") if isinstance(raw,dict) and isinstance(raw.get("model_id"),str) and SAFE_MODEL_ID_RE.fullmatch(raw["model_id"]) else "UNKNOWN"
                    blocked.append(candidate); fallback_blockers.append(raw if isinstance(raw,dict) else {"invalid":True})
        prior_count=state.get("quarantine_count",0)
        if (state.get("state")=="BLOCKED_WORKER_QUARANTINE" and isinstance(prior_count,int) and not isinstance(prior_count,bool) and prior_count>0 and
                not raw_blockers and not entries):
            blocked.append("UNKNOWN"); fallback_blockers.append({"invalid":True})
        status={"quarantine_count":len(blocked),"quarantined_model_ids":sorted(set(blocked))}
        return status,fallback_blockers

    def _log(self,event,model_id="-"):
        self._private_runtime(); descriptor=os.open(self.log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(descriptor,"a") as handle: handle.write(f"{utc_now()} | {event} | {model_id}\n")

    def _marker_path(self,spec): return spec.local_dir/".local-ai-download-complete.json"

    def _payload_manifest(self,spec):
        files=[]
        if not spec.local_dir.exists(): return files
        for item in sorted(spec.local_dir.rglob("*")):
            if not item.is_file() or item.is_symlink(): continue
            relative=item.relative_to(spec.local_dir)
            if ".cache" in relative.parts or item.name.endswith(".incomplete") or item.name==".local-ai-download-complete.json": continue
            files.append({"path":relative.as_posix(),"size":item.stat().st_size})
        return files

    def _snapshot_valid(self,spec):
        for index in spec.local_dir.rglob("*.safetensors.index.json"):
            try: weights=json.loads(index.read_text()).get("weight_map",{})
            except (json.JSONDecodeError,OSError): return False
            if not weights or any(not (index.parent/name).is_file() for name in set(weights.values())): return False
        return bool(self._payload_manifest(spec)) and storage_bytes(spec.local_dir).payload_bytes>=int(spec.expected_bytes*.98)

    def _is_complete(self,spec):
        try: marker=json.loads(self._marker_path(spec).read_text())
        except (FileNotFoundError,json.JSONDecodeError,OSError): return False
        if marker.get("repo")!=spec.repo or marker.get("revision")!=spec.revision or marker.get("expected_bytes")!=spec.expected_bytes: return False
        manifest=marker.get("files")
        if not isinstance(manifest,list) or not manifest: return False
        for entry in manifest:
            if not isinstance(entry,dict) or set(entry)!={"path","size"} or not isinstance(entry["path"],str) or not isinstance(entry["size"],int): return False
            candidate=(spec.local_dir/entry["path"]).resolve()
            if spec.local_dir.resolve() not in candidate.parents or not candidate.is_file() or candidate.stat().st_size!=entry["size"]: return False
        return manifest==self._payload_manifest(spec) and self._snapshot_valid(spec)

    def _write_marker(self,spec):
        self._atomic_json(self._marker_path(spec),{"repo":spec.repo,"revision":spec.revision,"expected_bytes":spec.expected_bytes,"completed_at":utc_now(),"files":self._payload_manifest(spec)})

    def _remaining(self,spec):
        sizes=storage_bytes(spec.local_dir); return max(spec.expected_bytes-sizes.payload_bytes-sizes.partial_cache_bytes,0)

    def _can_reserve(self,spec):
        parent=spec.local_dir.parent if spec.local_dir.parent.exists() else DEFAULT_ROOT
        return self.disk_usage(parent).free>=self.config.reserve_bytes+sum(self.active_reservations.values())+self._remaining(spec)

    def _set(self,spec,state,**values):
        with self.state_lock:
            item=self.statuses[spec.id]; item.update(values); item["state"]=state; item["status"]=state
            self._snapshot_state("RUNNING" if not self.stop_event.is_set() else "STOPPING")

    def _snapshot_state(self,manager_state):
        with self.state_lock:
            models={key:dict(value) for key,value in self.statuses.items()}; active=[key for key,value in models.items() if value.get("state")=="DOWNLOADING"]
            payload={"schema_version":"0.2","state":manager_state,"manager_pid":os.getpid(),"pid":os.getpid(),"updated_at":utc_now(),"parallel_limit":self.parallel_limit,"active_count":len(active),"active_model_ids":active,"models":models,"completed":[key for key,value in models.items() if value.get("state")=="COMPLETED"],"failed":[key for key,value in models.items() if value.get("state")=="FAILED"],"pending":[key for key,value in models.items() if value.get("state") in {"PENDING","RETRY_WAIT","PAUSED"}],"cleanup_blockers":self.cleanup_blockers,**self.quarantine_status}
            self.state=payload; self._atomic_json(self.state_path,payload)

    def _command(self,spec):
        command=[str(HF_EXECUTABLE),"download",spec.repo,"--revision",spec.revision,"--local-dir",str(spec.local_dir)]
        for pattern in spec.include: command.extend(["--include",pattern])
        return command

    def _owned_worker(self,spec,pid):
        saved=read_identity(self.worker_root/f"{spec.id}.identity.json")
        current=self.snapshot(pid)
        try:
            return bool(saved and current and saved.pid==pid and current==saved and expected_spawn_identity(current,self._command(spec)))
        except (OSError,ValueError):
            return False

    def _terminate_owned(self,spec,process):
        if process.poll() is not None: return
        if not self._owned_worker(spec,process.pid): self._log("WORKER_IDENTITY_MISMATCH_NOT_TERMINATED",spec.id); return
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if self._owned_worker(spec,process.pid): process.kill(); process.wait(timeout=5)

    @staticmethod
    def _reap_unverifiable_child(process):
        """Stop and reap only the direct Popen child, with bounded waits.

        Popen.poll()/wait() use waitpid for this child.  A non-None poll means
        it has already exited, so no PID signal is issued.  Escalation to kill
        occurs only after terminate timed out and the same Popen child still
        reports alive.
        """
        def reap_if_exited():
            try:
                if process.poll() is None: return False
                process.wait(timeout=0); return True
            except (OSError,subprocess.SubprocessError):
                return False

        if reap_if_exited(): return True
        try:
            process.terminate()
        except OSError:
            return reap_if_exited()
        try:
            process.wait(timeout=10); return True
        except subprocess.TimeoutExpired:
            pass
        except (OSError,subprocess.SubprocessError):
            return reap_if_exited()
        if reap_if_exited(): return True
        try:
            process.kill()
        except OSError:
            return reap_if_exited()
        try:
            process.wait(timeout=5); return True
        except (OSError,subprocess.SubprocessError):
            return reap_if_exited()

    def _download_process(self,spec,log_path):
        command=self._command(spec); environment=os.environ.copy(); environment.update({"HF_HUB_DISABLE_XET":"1","HF_HUB_DOWNLOAD_TIMEOUT":"120","HF_HUB_ETAG_TIMEOUT":"30"})
        spec.local_dir.mkdir(parents=True,exist_ok=True); descriptor=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(descriptor,"a") as handle:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,env=environment,shell=False); identity=None
            for _ in range(20):
                identity=self.snapshot(process.pid)
                if identity is not None: break
                if process.poll() is not None: break
                time.sleep(0.05)
            try: identity_matches=bool(identity and process.pid==identity.pid and expected_spawn_identity(identity,command))
            except (OSError,ValueError): identity_matches=False
            if not identity_matches:
                if not self._reap_unverifiable_child(process):
                    payload=self._quarantine_payload(spec,process.pid,identity)
                    try: self._write_quarantine(spec,process.pid,identity)
                    except Exception as error:
                        with self.state_lock:
                            model_ids=set(self.quarantine_status["quarantined_model_ids"]); model_ids.add(spec.id)
                            self.quarantine_status={"quarantine_count":len(model_ids),"quarantined_model_ids":sorted(model_ids)}
                            self.cleanup_blockers=[*self.cleanup_blockers,payload]
                        try: self._snapshot_state("BLOCKED_WORKER_QUARANTINE")
                        except Exception as fallback_error:
                            raise WorkerQuarantinePersistenceFailure("no durable worker cleanup blocker could be written") from fallback_error
                        raise WorkerCleanupUnconfirmed("primary quarantine failed; durable state blocker persisted") from error
                    raise WorkerCleanupUnconfirmed("unverifiable worker cleanup was not confirmed")
                raise WorkerIdentityConflict("worker identity validation failed")
            identity_path=self.worker_root/f"{spec.id}.identity.json"; write_identity(identity_path,identity)
            with self.state_lock:
                self.active_processes[spec.id]=process; self.statuses[spec.id].update({"worker_pid":process.pid,"worker_identity":str(identity_path)}); self._snapshot_state("RUNNING")
            while process.poll() is None:
                if self.waiter(0.2): self._terminate_owned(spec,process); break
            code=process.wait()
        with self.state_lock: self.active_processes.pop(spec.id,None); self.statuses[spec.id]["worker_pid"]=None
        return code

    def _worker(self,spec):
        log_path=self.runtime_dir/f"{spec.id}.log"
        release_reservation=True
        try:
            for attempt in range(1,self.config.max_attempts+1):
                if self.stop_event.is_set(): self._set(spec,"PAUSED",last_error_category=None); return "PAUSED"
                self._set(spec,"DOWNLOADING",retry_count=attempt-1,started_at=self.statuses[spec.id].get("started_at") or utc_now(),last_error_category=None)
                try: code=self.downloader(spec,log_path) if self.downloader else self._download_process(spec,log_path)
                except WorkerQuarantinePersistenceFailure:
                    release_reservation=False; self.stop_event.set()
                    with self.state_lock:
                        self.statuses[spec.id].update({"state":"BLOCKED_WORKER_QUARANTINE","status":"BLOCKED_WORKER_QUARANTINE","exit_code":125,"finished_at":None,"last_error_category":"QUARANTINE_PERSISTENCE_FAILURE"})
                    raise
                except WorkerCleanupUnconfirmed:
                    release_reservation=False; self.stop_event.set()
                    self._set(spec,"PAUSED",exit_code=125,finished_at=None,last_error_category="WORKER_CLEANUP_UNCONFIRMED")
                    self._log("WORKER_CLEANUP_UNCONFIRMED_MANAGER_STOP",spec.id); return "PAUSED"
                except WorkerIdentityConflict:
                    self._set(spec,"FAILED",exit_code=125,finished_at=utc_now(),last_error_category="WORKER_IDENTITY_CONFLICT")
                    self._log("WORKER_IDENTITY_CONFLICT_NO_RETRY",spec.id); return "FAILED"
                except Exception as error: code=125; category=type(error).__name__.upper()
                else: category=None if code==0 else "DOWNLOAD_EXIT"
                if self.stop_event.is_set(): self._set(spec,"PAUSED",exit_code=code,last_error_category=None); return "PAUSED"
                if code==0 and self._snapshot_valid(spec):
                    self._write_marker(spec); self._set(spec,"COMPLETED",exit_code=0,finished_at=utc_now(),last_error_category=None); self._log("DOWNLOAD_COMPLETED",spec.id); return "COMPLETED"
                if attempt<self.config.max_attempts:
                    self._set(spec,"RETRY_WAIT",exit_code=code,last_error_category=category or "SNAPSHOT_INVALID"); self._log(f"DOWNLOAD_RETRY_{attempt}",spec.id)
                    if self.waiter((30,120,300)[min(attempt-1,2)]): self._set(spec,"PAUSED",last_error_category=None); return "PAUSED"
            self._set(spec,"FAILED",exit_code=code,finished_at=utc_now(),last_error_category=category or "SNAPSHOT_INVALID"); self._log("DOWNLOAD_FAILED",spec.id); return "FAILED"
        finally:
            if release_reservation:
                with self.state_lock: self.active_reservations.pop(spec.id,None)

    def request_stop(self,*_args): self.stop_event.set(); self._log("STOP_REQUESTED")

    def _manager_identity(self):
        identity=self.snapshot(os.getpid())
        if identity is None: identity=ProcessIdentity(os.getpid(),str(Path(sys.executable).resolve()),tuple([sys.executable,*sys.argv]),f"fallback-{time.time_ns()}")
        write_identity(self.identity_path,identity); descriptor=os.open(self.pid_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(descriptor,"w") as handle: handle.write(f"{os.getpid()}\n")

    def run(self):
        self._private_runtime(); lock=self.lock_path.open("a+")
        try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: self._log("SINGLETON_ALREADY_RUNNING"); lock.close(); return "ALREADY_RUNNING"
        previous_handlers={}
        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGTERM,signal.SIGINT): previous_handlers[signum]=signal.signal(signum,self.request_stop)
            self.statuses={spec.id:{"state":"COMPLETED" if self._is_complete(spec) else "PENDING","status":"COMPLETED" if self._is_complete(spec) else "PENDING","repo":spec.repo,"local_dir":str(spec.local_dir),"worker_pid":None,"worker_identity":None,"retry_count":0,"exit_code":None,"started_at":None,"finished_at":None,"last_error_category":None} for spec in self.config.models}
            self.quarantine_status,self.cleanup_blockers=self._scan_quarantine(clear_resolved=True)
            if self.quarantine_status["quarantine_count"]:
                self._snapshot_state("BLOCKED_WORKER_QUARANTINE"); self._log("BLOCKED_WORKER_QUARANTINE")
                return "BLOCKED_WORKER_QUARANTINE"
            self._manager_identity()
            self._snapshot_state("RUNNING"); self._log("MANAGER_STARTED"); futures: dict[Future,DownloadSpec]={}
            with ThreadPoolExecutor(max_workers=self.parallel_limit,thread_name_prefix="model-download") as pool:
                while True:
                    for future,spec in list(futures.items()):
                        if future.done(): future.result(); futures.pop(future,None)
                    if self.stop_event.is_set():
                        for spec in self.config.models:
                            if self.statuses[spec.id]["state"] in {"PENDING","RETRY_WAIT"}: self.statuses[spec.id].update({"state":"PAUSED","status":"PAUSED"})
                        self._snapshot_state("STOPPING")
                        if not futures: break
                        self.stop_event.wait(0.1); continue
                    pending=[spec for spec in self.config.models if self.statuses[spec.id]["state"]=="PENDING"]; started=False
                    for spec in pending:
                        if len(futures)>=self.parallel_limit: break
                        if spec.id in self.active_reservations: continue
                        if self._is_complete(spec): self._set(spec,"COMPLETED",finished_at=utc_now()); continue
                        if not self._can_reserve(spec): continue
                        self.active_reservations[spec.id]=self._remaining(spec); futures[pool.submit(self._worker,spec)]=spec; started=True
                    terminal=all(value["state"] in {"COMPLETED","FAILED"} for value in self.statuses.values())
                    if terminal and not futures: break
                    if pending and not futures and not started:
                        for spec in pending: self._set(spec,"FAILED",finished_at=utc_now(),last_error_category="INSUFFICIENT_DISK")
                        break
                    time.sleep(0.1)
            final=("BLOCKED_WORKER_QUARANTINE" if self.quarantine_status["quarantine_count"] else
                   ("PAUSED" if self.stop_event.is_set() else ("COMPLETED_WITH_FAILURES" if any(value["state"]=="FAILED" for value in self.statuses.values()) else "COMPLETED")))
            self._snapshot_state(final); self._log(final); return final
        finally:
            self.pid_path.unlink(missing_ok=True)
            for signum,handler in previous_handlers.items(): signal.signal(signum,handler)
            fcntl.flock(lock.fileno(),fcntl.LOCK_UN); lock.close()


def _state_file(runtime_dir):
    try: return json.loads((Path(runtime_dir)/"state.json").read_text())
    except (FileNotFoundError,json.JSONDecodeError,OSError): return {}


def _verified_identity(path,snapshot=process_snapshot):
    status,pid=identity_status(Path(path),snapshot=snapshot); return status=="MATCH",status,pid


def _verified_worker_identity(path,command,snapshot=process_snapshot):
    saved=read_identity(Path(path))
    if saved is None: return False,"MISSING_OR_INVALID",None
    current=snapshot(saved.pid)
    if current is None: return False,"DEAD",saved.pid
    if current!=saved: return False,"MISMATCH",saved.pid
    try: command_matches=expected_spawn_identity(current,command)
    except (OSError,ValueError): command_matches=False
    if not command_matches:
        return False,"COMMAND_MISMATCH",saved.pid
    return True,"MATCH",saved.pid


def status_snapshot(config: QueueConfig|None=None,runtime_dir: Path=DEFAULT_RUNTIME,*,snapshot=process_snapshot,process_exists=exact_pid_exists):
    config=config or load_queue_config(); runtime_dir=Path(runtime_dir); old=_state_file(runtime_dir)
    verifier=ModelDownloadQueue(config,runtime_dir,snapshot=snapshot,process_exists=process_exists); quarantine,_fallback=verifier._scan_quarantine(clear_resolved=False)
    manager_verified,manager_identity_state,manager_pid=_verified_identity(runtime_dir/"manager.identity.json",snapshot); recorded_state=old.get("state","NOT_STARTED")
    if quarantine["quarantine_count"]: manager_state="BLOCKED_WORKER_QUARANTINE"
    elif manager_verified: manager_state="RUNNING" if recorded_state not in {"STOPPING","PAUSED"} else recorded_state
    elif recorded_state in {"RUNNING","STOPPING"}: manager_state="STALE"
    else: manager_state=recorded_state
    rows=[]; active=[]
    for spec in config.models:
        sizes=storage_bytes(spec.local_dir); complete=verifier._is_complete(spec); item=(old.get("models") or {}).get(spec.id,{})
        worker_path=item.get("worker_identity") or runtime_dir/"workers"/f"{spec.id}.identity.json"; worker_verified,worker_identity_state,worker_pid=_verified_worker_identity(worker_path,verifier._command(spec),snapshot)
        requested_state=item.get("state") or item.get("status") or "PENDING"
        if complete: state="COMPLETED"; worker_verified=False
        elif manager_verified and worker_verified and requested_state=="DOWNLOADING": state="DOWNLOADING"; active.append(spec.id)
        elif requested_state=="RETRY_WAIT" and manager_verified: state="RETRY_WAIT"
        elif requested_state=="FAILED": state="FAILED"
        elif requested_state=="PAUSED" or manager_state in {"PAUSED","STOPPING"}: state="PAUSED"
        else: state="PENDING"
        downloaded=sizes.payload_bytes+sizes.partial_cache_bytes; progress=100.0 if complete else min(downloaded/spec.expected_bytes*100,99.9)
        rows.append({"id":spec.id,"repo":spec.repo,"state":state,"worker_pid":worker_pid if worker_verified else None,"worker_pid_verified":worker_verified,"worker_identity_state":worker_identity_state,"local_dir":str(spec.local_dir),"payload_bytes":sizes.payload_bytes,"partial_cache_bytes":sizes.partial_cache_bytes,"downloaded_bytes":downloaded,"expected_bytes":spec.expected_bytes,"progress_pct":round(progress,2),"retry_count":int(item.get("retry_count",0)),"last_error_category":item.get("last_error_category")})
    return {"manager_state":manager_state,"manager_pid":manager_pid,"manager_pid_verified":manager_verified,"manager_identity_state":manager_identity_state,"parallel_limit":int(old.get("parallel_limit",config.default_parallel)),"active_count":len(active),"active_model_ids":active,**quarantine,"models":rows}


def bounded_status(runtime_dir: Path=DEFAULT_RUNTIME,config: QueueConfig|None=None,snapshot=process_snapshot,process_exists=exact_pid_exists):
    state=status_snapshot(config,runtime_dir,snapshot=snapshot,process_exists=process_exists); lines=["MODEL_DOWNLOAD_MANAGER",f"state: {state['manager_state']}",f"manager_pid: {state['manager_pid'] or '-'}",f"manager_pid_verified: {'YES' if state['manager_pid_verified'] else 'NO'}",f"parallel_limit: {state['parallel_limit']}",f"active_count: {state['active_count']}",f"quarantine_count: {state['quarantine_count']}",f"quarantined_model_ids: {','.join(state['quarantined_model_ids']) or '-'}"]
    for row in state["models"]:
        lines.extend(["",f"MODEL {row['id']}",f"repo: {row['repo']}",f"state: {row['state']}",f"worker_pid: {row['worker_pid'] or '-'}",f"worker_pid_verified: {'YES' if row['worker_pid_verified'] else 'NO'}",f"local_dir: {row['local_dir']}",f"payload_gib: {row['payload_bytes']/1024**3:.3f}",f"partial_cache_gib: {row['partial_cache_bytes']/1024**3:.3f}",f"downloaded_gib: {row['downloaded_bytes']/1024**3:.3f}",f"expected_gib: {row['expected_bytes']/1024**3:.3f}",f"progress_pct: {row['progress_pct']:.2f}",f"retry_count: {row['retry_count']}",f"last_error_category: {row['last_error_category'] or '-'}"])
    return "\n".join(lines)+"\n"


def stop_manager(runtime_dir: Path=DEFAULT_RUNTIME,*,snapshot=process_snapshot,killer=os.kill,sleeper=time.sleep,attempts=50):
    runtime_dir=Path(runtime_dir); path=runtime_dir/"manager.identity.json"; verified,status,pid=_verified_identity(path,snapshot)
    if not verified: return "ALREADY_STOPPED" if status in {"DEAD","MISMATCH","MISSING"} else "IDENTITY_INVALID"
    saved=json.loads(path.read_text()); argv=saved.get("argv",[])
    if "model-download-queue.py" not in " ".join(argv) or "--run" not in argv: return "IDENTITY_INVALID"
    killer(pid,signal.SIGTERM)
    for _ in range(attempts):
        current,_,_=_verified_identity(path,snapshot)
        if not current: return "STOPPED"
        sleeper(0.2)
    return "STOP_TIMEOUT"


def write_launch_plist(path: Path=DEFAULT_RUNTIME/f"{LABEL}.plist"):
    """Legacy compatibility only; manual scripts never call this function."""
    path.parent.mkdir(parents=True,exist_ok=True); payload={"Label":LABEL,"ProgramArguments":[str(Path(sys.executable)),str(DEFAULT_ROOT/"control-plane/scripts/model-download-queue.py"),"--run"],"WorkingDirectory":str(DEFAULT_ROOT),"RunAtLoad":False,"KeepAlive":False}
    temporary=path.with_suffix(".tmp")
    with temporary.open("wb") as handle: plistlib.dump(payload,handle)
    os.replace(temporary,path); return path

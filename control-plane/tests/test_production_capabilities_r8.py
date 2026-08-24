import json
import subprocess

import pytest

from local_ai_control.services.heavy_process_identity import ProcessIdentity
from local_ai_control.services.model_downloads import DownloadSpec,ModelDownloadQueue,QueueConfig,WorkerCleanupUnconfirmed,WorkerQuarantinePersistenceFailure,bounded_status,status_snapshot


def model(tmp_path):
    return DownloadSpec("model","TEST","org/model","a"*40,tmp_path/"models/model",10,"test","test")


def config(spec): return QueueConfig((spec,),2,0,2)


def identity(pid=77,start="START"):
    return ProcessIdentity(pid,"/unverifiable/worker",("/unverifiable/worker","download"),start)


def complete(spec,_log):
    spec.local_dir.mkdir(parents=True,exist_ok=True); (spec.local_dir/"weights.bin").write_bytes(b"0123456789"); return 0


class FakePopen:
    pid=77
    def __init__(self): self.alive=True
    def poll(self): return None if self.alive else 0
    def terminate(self): pass
    def kill(self): pass
    def wait(self,timeout=None): raise subprocess.TimeoutExpired("hf",timeout)


def use_popen(monkeypatch,process):
    monkeypatch.setattr("local_ai_control.services.model_downloads.subprocess.Popen",lambda *_args,**_kwargs:process)


def primary_record(tmp_path):
    spec=model(tmp_path); queue=ModelDownloadQueue(config(spec),tmp_path/"runtime"); queue._write_quarantine(spec,77,identity()); return spec,queue


def fallback_record(monkeypatch,tmp_path):
    spec=model(tmp_path); process=FakePopen(); use_popen(monkeypatch,process)
    queue=ModelDownloadQueue(config(spec),tmp_path/"runtime",snapshot=lambda pid:identity(pid))
    monkeypatch.setattr(queue,"_write_quarantine",lambda *_args:(_ for _ in ()).throw(OSError("injected primary write failure")))
    with pytest.raises(WorkerCleanupUnconfirmed): queue._download_process(spec,tmp_path/"worker.log")
    return spec,queue


def test_observed_snapshot_unavailable_but_pid_live_blocks_and_preserves(tmp_path):
    spec,first=primary_record(tmp_path); calls=[]
    restarted=ModelDownloadQueue(config(spec),first.runtime_dir,downloader=lambda *_:calls.append(1),snapshot=lambda _pid:None,process_exists=lambda _pid:True)
    assert restarted.run()=="BLOCKED_WORKER_QUARANTINE" and not calls
    assert (restarted.quarantine_root/f"{spec.id}.json").exists()


def test_observed_snapshot_unavailable_and_pid_absent_clears_and_resumes(tmp_path):
    spec,first=primary_record(tmp_path)
    restarted=ModelDownloadQueue(config(spec),first.runtime_dir,downloader=complete,snapshot=lambda _pid:None,process_exists=lambda _pid:False,waiter=lambda _delay:False)
    assert restarted.run()=="COMPLETED" and not list(restarted.quarantine_root.iterdir())


def test_observed_different_identity_clears_without_signal(monkeypatch,tmp_path):
    spec,first=primary_record(tmp_path); reused=identity(start="REUSED"); signals=[]
    monkeypatch.setattr("local_ai_control.services.model_downloads.os.kill",lambda *args:signals.append(args))
    restarted=ModelDownloadQueue(config(spec),first.runtime_dir,downloader=complete,snapshot=lambda pid:reused if pid==77 else None,process_exists=lambda _pid:True,waiter=lambda _delay:False)
    assert restarted.run()=="COMPLETED" and not signals and not list(restarted.quarantine_root.iterdir())


def test_observed_exact_pid_probe_error_fails_closed(tmp_path):
    spec,first=primary_record(tmp_path); calls=[]
    def probe(_pid): raise RuntimeError("probe unavailable")
    restarted=ModelDownloadQueue(config(spec),first.runtime_dir,downloader=lambda *_:calls.append(1),snapshot=lambda _pid:None,process_exists=probe)
    assert restarted.run()=="BLOCKED_WORKER_QUARANTINE" and not calls
    assert (restarted.quarantine_root/f"{spec.id}.json").exists()


def test_status_blocks_when_observed_snapshot_missing_but_pid_live(tmp_path):
    spec,first=primary_record(tmp_path)
    state=status_snapshot(config(spec),first.runtime_dir,snapshot=lambda _pid:None,process_exists=lambda _pid:True)
    text=bounded_status(first.runtime_dir,config(spec),snapshot=lambda _pid:None,process_exists=lambda _pid:True)
    assert state["manager_state"]=="BLOCKED_WORKER_QUARANTINE" and state["quarantine_count"]==1
    assert "state: BLOCKED_WORKER_QUARANTINE" in text and "quarantine_count: 1" in text


def test_primary_quarantine_write_failure_persists_full_state_blocker(monkeypatch,tmp_path):
    spec,queue=fallback_record(monkeypatch,tmp_path); state=json.loads(queue.state_path.read_text()); blocker=state["cleanup_blockers"][0]
    assert state["state"]=="BLOCKED_WORKER_QUARANTINE" and state["quarantine_count"]==1
    assert blocker["model_id"]==spec.id and blocker["pid"]==77 and blocker["identity_state"]=="OBSERVED"
    assert blocker["observed_identity"]["start_identity"]=="START"


def test_both_durable_writes_failing_raises_hard_persistence_error(monkeypatch,tmp_path):
    spec=model(tmp_path); process=FakePopen(); use_popen(monkeypatch,process)
    queue=ModelDownloadQueue(config(spec),tmp_path/"runtime",snapshot=lambda pid:identity(pid))
    monkeypatch.setattr(queue,"_write_quarantine",lambda *_args:(_ for _ in ()).throw(OSError("primary failed")))
    monkeypatch.setattr(queue,"_snapshot_state",lambda *_args:(_ for _ in ()).throw(OSError("fallback failed")))
    with pytest.raises(WorkerQuarantinePersistenceFailure): queue._download_process(spec,tmp_path/"worker.log")


def test_restart_with_fallback_blocker_and_live_identity_schedules_zero(monkeypatch,tmp_path):
    spec,queue=fallback_record(monkeypatch,tmp_path); calls=[]; current=identity()
    restarted=ModelDownloadQueue(config(spec),queue.runtime_dir,downloader=lambda *_:calls.append(1),snapshot=lambda pid:current if pid==77 else None)
    assert restarted.run()=="BLOCKED_WORKER_QUARANTINE" and not calls and not restarted.active_reservations


@pytest.mark.parametrize("proof",["DEAD","REUSED"])
def test_fallback_blocker_clears_only_after_dead_or_reused_proof_without_signal(monkeypatch,tmp_path,proof):
    spec,queue=fallback_record(monkeypatch,tmp_path); signals=[]
    monkeypatch.setattr("local_ai_control.services.model_downloads.os.kill",lambda *args:signals.append(args))
    current=None if proof=="DEAD" else identity(start="REUSED")
    restarted=ModelDownloadQueue(config(spec),queue.runtime_dir,downloader=complete,snapshot=lambda pid:current if pid==77 else None,process_exists=lambda _pid:False if proof=="DEAD" else True,waiter=lambda _delay:False)
    assert restarted.run()=="COMPLETED" and not signals
    assert json.loads(restarted.state_path.read_text())["cleanup_blockers"]==[]

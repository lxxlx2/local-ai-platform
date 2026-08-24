import json
import os
import stat
import subprocess

import pytest

from local_ai_control.services.heavy_process_identity import ProcessIdentity
from local_ai_control.services.model_downloads import DownloadSpec,ModelDownloadQueue,QueueConfig,WorkerCleanupUnconfirmed,WorkerIdentityConflict,bounded_status,status_snapshot


def model(tmp_path,index=0):
    return DownloadSpec(f"model-{index}","TEST",f"org/model-{index}",f"{index+1:040x}",tmp_path/f"models/model-{index}",10,"test","test")


def config(*models): return QueueConfig(tuple(models),2,0,2)


def observed(pid=77,start="START"):
    return ProcessIdentity(pid,"/unverifiable/worker",("/unverifiable/worker","download"),start)


def complete(spec,_log):
    spec.local_dir.mkdir(parents=True,exist_ok=True); (spec.local_dir/"weights.bin").write_bytes(b"0123456789"); return 0


class FakePopen:
    pid=77
    def __init__(self,stuck): self.stuck=stuck; self.alive=True; self.terminate_calls=0; self.kill_calls=0
    def poll(self): return None if self.alive else 0
    def terminate(self): self.terminate_calls+=1; self.alive=self.stuck
    def kill(self): self.kill_calls+=1; self.alive=self.stuck
    def wait(self,timeout=None):
        if self.alive: raise subprocess.TimeoutExpired("hf",timeout)
        return 0


def use_popen(monkeypatch,process):
    calls=[]
    def create(*_args,**_kwargs): calls.append(1); return process
    monkeypatch.setattr("local_ai_control.services.model_downloads.subprocess.Popen",create)
    return calls


def write_record(queue,spec,identity): queue._write_quarantine(spec,identity.pid if identity else 77,identity)


def test_cleanup_unconfirmed_writes_atomic_private_quarantine(monkeypatch,tmp_path):
    spec=model(tmp_path); process=FakePopen(stuck=True); use_popen(monkeypatch,process)
    queue=ModelDownloadQueue(config(spec),tmp_path/"runtime",snapshot=lambda pid:observed(pid))
    with pytest.raises(WorkerCleanupUnconfirmed): queue._download_process(spec,tmp_path/"worker.log")
    record=queue.quarantine_root/f"{spec.id}.json"; payload=json.loads(record.read_text())
    assert stat.S_IMODE(queue.quarantine_root.stat().st_mode)==0o700
    assert stat.S_IMODE(record.stat().st_mode)==0o600
    assert payload["reason"]=="WORKER_CLEANUP_UNCONFIRMED" and payload["observed_identity"]["pid"]==77
    assert [item.name for item in queue.quarantine_root.iterdir()]==[record.name]


def test_restart_with_exact_observed_identity_blocks_all_workers(tmp_path):
    spec=model(tmp_path); first=ModelDownloadQueue(config(spec),tmp_path/"runtime"); identity=observed(); write_record(first,spec,identity); calls=[]
    restarted=ModelDownloadQueue(config(spec),tmp_path/"runtime",downloader=lambda *_:calls.append(1),snapshot=lambda pid:identity if pid==identity.pid else None)
    assert restarted.run()=="BLOCKED_WORKER_QUARANTINE"
    assert not calls and not restarted.active_reservations
    assert json.loads(restarted.state_path.read_text())["state"]=="BLOCKED_WORKER_QUARANTINE"


def test_restart_with_dead_pid_clears_quarantine_and_schedules(tmp_path):
    spec=model(tmp_path); first=ModelDownloadQueue(config(spec),tmp_path/"runtime"); write_record(first,spec,observed())
    restarted=ModelDownloadQueue(config(spec),tmp_path/"runtime",downloader=complete,snapshot=lambda _pid:None,waiter=lambda _delay:False)
    assert restarted.run()=="COMPLETED"
    assert not list(restarted.quarantine_root.iterdir()) and (spec.local_dir/"weights.bin").exists()


def test_restart_with_reused_pid_clears_without_signaling_and_schedules(monkeypatch,tmp_path):
    spec=model(tmp_path); first=ModelDownloadQueue(config(spec),tmp_path/"runtime"); original=observed(); write_record(first,spec,original)
    reused=observed(start="REUSED"); signals=[]; monkeypatch.setattr("local_ai_control.services.model_downloads.os.kill",lambda *args:signals.append(args))
    restarted=ModelDownloadQueue(config(spec),tmp_path/"runtime",downloader=complete,snapshot=lambda pid:reused if pid==original.pid else None,waiter=lambda _delay:False)
    assert restarted.run()=="COMPLETED" and not signals
    assert not list(restarted.quarantine_root.iterdir())


def test_invalid_quarantine_fails_closed_without_workers(tmp_path):
    spec=model(tmp_path); runtime=tmp_path/"runtime"; root=runtime/"quarantine"; root.mkdir(parents=True,mode=0o700); record=root/f"{spec.id}.json"; record.write_text("{invalid"); os.chmod(record,0o600); calls=[]
    queue=ModelDownloadQueue(config(spec),runtime,downloader=lambda *_:calls.append(1),snapshot=lambda _pid:None)
    assert queue.run()=="BLOCKED_WORKER_QUARANTINE" and not calls and not queue.active_reservations


def test_unavailable_identity_live_blocks_and_dead_clears(tmp_path):
    spec=model(tmp_path); runtime=tmp_path/"runtime"; first=ModelDownloadQueue(config(spec),runtime); first._write_quarantine(spec,77,None)
    payload=json.loads((first.quarantine_root/f"{spec.id}.json").read_text())
    assert payload["identity_state"]=="UNAVAILABLE" and payload["observed_identity"] is None and payload["pid"]==77
    live=ModelDownloadQueue(config(spec),runtime,downloader=lambda *_:pytest.fail("worker scheduled"),snapshot=lambda _pid:None,process_exists=lambda _pid:True)
    assert live.run()=="BLOCKED_WORKER_QUARANTINE"
    dead=ModelDownloadQueue(config(spec),runtime,downloader=complete,snapshot=lambda _pid:None,process_exists=lambda _pid:False,waiter=lambda _delay:False)
    assert dead.run()=="COMPLETED" and not list(dead.quarantine_root.iterdir())


@pytest.mark.parametrize("old_state",["RUNNING","PAUSED"])
def test_status_quarantine_overrides_stale_state_and_is_visible(tmp_path,old_state):
    spec=model(tmp_path); runtime=tmp_path/"runtime"; queue=ModelDownloadQueue(config(spec),runtime); identity=observed(); write_record(queue,spec,identity)
    queue._atomic_json(queue.state_path,{"state":old_state,"parallel_limit":2,"models":{spec.id:{"state":"PAUSED"}}})
    state=status_snapshot(config(spec),runtime,snapshot=lambda pid:identity if pid==identity.pid else None)
    text=bounded_status(runtime,config(spec),snapshot=lambda pid:identity if pid==identity.pid else None)
    assert state["manager_state"]=="BLOCKED_WORKER_QUARANTINE"
    assert state["quarantine_count"]==1 and state["quarantined_model_ids"]==[spec.id]
    assert "quarantine_count: 1" in text and f"quarantined_model_ids: {spec.id}" in text


def test_successful_mismatch_cleanup_never_creates_quarantine(monkeypatch,tmp_path):
    spec=model(tmp_path); process=FakePopen(stuck=False); use_popen(monkeypatch,process)
    queue=ModelDownloadQueue(config(spec),tmp_path/"runtime",snapshot=lambda pid:observed(pid))
    with pytest.raises(WorkerIdentityConflict): queue._download_process(spec,tmp_path/"worker.log")
    assert not queue.quarantine_root.exists() or not list(queue.quarantine_root.iterdir())

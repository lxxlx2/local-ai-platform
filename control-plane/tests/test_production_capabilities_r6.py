import subprocess

import pytest

from local_ai_control.services.heavy_process_identity import ProcessIdentity
from local_ai_control.services.model_downloads import DownloadSpec,ModelDownloadQueue,QueueConfig,WorkerCleanupUnconfirmed,WorkerIdentityConflict


def spec(tmp_path,index=0):
    return DownloadSpec(f"model-{index}","TEST",f"org/model-{index}",f"{index+1:040x}",tmp_path/f"models/model-{index}",10,"test","test")


def queue_for(tmp_path,models,**kwargs):
    return ModelDownloadQueue(QueueConfig(tuple(models),3,0,2),tmp_path/"runtime",waiter=lambda _delay:False,**kwargs)


def mismatch(pid):
    return ProcessIdentity(pid,"/unrelated",("unrelated",),"START")


class FakePopen:
    pid=77
    def __init__(self,mode="alive",observer=lambda:None):
        self.mode=mode; self.observer=observer; self.alive=mode!="exited"
        self.terminate_calls=0; self.kill_calls=0; self.wait_calls=[]
    def poll(self): return None if self.alive else 0
    def terminate(self):
        self.observer(); self.terminate_calls+=1
        if self.mode=="alive": self.alive=False
    def kill(self):
        self.observer(); self.kill_calls+=1
        if self.mode!="stuck": self.alive=False
    def wait(self,timeout=None):
        self.observer(); self.wait_calls.append(timeout)
        if self.alive: raise subprocess.TimeoutExpired("hf",timeout)
        return 0


def install_popen(monkeypatch,processes):
    calls=[]
    def create(*_args,**_kwargs):
        calls.append(1); return processes[min(len(calls)-1,len(processes)-1)]
    monkeypatch.setattr("local_ai_control.services.model_downloads.subprocess.Popen",create)
    return calls


def test_alive_identity_mismatch_terminates_reaps_and_never_retries(monkeypatch,tmp_path):
    model=spec(tmp_path); process=FakePopen("alive"); calls=install_popen(monkeypatch,[process])
    queue=queue_for(tmp_path,[model],snapshot=lambda pid:mismatch(pid)); queue.statuses={model.id:{"started_at":None}}
    queue.active_reservations[model.id]=10
    assert queue._worker(model)=="FAILED"
    assert process.terminate_calls==1 and process.kill_calls==0 and process.wait_calls==[10]
    assert len(calls)==1 and model.id not in queue.active_reservations


def test_already_exited_identity_mismatch_is_reaped_without_signal(monkeypatch,tmp_path):
    model=spec(tmp_path); process=FakePopen("exited"); install_popen(monkeypatch,[process])
    queue=queue_for(tmp_path,[model],snapshot=lambda pid:mismatch(pid))
    with pytest.raises(WorkerIdentityConflict): queue._download_process(model,tmp_path/"worker.log")
    assert process.terminate_calls==0 and process.kill_calls==0 and process.wait_calls==[0]


def test_terminate_timeout_kills_only_after_child_still_alive_then_reaps(monkeypatch,tmp_path):
    model=spec(tmp_path); process=FakePopen("timeout"); install_popen(monkeypatch,[process])
    queue=queue_for(tmp_path,[model],snapshot=lambda pid:mismatch(pid))
    with pytest.raises(WorkerIdentityConflict): queue._download_process(model,tmp_path/"worker.log")
    assert process.terminate_calls==1 and process.kill_calls==1 and process.wait_calls==[10,5]
    assert process.poll()==0


def test_reservation_is_held_until_unverifiable_child_is_reaped(monkeypatch,tmp_path):
    model=spec(tmp_path); observed=[]; queue=queue_for(tmp_path,[model],snapshot=lambda pid:mismatch(pid))
    process=FakePopen("alive",lambda:observed.append(model.id in queue.active_reservations)); install_popen(monkeypatch,[process])
    queue.statuses={model.id:{"started_at":None}}; queue.active_reservations[model.id]=10
    assert queue._worker(model)=="FAILED"
    assert observed and all(observed) and model.id not in queue.active_reservations


def test_unreaped_identity_conflict_stops_manager_and_blocks_more_scheduling(monkeypatch,tmp_path):
    first,second=spec(tmp_path,0),spec(tmp_path,1); process=FakePopen("stuck"); calls=install_popen(monkeypatch,[process])
    usage=lambda _path:type("Usage",(),{"free":15})()
    queue=queue_for(tmp_path,[first,second],snapshot=lambda pid:mismatch(pid) if pid==process.pid else None,disk_usage=usage)
    assert queue.run()=="BLOCKED_WORKER_QUARANTINE"
    assert len(calls)==1 and process.terminate_calls==1 and process.kill_calls==1
    assert queue.stop_event.is_set() and first.id in queue.active_reservations
    assert queue.statuses[first.id]["last_error_category"]=="WORKER_CLEANUP_UNCONFIRMED"
    assert queue.statuses[second.id]["state"]=="PAUSED"


def test_cleanup_failure_is_distinct_from_reaped_identity_conflict(monkeypatch,tmp_path):
    model=spec(tmp_path); process=FakePopen("stuck"); install_popen(monkeypatch,[process])
    queue=queue_for(tmp_path,[model],snapshot=lambda pid:mismatch(pid))
    with pytest.raises(WorkerCleanupUnconfirmed): queue._download_process(model,tmp_path/"worker.log")

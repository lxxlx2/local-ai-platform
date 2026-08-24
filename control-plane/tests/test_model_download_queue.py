import fcntl
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import threading
import time
from types import SimpleNamespace

from local_ai_control.services.heavy_process_identity import ProcessIdentity,write_identity
from local_ai_control.services.model_downloads import ModelDownloadQueue,bounded_status,load_queue_config,status_snapshot,storage_bytes,stop_manager,write_launch_plist


def make_config(tmp_path: Path,*,count=3,attempts=2,reserve=0,expected=10):
    models_root=tmp_path/"models"; models_root.mkdir(exist_ok=True); rows=[]
    for index in range(count):
        rows.append({"id":f"model-{index}","role":"TEST","repo":f"org/model-{index}","revision":f"{index+1:040x}","local_dir":str(models_root/f"model-{index}"),"expected_bytes":expected,"license":"test","runtime":"test"})
    path=tmp_path/"queue.json"; path.write_text(json.dumps({"serial_only":False,"default_parallel":3,"max_attempts":attempts,"reserve_bytes":reserve,"models":rows}))
    return load_queue_config(path,models_root=models_root),models_root


def complete_file(spec,value=b"0123456789"):
    spec.local_dir.mkdir(parents=True,exist_ok=True); (spec.local_dir/"weights.bin").write_bytes(value)


def test_production_config_is_pinned_parallel_and_has_eight_models():
    config=load_queue_config(); assert len(config.models)==8 and config.default_parallel==3
    assert len({item.id for item in config.models})==8 and all(len(item.revision)==40 for item in config.models)
    assert config.models[-1].include==("8-bit/*",)


def test_three_pending_start_concurrently_without_duplicate_model(tmp_path):
    config,_=make_config(tmp_path); lock=threading.Lock(); release=threading.Event(); active=set(); maximum=0; calls=[]
    def download(spec,_log):
        nonlocal maximum
        with lock: active.add(spec.id); calls.append(spec.id); maximum=max(maximum,len(active))
        if len(active)>=3: release.set()
        assert release.wait(2); complete_file(spec)
        with lock: active.remove(spec.id)
        return 0
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=download,waiter=lambda _delay:False)
    assert runner.run()=="COMPLETED" and maximum==3 and sorted(calls)==["model-0","model-1","model-2"]


def test_valid_complete_skips_and_existing_incomplete_is_preserved(tmp_path):
    config,_=make_config(tmp_path,count=2); first,second=config.models; complete_file(first)
    verifier=ModelDownloadQueue(config,tmp_path/"runtime",downloader=lambda *_:0); verifier._write_marker(first)
    cache=second.local_dir/".cache/huggingface/download"; cache.mkdir(parents=True); partial=cache/"weights.incomplete"; partial.write_bytes(b"partial")
    calls=[]
    def download(spec,_log): calls.append(spec.id); assert partial.read_bytes()==b"partial"; complete_file(spec); return 0
    assert ModelDownloadQueue(config,tmp_path/"runtime",downloader=download,waiter=lambda _delay:False).run()=="COMPLETED"
    assert calls==[second.id] and partial.exists()


def test_one_worker_failure_and_retry_are_independent(tmp_path):
    config,_=make_config(tmp_path,count=2,attempts=2); calls=[]
    def download(spec,_log):
        calls.append(spec.id)
        if spec.id=="model-0": return 7
        complete_file(spec); return 0
    result=ModelDownloadQueue(config,tmp_path/"runtime",downloader=download,waiter=lambda _delay:False).run()
    state=json.loads((tmp_path/"runtime/state.json").read_text())
    assert result=="COMPLETED_WITH_FAILURES" and calls.count("model-0")==2 and calls.count("model-1")==1
    assert state["models"]["model-0"]["retry_count"]==1 and state["models"]["model-1"]["state"]=="COMPLETED"


def test_global_disk_reservation_prevents_parallel_overcommit(tmp_path):
    config,_=make_config(tmp_path,count=2,attempts=1,reserve=100,expected=100); active=0; maximum=0; runner=None
    def download(spec,_log):
        nonlocal active,maximum
        active+=1; maximum=max(maximum,active); complete_file(spec,b"x"*100); active-=1; return 0
    usage=lambda _path:SimpleNamespace(free=250)
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=download,disk_usage=usage)
    runner.run(); assert maximum==1


def test_pause_preserves_partial_and_new_manager_resumes(tmp_path):
    config,_=make_config(tmp_path,count=1,attempts=1); spec=config.models[0]; cache=spec.local_dir/".cache/huggingface/download"; cache.mkdir(parents=True); partial=cache/"x.incomplete"; partial.write_bytes(b"keep")
    started=threading.Event(); holder={}
    def paused(_spec,_log): started.set(); holder["runner"].stop_event.wait(2); return 143
    first=ModelDownloadQueue(config,tmp_path/"runtime",downloader=paused); holder["runner"]=first
    thread=threading.Thread(target=first.run); thread.start(); assert started.wait(2); first.request_stop(); thread.join(3)
    assert partial.read_bytes()==b"keep" and json.loads(first.state_path.read_text())["state"]=="PAUSED"
    def resumed(spec,_log): complete_file(spec); return 0
    assert ModelDownloadQueue(config,tmp_path/"runtime",downloader=resumed).run()=="COMPLETED" and partial.exists()


class FakeProcess:
    def __init__(self,pid=22): self.pid=pid; self.terminated=False; self.killed=False
    def poll(self): return None
    def terminate(self): self.terminated=True
    def wait(self,timeout=None): return 0
    def kill(self): self.killed=True


def test_stop_only_terminates_exact_owned_child(tmp_path):
    config,_=make_config(tmp_path,count=1); spec=config.models[0]; current=ProcessIdentity(22,"/python",("python","hf","download"),"START")
    runner=ModelDownloadQueue(config,tmp_path/"runtime",snapshot=lambda _pid:current); runner._private_runtime(); write_identity(runner.worker_root/f"{spec.id}.identity.json",current)
    owned=FakeProcess(); runner._terminate_owned(spec,owned); assert owned.terminated
    reused=ProcessIdentity(22,"/other",("other",),"REUSED"); runner.snapshot=lambda _pid:reused
    unrelated=FakeProcess(); runner._terminate_owned(spec,unrelated); assert not unrelated.terminated and not unrelated.killed


def test_stale_manager_and_worker_are_not_reported_running(tmp_path):
    config,_=make_config(tmp_path,count=1); runtime=tmp_path/"runtime"; runtime.mkdir(); worker_dir=runtime/"workers"; worker_dir.mkdir()
    manager=ProcessIdentity(10,"/python",("python","model-download-queue.py","--run"),"OLD"); worker=ProcessIdentity(11,"/python",("python","hf","download"),"OLD")
    write_identity(runtime/"manager.identity.json",manager); write_identity(worker_dir/"model-0.identity.json",worker)
    (runtime/"state.json").write_text(json.dumps({"state":"RUNNING","parallel_limit":3,"models":{"model-0":{"state":"DOWNLOADING","worker_identity":str(worker_dir/"model-0.identity.json")}}}))
    state=status_snapshot(config,runtime,snapshot=lambda _pid:None)
    assert state["manager_state"]=="STALE" and not state["manager_pid_verified"] and state["active_count"]==0 and state["models"][0]["state"]=="PENDING"


def test_status_progress_includes_partial_and_caps_before_verified_complete(tmp_path):
    config,_=make_config(tmp_path,count=1,expected=100); spec=config.models[0]; spec.local_dir.mkdir(); (spec.local_dir/"payload.bin").write_bytes(b"x"*30); cache=spec.local_dir/".cache"; cache.mkdir(); (cache/"x.incomplete").write_bytes(b"y"*70)
    row=status_snapshot(config,tmp_path/"runtime",snapshot=lambda _pid:None)["models"][0]
    assert row["downloaded_bytes"]==100 and row["progress_pct"]==99.9 and row["partial_cache_bytes"]==70
    assert "partial_cache_gib" in bounded_status(tmp_path/"runtime",config,snapshot=lambda _pid:None)


def test_stop_manager_requires_exact_live_identity(tmp_path):
    runtime=tmp_path/"runtime"; runtime.mkdir(); saved=ProcessIdentity(33,"/python",("python","model-download-queue.py","--run"),"START"); write_identity(runtime/"manager.identity.json",saved); kills=[]
    assert stop_manager(runtime,snapshot=lambda _pid:ProcessIdentity(33,"/other",("other",),"REUSED"),killer=lambda *args:kills.append(args))=="ALREADY_STOPPED" and not kills
    snapshots=iter([saved,None]); result=stop_manager(runtime,snapshot=lambda _pid:next(snapshots,None),killer=lambda *args:kills.append(args),sleeper=lambda _:None)
    assert result=="STOPPED" and len(kills)==1


def test_singleton_lock_refuses_second_runner(tmp_path):
    config,_=make_config(tmp_path,count=1); runtime=tmp_path/"runtime"; runtime.mkdir(); lock=(runtime/"manager.lock").open("a+"); fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    try: assert ModelDownloadQueue(config,runtime,downloader=lambda *_:0).run()=="ALREADY_RUNNING"
    finally: fcntl.flock(lock.fileno(),fcntl.LOCK_UN); lock.close()


def test_snapshot_index_and_manifest_are_strict(tmp_path):
    config,_=make_config(tmp_path,count=1); spec=config.models[0]; spec.local_dir.mkdir(); (spec.local_dir/"model.safetensors.index.json").write_text(json.dumps({"weight_map":{"a":"a.safetensors","b":"b.safetensors"}})); (spec.local_dir/"a.safetensors").write_bytes(b"x"*10)
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=lambda *_:0); assert not runner._snapshot_valid(spec)
    (spec.local_dir/"model.safetensors.index.json").unlink(); complete_file(spec); runner._write_marker(spec); assert runner._is_complete(spec); (spec.local_dir/"weights.bin").write_bytes(b"tampered"); assert not runner._is_complete(spec)


def test_manual_scripts_have_no_launchd_or_shell_true_and_watch_ctrl_c_is_read_only():
    root=Path("/Users/jerson/AI"); start=(root/"control-plane/scripts/start-model-downloads.sh").read_text(); service=(root/"control-plane/src/local_ai_control/services/model_downloads.py").read_text(); watch_path=root/"control-plane/scripts/watch-model-downloads.py"
    assert "launchctl" not in start and "bootstrap" not in start and "shell=True" not in service and "shell=True" not in start
    spec=importlib.util.spec_from_file_location("download_watch",watch_path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    calls=[]
    snapshot={"manager_state":"RUNNING","active_count":0,"parallel_limit":3,"models":[]}
    assert module.watch(5,provider=lambda:snapshot,printer=lambda *args,**kwargs:calls.append(args),sleeper=lambda _delay:(_ for _ in ()).throw(KeyboardInterrupt()))=="WATCH_STOPPED"
    assert calls and "os.kill" not in watch_path.read_text()


def test_legacy_plist_is_disabled_and_runtime_is_git_ignored(tmp_path):
    path=write_launch_plist(tmp_path/"queue.plist")
    with path.open("rb") as handle: payload=plistlib.load(handle)
    assert payload["KeepAlive"] is False and payload["RunAtLoad"] is False
    assert "runtime/" in Path("/Users/jerson/AI/.gitignore").read_text().splitlines()

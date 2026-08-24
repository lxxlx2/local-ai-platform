from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.heavy_process_identity import ProcessIdentity,expected_spawn_identity,normalized_spawn_signature,write_identity
from local_ai_control.services.models import ModelRegistry,QWEN36,QWEN38
from local_ai_control.services.model_downloads import DownloadSpec,ModelDownloadQueue,QueueConfig,WorkerIdentityConflict
from local_ai_control.services.runtime_providers import HeavyModelConflict,LaunchdHeavyRuntimeLifecycle,RuntimeProviderFactory


class Provider:
    def __init__(self,healthy=False): self.healthy=healthy; self.calls=0
    def health(self):
        if not self.healthy: raise OSError("down")
        return {"status":"healthy"}


class Preflight:
    def check(self,*_args,**_kwargs): return SimpleNamespace(allowed=True,reason="OK")


class Runner:
    def __init__(self,*,present=False): self.present=present; self.events=[]
    def __call__(self,argv,**_kwargs):
        self.events.append(tuple(argv))
        if argv[1]=="print": return SimpleNamespace(returncode=0 if self.present else 1)
        return SimpleNamespace(returncode=0)


def saved(lifecycle,profile,pid=41):
    executable,argv=lifecycle._process_signature(profile)
    identity=ProcessIdentity(pid,executable,argv,"START")
    write_identity(lifecycle._identity_path(profile),identity)
    return identity


def runtime_with(lifecycle):
    main=Provider(False); fast=Provider(False)
    runtime=RuntimeProviderFactory(ModelRegistry(),main=main,fast=fast,preflight=Preflight(),lifecycle=lifecycle,sleep=lambda _:None)
    return runtime,main,fast


@pytest.mark.parametrize(("live_profile","task"),[
    (QWEN38.profile_id,"FAST"),
    (QWEN36.profile_id,"CHAT"),
    (QWEN38.profile_id,"CHAT"),
])
def test_live_owned_process_with_endpoint_down_is_reconciled_before_any_start(tmp_path,live_profile,task):
    runner=Runner(); holder={}
    lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda pid:holder.get(pid),listeners=lambda _port:())
    live=saved(lifecycle,live_profile); holder[live.pid]=live
    runtime,_,_=runtime_with(lifecycle)
    with pytest.raises(HeavyModelConflict):
        with runtime.session(task): pass
    assert not any(event[1] in {"bootstrap","kickstart"} for event in runner.events)


def test_unknown_listener_fails_closed_without_start_or_control(tmp_path):
    runner=Runner(); lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:ProcessIdentity(91,"/unknown",("unknown",),"START"),listeners=lambda port:(91,) if port==8000 else ())
    runtime,_,_=runtime_with(lifecycle)
    with pytest.raises(HeavyModelConflict):
        with runtime.session("CHAT"): pass
    assert not any(event[1] in {"bootout","bootstrap","kickstart"} for event in runner.events)


def test_service_without_saved_identity_fails_closed_without_start(tmp_path):
    runner=Runner(present=True); lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:None,listeners=lambda _port:())
    runtime,_,_=runtime_with(lifecycle)
    with pytest.raises(HeavyModelConflict):
        with runtime.session("CHAT"): pass
    assert not any(event[1] in {"bootout","bootstrap","kickstart"} for event in runner.events)


def test_dead_profiles_and_absent_runtime_authorize_exactly_one_start(tmp_path):
    runner=Runner(); lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:None,listeners=lambda _port:())
    saved(lifecycle,QWEN38.profile_id); saved(lifecycle,QWEN36.profile_id,pid=42)
    probes={QWEN38.profile_id:lambda:False,QWEN36.profile_id:lambda:False}
    lifecycle.reconcile_before_start(QWEN36.profile_id,probes); lifecycle.start(QWEN36.profile_id)
    assert sum(event[1]=="bootstrap" for event in runner.events)==1
    with pytest.raises(HeavyModelConflict): lifecycle.start(QWEN36.profile_id)


def test_spawn_signature_is_exact_and_macos_python_framework_compatible(tmp_path):
    script=tmp_path/"hf"; script.write_text("#!/opt/homebrew/Cellar/python@3.14/3.14.6/bin/python3.14\n")
    command=(str(script),"download","org/model","--revision","a"*40,"--local-dir",str(tmp_path/"model"))
    executable,argv=normalized_spawn_signature(command)
    mac_executable="/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
    actual_argv=(mac_executable,*argv[1:])
    exact=ProcessIdentity(50,mac_executable,actual_argv,"START")
    assert expected_spawn_identity(exact,command)
    assert not expected_spawn_identity(ProcessIdentity(50,mac_executable,(*actual_argv,"--extra"),"START"),command)
    assert not expected_spawn_identity(ProcessIdentity(50,mac_executable,(actual_argv[0],actual_argv[2],actual_argv[1],*actual_argv[3:]),"START"),command)
    wrong="/opt/homebrew/Cellar/python@3.13/3.13.9/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
    assert not expected_spawn_identity(ProcessIdentity(50,wrong,(wrong,*actual_argv[1:]),"START"),command)


class FakeProcess:
    pid=77
    def __init__(self): self.terminated=False; self.killed=False
    def poll(self): return None
    def terminate(self): self.terminated=True
    def kill(self): self.killed=True
    def wait(self,timeout=None): return 0


def test_download_worker_exact_command_controls_only_exact_saved_process(tmp_path):
    spec=DownloadSpec("model","TEST","org/model","a"*40,tmp_path/"models/model",10,"test","test")
    config=QueueConfig((spec,),2,0,2); queue=ModelDownloadQueue(config,tmp_path/"runtime")
    executable,argv=normalized_spawn_signature(queue._command(spec)); exact=ProcessIdentity(77,executable,argv,"START")
    queue._private_runtime(); write_identity(queue.worker_root/f"{spec.id}.identity.json",exact); queue.snapshot=lambda _pid:exact
    owned=FakeProcess(); queue._terminate_owned(spec,owned); assert owned.terminated
    for unrelated in (
        ProcessIdentity(77,executable,(*argv,"--extra"),"START"),
        ProcessIdentity(77,"/wrong/executable",argv,"START"),
        ProcessIdentity(77,executable,argv,"REUSED"),
    ):
        queue.snapshot=lambda _pid,value=unrelated:value
        process=FakeProcess(); queue._terminate_owned(spec,process)
        assert not process.terminated and not process.killed


def test_unverifiable_worker_is_failed_without_retry(tmp_path):
    spec=DownloadSpec("model","TEST","org/model","a"*40,tmp_path/"models/model",10,"test","test")
    config=QueueConfig((spec,),3,0,2); calls=[]
    def conflict(_spec,_log): calls.append(1); raise WorkerIdentityConflict("mismatch")
    queue=ModelDownloadQueue(config,tmp_path/"runtime",downloader=conflict,waiter=lambda _delay:False)
    assert queue.run()=="COMPLETED_WITH_FAILURES" and len(calls)==1
    assert queue.statuses[spec.id]["last_error_category"]=="WORKER_IDENTITY_CONFLICT"

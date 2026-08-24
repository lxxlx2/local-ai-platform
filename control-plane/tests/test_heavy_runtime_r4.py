from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.heavy_process_identity import ProcessIdentity,identity_status,write_identity
from local_ai_control.services.models import ModelRegistry,QWEN36,QWEN38
from local_ai_control.services.qwen38_runtime import RuntimeUnavailable
from local_ai_control.services.runtime_providers import HeavyModelConflict,LaunchdHeavyRuntimeLifecycle,RuntimeProviderFactory


class Provider:
    def __init__(self,name,healthy=False,error=None): self.name=name; self.healthy=healthy; self.error=error; self.calls=0
    def health(self):
        if not self.healthy: raise OSError("down")
        return {"status":"healthy"}
    def generate(self,*_args,**_kwargs):
        self.calls+=1
        if self.error: raise self.error
        return self.name


class Preflight:
    def check(self,*_args,**_kwargs): return SimpleNamespace(allowed=True,reason="OK")


class SafeLifecycle:
    def __init__(self,main,fast,*,stop_refusal=None,proof_refusal=None,start_failure=None,capture_failure=None):
        self.main=main; self.fast=fast; self.stop_refusal=stop_refusal; self.proof_refusal=proof_refusal; self.start_failure=start_failure; self.capture_failure=capture_failure; self.events=[]
    def provider(self,profile): return self.main if profile==QWEN38.profile_id else self.fast
    def safe_stop(self,profile,endpoint):
        self.events.append(("bootout",profile))
        if profile==self.stop_refusal: raise HeavyModelConflict("owned process still live")
        self.provider(profile).healthy=False
    def wait_stopped(self,profile,endpoint,attempts=20):
        self.events.append(("proved_down",profile))
        if profile==self.proof_refusal or endpoint(): raise HeavyModelConflict("still live")
    def start(self,profile):
        self.events.append(("start",profile)); self.provider(profile).healthy=True
        if profile==self.start_failure: raise RuntimeUnavailable("partial start")
    def capture_started(self,profile):
        self.events.append(("captured",profile))
        if profile==self.capture_failure: raise HeavyModelConflict("identity mismatch")


def factory(main=False,fast=False,**lifecycle_args):
    main_provider=Provider("main",main); fast_provider=Provider("fast",fast); lifecycle=SafeLifecycle(main_provider,fast_provider,**lifecycle_args)
    runtime=RuntimeProviderFactory(ModelRegistry(),main=main_provider,fast=fast_provider,preflight=Preflight(),lifecycle=lifecycle,sleep=lambda _:None)
    return runtime,main_provider,fast_provider,lifecycle


class Runner:
    def __init__(self,bootout=0,present=False): self.bootout=bootout; self.present=present; self.events=[]
    def __call__(self,argv,**_kwargs):
        self.events.append(tuple(argv))
        if argv[1]=="bootout": return SimpleNamespace(returncode=self.bootout)
        if argv[1]=="print": return SimpleNamespace(returncode=0 if self.present else 1)
        return SimpleNamespace(returncode=0)


def saved(lifecycle,profile,pid=41,start="START"):
    executable,argv=lifecycle._process_signature(profile); identity=ProcessIdentity(pid,executable,argv,start); write_identity(lifecycle._identity_path(profile),identity); return identity


def test_a_bootout_failure_with_live_identity_never_starts_target(tmp_path):
    runner=Runner(bootout=5,present=True); holder={}; lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:holder["identity"],listeners=lambda _port:())
    holder["identity"]=saved(lifecycle,QWEN38.profile_id)
    with pytest.raises(HeavyModelConflict): lifecycle.safe_stop(QWEN38.profile_id,lambda:True)
    assert not any(event[1] in {"bootstrap","kickstart"} for event in runner.events)


def test_b_endpoint_down_but_saved_identity_still_live_fails_closed(tmp_path):
    runner=Runner(); holder={}; lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:holder["identity"],listeners=lambda _port:())
    holder["identity"]=saved(lifecycle,QWEN38.profile_id); lifecycle.safe_stop(QWEN38.profile_id,lambda:False)
    with pytest.raises(HeavyModelConflict): lifecycle.wait_stopped(QWEN38.profile_id,lambda:False,attempts=2)


def test_b_factory_never_starts_any_profile_after_old_death_proof_fails():
    runtime,main,fast,lifecycle=factory(main=True,fast=False,proof_refusal=QWEN38.profile_id)
    with pytest.raises(HeavyModelConflict):
        with runtime.session("FAST"): pass
    assert not any(event[0]=="start" for event in lifecycle.events)


def test_c_and_l_dead_identity_endpoint_down_service_absent_allows_start(tmp_path):
    runner=Runner(present=False); lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:None,listeners=lambda _port:())
    saved(lifecycle,QWEN38.profile_id)
    assert lifecycle.safe_stop(QWEN38.profile_id,lambda:False)=="ALREADY_STOPPED"
    lifecycle.wait_stopped(QWEN38.profile_id,lambda:False)
    lifecycle.reconcile_before_start(QWEN36.profile_id,{QWEN38.profile_id:lambda:False,QWEN36.profile_id:lambda:False})
    lifecycle.start(QWEN36.profile_id)
    assert any(event[1]=="bootstrap" for event in runner.events)


def test_d_reused_pid_is_unrelated_and_never_controlled(tmp_path):
    runner=Runner(); holder={}; lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:holder["current"],listeners=lambda _port:())
    original=saved(lifecycle,QWEN38.profile_id); holder["current"]=ProcessIdentity(original.pid,"/unrelated",("unrelated",),"REUSED")
    assert identity_status(lifecycle._identity_path(QWEN38.profile_id),snapshot=lifecycle.snapshot)[0]=="MISMATCH"
    with pytest.raises(HeavyModelConflict): lifecycle.safe_stop(QWEN38.profile_id,lambda:True)
    assert not any(event[1]=="bootout" for event in runner.events)


def test_e_partial_target_live_prevents_rollback():
    runtime,main,fast,lifecycle=factory(main=False,fast=True,start_failure=QWEN38.profile_id,stop_refusal=QWEN38.profile_id)
    with pytest.raises(HeavyModelConflict):
        with runtime.session("CHAT"): pass
    assert ("start",QWEN36.profile_id) not in lifecycle.events[1:] and main.healthy and not fast.healthy


def test_f_inference_failure_cannot_start_fast_until_main_process_proved_down():
    runtime,main,fast,lifecycle=factory(main=True,fast=False,stop_refusal=QWEN38.profile_id); main.error=RuntimeUnavailable("HTTP dead")
    with pytest.raises(HeavyModelConflict): runtime.generate("CHAT","hello")
    assert main.calls==1 and fast.calls==0 and ("start",QWEN36.profile_id) not in lifecycle.events


def test_g_and_j_main_fast_main_has_exactly_one_resident():
    runtime,main,fast,lifecycle=factory(main=True,fast=False)
    with runtime.session("FAST") as selected:
        assert selected is fast and fast.healthy and not main.healthy
    assert main.healthy and not fast.healthy
    assert lifecycle.events==[("bootout",QWEN38.profile_id),("proved_down",QWEN38.profile_id),("start",QWEN36.profile_id),("captured",QWEN36.profile_id),("bootout",QWEN36.profile_id),("proved_down",QWEN36.profile_id),("start",QWEN38.profile_id),("captured",QWEN38.profile_id)]


def test_i_cold_fast_restores_eligible_main():
    runtime,main,fast,_=factory(main=False,fast=False)
    with runtime.session("FAST") as selected: assert selected is fast and fast.healthy and not main.healthy
    assert main.healthy and not fast.healthy


def test_k_restore_failure_keeps_exactly_one_fallback():
    runtime,main,fast,_=factory(main=True,fast=False,start_failure=QWEN38.profile_id)
    with runtime.session("FAST") as selected: assert selected is fast
    assert fast.healthy and not main.healthy


def test_h_unknown_listener_and_m_invalid_identity_fail_closed(tmp_path):
    runner=Runner(); unexpected=ProcessIdentity(91,"/unknown",("unknown",),"START")
    lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path,sleep=lambda _:None,runner=runner,snapshot=lambda _pid:unexpected,listeners=lambda _port:(91,))
    with pytest.raises(HeavyModelConflict): lifecycle.capture_started(QWEN38.profile_id)
    lifecycle._identity_path(QWEN38.profile_id).write_text("not-json")
    with pytest.raises(HeavyModelConflict): lifecycle.safe_stop(QWEN38.profile_id,lambda:True)

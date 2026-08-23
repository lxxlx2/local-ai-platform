import ast
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import urllib.error

import pytest

from local_ai_control.bot.app import owner_image_reply
from local_ai_control.services.authorization import AuthorizationDenied
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.async_runtime import AsyncRuntimeExecutor, sync_chat_reply
from local_ai_control.services.models import ModelRegistry, QWEN36, QWEN38
from local_ai_control.services.omlx import ModelReply
from local_ai_control.services.qwen38_runtime import ContextLimitExceeded, Qwen38SidecarEngine, RuntimeUnavailable
from local_ai_control.services.runtime_providers import HeavyModelConflict, RuntimeProviderFactory
from local_ai_control.services.security import SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository
from local_ai_control.services.vision import TelegramImageService


class Tracker:
    def __init__(self): self.active=0; self.maximum=0; self.lock=threading.Lock()
    @contextmanager
    def enter(self):
        with self.lock:
            self.active+=1; self.maximum=max(self.maximum,self.active)
        try: yield
        finally:
            with self.lock: self.active-=1


class Provider:
    def __init__(self,name,tracker,*,healthy=False,delay=.04,error=None):
        self.name=name; self.tracker=tracker; self.healthy=healthy; self.delay=delay; self.error=error
        self.generate_calls=0; self.vision_calls=0
    def health(self):
        if not self.healthy: raise OSError("down")
        return {"status":"healthy"}
    def generate(self,prompt,max_output_tokens=1024):
        self.generate_calls+=1
        with self.tracker.enter():
            time.sleep(self.delay)
            if self.error: raise self.error
            return ModelReply(self.name,"completed",None,1,max_output_tokens)
    def vision(self,path,prompt,max_output_tokens=1024):
        self.vision_calls+=1
        with self.tracker.enter():
            time.sleep(self.delay)
            if self.error: raise self.error
            return ModelReply(self.name+"-vision","completed",None,1,max_output_tokens)


class Lifecycle:
    def __init__(self,main,fast,*,partial_fail=None,refuse_stop=None):
        self.main=main; self.fast=fast; self.partial_fail=partial_fail; self.refuse_stop=refuse_stop; self.events=[]
    def stop(self,profile_id):
        self.events.append(("stop",profile_id))
        if profile_id==self.refuse_stop: return
        (self.main if profile_id==QWEN38.profile_id else self.fast).healthy=False
    def start(self,profile_id):
        self.events.append(("start",profile_id))
        target=self.main if profile_id==QWEN38.profile_id else self.fast
        target.healthy=True
        if profile_id==self.partial_fail: raise RuntimeError("partial start")


class Preflight:
    def check(self,*_args,**_kwargs): return SimpleNamespace(allowed=True,reason="OK")


def runtime(*,main_healthy=True,fast_healthy=False,main_error=None,fast_error=None,registry=None,partial_fail=None,refuse_stop=None,delay=.04):
    tracker=Tracker(); main=Provider("main",tracker,healthy=main_healthy,error=main_error,delay=delay)
    fast=Provider("fast",tracker,healthy=fast_healthy,error=fast_error,delay=delay)
    lifecycle=Lifecycle(main,fast,partial_fail=partial_fail,refuse_stop=refuse_stop)
    factory=RuntimeProviderFactory(registry or ModelRegistry(),main=main,fast=fast,preflight=Preflight(),lifecycle=lifecycle,sleep=lambda _:None)
    return factory,main,fast,lifecycle,tracker


class Bot:
    def __init__(self,payload=b"\xff\xd8\xffsynthetic"): self.payload=payload; self.downloads=0
    async def download(self,_ref,destination):
        self.downloads+=1; await asyncio.sleep(.01); Path(destination).write_bytes(self.payload)


def repository(tmp_path):
    repo=ScopedSQLiteRepository(tmp_path/"chat.db","private"); repo.migrate()
    identity=identity_from_telegram(7,"7"); return repo,identity,repo.create_session(identity)


def test_async_admission_serializes_chat_fast_chat_image_and_two_images(tmp_path):
    factory,main,fast,_,tracker=runtime(delay=.05)
    executor=AsyncRuntimeExecutor(factory); repo,identity,session=repository(tmp_path)
    service=TelegramImageService(main,inbox_root=tmp_path/"inbox",spool_root=tmp_path/"spool")
    async def scenario():
        first=await service.stage(Bot(),"one",declared_size=len(Bot().payload),caption="one")
        second=await service.stage(Bot(),"two",declared_size=len(Bot().payload),caption="two")
        results=await asyncio.gather(
            executor.chat(repo,SecretFirewall(),identity,session,"普通聊天"),
            executor.chat(repo,SecretFirewall(),identity,session,"/fast 快速聊天"),
            executor.vision(service,first),
            executor.vision(service,second),
        )
        assert len(results)==4
    try: asyncio.run(scenario())
    finally: executor.shutdown(); repo.close()
    assert tracker.maximum==1 and main.generate_calls==1 and fast.generate_calls==1 and main.vision_calls==2
    assert not list((tmp_path/"spool").iterdir())


def test_event_loop_heartbeat_remains_responsive_during_slow_inference(tmp_path):
    factory,_,_,_,_=runtime(delay=.2); executor=AsyncRuntimeExecutor(factory); repo,identity,session=repository(tmp_path)
    async def scenario():
        started=time.monotonic()
        inference=asyncio.create_task(executor.chat(repo,SecretFirewall(),identity,session,"慢请求"))
        await asyncio.sleep(.02)
        heartbeat=time.monotonic()-started
        await inference
        return heartbeat
    try: heartbeat=asyncio.run(scenario())
    finally: executor.shutdown(); repo.close()
    assert heartbeat<.12


def test_explicit_async_cancellation_never_triggers_failover(tmp_path):
    factory,main,fast,_,_=runtime(delay=.12); executor=AsyncRuntimeExecutor(factory); repo,identity,session=repository(tmp_path)
    async def scenario():
        task=asyncio.create_task(executor.chat(repo,SecretFirewall(),identity,session,"cancel me"))
        await asyncio.sleep(.02); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    try: asyncio.run(scenario())
    finally: executor.shutdown(); repo.close()
    assert main.generate_calls==1 and fast.generate_calls==0


def test_no_provider_thread_lock_is_acquired_across_await():
    roots=(Path("/Users/jerson/AI/control-plane/src/local_ai_control/services/async_runtime.py"),Path("/Users/jerson/AI/control-plane/src/local_ai_control/bot/app.py"))
    for path in roots:
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.AsyncFunctionDef,ast.AsyncWith)):
                segment=ast.get_source_segment(path.read_text(),node) or ""
                assert "with provider_factory.session" not in segment


def test_main_inference_death_fails_over_once_without_history_duplication(tmp_path):
    factory,main,fast,lifecycle,_=runtime(main_error=RuntimeUnavailable("dead"))
    repo,identity,session=repository(tmp_path)
    result=sync_chat_reply(factory,repo,SecretFirewall(),identity,session,"hello")
    counts=dict(repo.db.execute("SELECT role,COUNT(*) FROM messages GROUP BY role").fetchall())
    repo.close()
    assert result.text=="fast" and main.generate_calls==1 and fast.generate_calls==1
    assert counts=={"user":1,"assistant":1}
    assert lifecycle.events==[("stop",QWEN38.profile_id),("start",QWEN36.profile_id)]


def test_main_and_fallback_failure_is_bounded_and_user_is_stored_once(tmp_path):
    factory,main,fast,_,_=runtime(main_error=RuntimeUnavailable("dead"),fast_error=RuntimeUnavailable("also dead"))
    repo,identity,session=repository(tmp_path)
    with pytest.raises(RuntimeUnavailable): sync_chat_reply(factory,repo,SecretFirewall(),identity,session,"hello")
    counts=dict(repo.db.execute("SELECT role,COUNT(*) FROM messages GROUP BY role").fetchall())
    repo.close()
    assert main.generate_calls==1 and fast.generate_calls==1 and counts=={"user":1}


def test_context_limit_does_not_fail_over(tmp_path):
    factory,main,fast,lifecycle,_=runtime(main_error=ContextLimitExceeded("limit"))
    repo,identity,session=repository(tmp_path)
    with pytest.raises(ContextLimitExceeded): sync_chat_reply(factory,repo,SecretFirewall(),identity,session,"hello")
    repo.close()
    assert main.generate_calls==1 and fast.generate_calls==0 and lifecycle.events==[]


@pytest.mark.parametrize("rejection",[ValueError("invalid"),PermissionError("denied")])
def test_validation_and_authorization_rejections_do_not_fail_over(tmp_path,rejection):
    factory,main,fast,lifecycle,_=runtime(main_error=rejection)
    repo,identity,session=repository(tmp_path)
    with pytest.raises(type(rejection)):
        sync_chat_reply(factory,repo,SecretFirewall(),identity,session,"hello")
    repo.close()
    assert main.generate_calls==1 and fast.generate_calls==0 and lifecycle.events==[]


def unqualified_main_registry(tmp_path):
    payload=json.loads(Path("/Users/jerson/AI/config/model-registry-v0.1.json").read_text())
    payload["production_aliases"]["MAIN"]={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"}
    path=tmp_path/"registry.json"; path.write_text(json.dumps(payload)); return ModelRegistry(config_path=path)


def test_healthy_unqualified_main_is_physically_stopped_before_fast_starts(tmp_path):
    factory,main,fast,lifecycle,_=runtime(registry=unqualified_main_registry(tmp_path))
    with factory.session("CHAT") as provider: assert provider is fast
    assert lifecycle.events==[("stop",QWEN38.profile_id),("start",QWEN36.profile_id)]
    assert not main.healthy and fast.healthy


def test_partial_target_start_is_confirmed_down_before_rollback():
    factory,main,fast,lifecycle,_=runtime(main_healthy=False,fast_healthy=True,partial_fail=QWEN38.profile_id)
    with factory.session("CHAT") as provider: assert provider is fast
    assert lifecycle.events==[("stop",QWEN36.profile_id),("start",QWEN38.profile_id),("stop",QWEN38.profile_id),("start",QWEN36.profile_id)]
    assert not main.healthy and fast.healthy


def test_failed_target_cleanup_refuses_two_runtime_rollback():
    factory,main,fast,_,_=runtime(main_healthy=False,fast_healthy=True,partial_fail=QWEN38.profile_id,refuse_stop=QWEN38.profile_id)
    with pytest.raises(HeavyModelConflict):
        with factory.session("CHAT"): pass
    assert main.healthy and not fast.healthy


def test_vision_spool_is_removed_on_success_failure_and_ttl_recovers_crash(tmp_path):
    factory,main,_,_,_=runtime(); executor=AsyncRuntimeExecutor(factory)
    service=TelegramImageService(main,inbox_root=tmp_path/"inbox",spool_root=tmp_path/"spool",ttl_seconds=1)
    async def scenario():
        request=await service.stage(Bot(),"ok",declared_size=len(Bot().payload))
        assert (request.path.stat().st_mode&0o777)==0o600
        assert (service.inbox_root.stat().st_mode&0o777)==0o700 and (service.spool.root.stat().st_mode&0o777)==0o700
        assert await executor.vision(service,request)=="main-vision"
        assert not request.path.exists()
        failed=await service.stage(Bot(),"fail",declared_size=len(Bot().payload))
        main.error=RuntimeUnavailable("vision failed")
        with pytest.raises(RuntimeUnavailable): await executor.vision(service,failed)
        assert not failed.path.exists()
    try: asyncio.run(scenario())
    finally: executor.shutdown()
    stale=tmp_path/"spool"/"crash.jpg"; stale.write_bytes(b"\xff\xd8\xffold"); os.chmod(stale,0o600); os.utime(stale,(time.time()-10,time.time()-10))
    assert service.spool.cleanup()==1 and not stale.exists()


def test_public_image_request_downloads_nothing(tmp_path):
    factory,main,_,_,_=runtime(); executor=AsyncRuntimeExecutor(factory)
    service=TelegramImageService(main,inbox_root=tmp_path/"inbox",spool_root=tmp_path/"spool"); bot=Bot()
    async def scenario():
        with pytest.raises(AuthorizationDenied):
            await owner_image_reply(Role.PUBLIC,service,executor,bot,"private",declared_size=len(bot.payload))
    try: asyncio.run(scenario())
    finally: executor.shutdown()
    assert bot.downloads==0 and not list((tmp_path/"spool").iterdir())


class Tokenizer:
    def encode(self,text):
        count=len(text) if any("\u4e00"<=char<="\u9fff" for char in text) else max(1,len(text)//4)
        return list(range(count))


def budget_engine():
    engine=Qwen38SidecarEngine.__new__(Qwen38SidecarEngine)
    engine.processor=SimpleNamespace(tokenizer=Tokenizer()); engine.config=None; engine.max_context_tokens=16384
    engine.apply_chat_template=lambda _processor,_config,prompt,num_images=0:prompt
    engine.lock=threading.Lock(); engine.seen=[]
    def stream(_model,_processor,prompt,**kwargs):
        engine.seen.append(kwargs["max_tokens"])
        yield SimpleNamespace(text="ok",finish_reason="stop",prompt_tokens=len(engine.processor.tokenizer.encode(prompt)),generation_tokens=1)
    engine.stream_generate=stream; engine.model=object(); return engine


@pytest.mark.parametrize("prompt",["中"*100,"english words "*100])
def test_total_context_budget_handles_chinese_and_english(prompt):
    engine=budget_engine(); result=engine.text(prompt,200)
    assert result["exact_prompt_tokens"]+result["max_output_tokens"]<=16384


def test_near_limit_clamps_output_and_no_safe_output_returns_413():
    engine=budget_engine(); result=engine.text("中"*16000,1024)
    assert result["exact_prompt_tokens"]==16000 and result["max_output_tokens"]==384 and engine.seen==[384]
    with pytest.raises(ContextLimitExceeded): engine.text("中"*16369,1024)


def test_qwen38_http_validation_and_authorization_errors_are_not_infrastructure(monkeypatch):
    from local_ai_control.services.qwen38_runtime import Qwen38Provider
    provider=Qwen38Provider()
    def rejected(code):
        def open_(*_args,**_kwargs):
            raise urllib.error.HTTPError("http://127.0.0.1",code,"rejected",{},None)
        return open_
    monkeypatch.setattr("urllib.request.urlopen",rejected(400))
    with pytest.raises(ValueError): provider.generate("valid prompt")
    monkeypatch.setattr("urllib.request.urlopen",rejected(403))
    with pytest.raises(PermissionError): provider.generate("valid prompt")

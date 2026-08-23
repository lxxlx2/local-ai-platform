import asyncio
from contextlib import contextmanager
from http.server import HTTPServer
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import urllib.error
import urllib.request

import pytest

from local_ai_control.bot.app import chat_reply_with_runtime, owner_image_reply
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.models import ModelRegistry, QWEN36, QWEN38
from local_ai_control.services.omlx import ModelReply
from local_ai_control.services.qwen38_runtime import ContextLimitExceeded, MODEL_ID, Qwen38Provider, handler_for
from local_ai_control.services.runtime_providers import HeavyModelConflict, RuntimeProviderFactory
from local_ai_control.services.runtime_providers import LaunchdHeavyRuntimeLifecycle
from local_ai_control.services.security import SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository
from local_ai_control.services.vision import TelegramImageService


class FakeProvider:
    def __init__(self, healthy=False, text="ok"):
        self.healthy=healthy; self.text=text; self.generated=[]; self.vision_calls=[]
    def health(self):
        if not self.healthy: raise OSError("not running")
        return {"status":"healthy"}
    def generate(self,prompt,max_output_tokens=1024):
        self.generated.append((prompt,max_output_tokens))
        return ModelReply(self.text,"completed",None,2,max_output_tokens)
    def vision(self,path,prompt,max_output_tokens=1024):
        self.vision_calls.append((Path(path),prompt))
        assert Path(path).is_file() and (os.stat(path).st_mode & 0o777)==0o600
        return ModelReply("蓝色方块和红色圆形","completed",None,8,max_output_tokens)


class FakeLifecycle:
    def __init__(self,main,fast,fail=None): self.main=main; self.fast=fast; self.fail=fail; self.events=[]
    def stop(self,profile_id):
        self.events.append(("stop",profile_id))
        (self.main if profile_id==QWEN38.profile_id else self.fast).healthy=False
    def start(self,profile_id):
        self.events.append(("start",profile_id))
        if profile_id==self.fail: raise RuntimeError("start failed")
        self.main.healthy=profile_id==QWEN38.profile_id
        self.fast.healthy=profile_id==QWEN36.profile_id
    def activate(self,profile_id):
        other=QWEN36.profile_id if profile_id==QWEN38.profile_id else QWEN38.profile_id
        self.stop(other); self.start(profile_id)


class AllowPreflight:
    def __init__(self,allowed=True): self.allowed=allowed; self.calls=[]
    def check(self,required_gib,*,owned_reclaimable_gib=0):
        self.calls.append((required_gib,owned_reclaimable_gib))
        return SimpleNamespace(allowed=self.allowed,reason="OK" if self.allowed else "DENIED")


def factory(main=False,fast=False,*,fail=None,registry=None,preflight=None):
    main_provider=FakeProvider(main,"main")
    fast_provider=FakeProvider(fast,"fast")
    lifecycle=FakeLifecycle(main_provider,fast_provider,fail)
    result=RuntimeProviderFactory(registry or ModelRegistry(),main=main_provider,fast=fast_provider,
                                  lifecycle=lifecycle,preflight=preflight or AllowPreflight(),sleep=lambda _:None)
    return result,main_provider,fast_provider,lifecycle


def test_normal_chat_promotes_qualified_main_and_explicit_fast_restores_main():
    runtime,main,fast,lifecycle=factory(main=False,fast=True)
    with runtime.session("CHAT") as provider: assert provider is main
    assert lifecycle.events==[("stop",QWEN36.profile_id),("start",QWEN38.profile_id)]
    with runtime.session("FAST") as provider: assert provider is fast
    assert lifecycle.events[-4:]==[("stop",QWEN38.profile_id),("start",QWEN36.profile_id),("stop",QWEN36.profile_id),("start",QWEN38.profile_id)]
    assert main.healthy and not fast.healthy


def test_main_start_failure_deterministically_restores_fallback():
    runtime,main,fast,lifecycle=factory(main=False,fast=True,fail=QWEN38.profile_id)
    with runtime.session("CHAT") as provider: assert provider is fast
    assert fast.healthy and not main.healthy
    assert lifecycle.events==[("stop",QWEN36.profile_id),("start",QWEN38.profile_id),("start",QWEN36.profile_id)]


def test_unknown_or_unowned_resident_process_is_never_killed_or_overlapped():
    runtime,main,fast,lifecycle=factory(main=False,fast=True)
    lifecycle.stop=lambda profile_id: lifecycle.events.append(("refused-stop",profile_id))
    with runtime.session("CHAT") as provider: assert provider is fast
    assert not main.healthy and fast.healthy
    assert lifecycle.events==[("refused-stop",QWEN36.profile_id)]
    assert not any(event[0]=="start" for event in lifecycle.events)


def test_runtime_conflict_fails_closed_and_unqualified_main_uses_fast(tmp_path):
    runtime,_,_,_=factory(main=True,fast=True)
    with pytest.raises(HeavyModelConflict): runtime.state()
    payload=json.loads(Path("/Users/jerson/AI/config/model-registry-v0.1.json").read_text())
    payload["production_aliases"]["MAIN"]={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"}
    path=tmp_path/"registry.json"; path.write_text(json.dumps(payload))
    unqualified=ModelRegistry(config_path=path)
    runtime,main,fast,lifecycle=factory(main=False,fast=True,registry=unqualified)
    with runtime.session("CHAT") as provider: assert provider is fast
    assert not lifecycle.events and not main.generated


def test_production_chat_helper_selects_main_and_fast_without_model_injection(tmp_path):
    runtime,main,fast,_=factory(main=True,fast=False)
    repo=ScopedSQLiteRepository(tmp_path/"private.db","private"); repo.migrate()
    identity=identity_from_telegram(7,"7"); session=repo.create_session(identity)
    assert chat_reply_with_runtime(runtime,repo,SecretFirewall(),identity,session,"你好").text=="main"
    assert chat_reply_with_runtime(runtime,repo,SecretFirewall(),identity,session,"/fast 快速回答").text=="fast"
    assert main.generated and fast.generated
    assert "快速回答" in fast.generated[-1][0]
    with pytest.raises(ValueError): chat_reply_with_runtime(runtime,repo,SecretFirewall(),identity,session,"/fast")
    repo.close()


def test_context_cap_rejects_before_network():
    provider=Qwen38Provider(port=65534,max_context_tokens=16384)
    provider._request=lambda *_args,**_kwargs: (_ for _ in ()).throw(AssertionError("network reached"))
    with pytest.raises(ContextLimitExceeded): provider.generate("字"*16385)


def test_lifecycle_only_controls_owned_launchd_labels_and_never_kills_unknown(monkeypatch,tmp_path):
    import local_ai_control.services.runtime_providers as runtime_module
    calls=[]
    monkeypatch.setattr(runtime_module.subprocess,"check_output",lambda *a,**k:"501\n")
    monkeypatch.setattr(runtime_module.subprocess,"run",lambda argv,**kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    lifecycle=LaunchdHeavyRuntimeLifecycle(tmp_path)
    lifecycle.stop(QWEN38.profile_id)
    assert calls==[["launchctl","bootout","gui/501/local-ai.qwen38-runtime"]]
    source=Path(runtime_module.__file__).read_text()
    assert "os.kill" not in source and "pkill" not in source and "killall" not in source


class FakeEngine:
    max_context_tokens=16384
    def text(self,prompt,limit):
        if prompt=="oversized": raise ContextLimitExceeded("too large")
        return {"status":"completed","output_text":"ok","usage":{"output_tokens":1},"max_output_tokens":limit}
    def vision(self,image_ref,prompt,limit): return self.text(prompt,limit)


@contextmanager
def fake_sidecar():
    server=HTTPServer(("127.0.0.1",0),handler_for(FakeEngine()))
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield server.server_port
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


def post(port,payload):
    request=urllib.request.Request(f"http://127.0.0.1:{port}/v1/responses",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    return urllib.request.urlopen(request,timeout=2)


def test_sidecar_contract_is_local_bounded_and_denies_model_injection():
    with fake_sidecar() as port:
        provider=Qwen38Provider(port=port)
        assert provider.health()["model"]==MODEL_ID
        assert provider.generate("hello").text=="ok"
        with pytest.raises(urllib.error.HTTPError) as wrong:
            post(port,{"model":"attacker/repo","input":"hello","max_output_tokens":1})
        assert wrong.value.code==400
        with pytest.raises(urllib.error.HTTPError) as oversized:
            post(port,{"model":MODEL_ID,"input":"oversized","max_output_tokens":1})
        assert oversized.value.code==413


class FakeBot:
    def __init__(self,payload): self.payload=payload; self.downloads=0
    async def download(self,file_ref,destination):
        self.downloads+=1; Path(destination).write_bytes(self.payload)


def test_owner_image_ingestion_is_private_validated_and_public_is_denied(tmp_path):
    provider=FakeProvider(True)
    service=TelegramImageService(provider,inbox_root=tmp_path/"inbox",spool_root=tmp_path/"spool",ttl_seconds=1)
    @contextmanager
    def provider_session(_task):
        yield provider
    runtime=SimpleNamespace(session=provider_session)
    bot=FakeBot(b"\xff\xd8\xffsafe-jpeg")
    answer=asyncio.run(owner_image_reply(Role.OWNER,service,runtime,bot,"photo",declared_size=len(bot.payload),caption="描述"))
    assert "蓝色方块" in answer and bot.downloads==1 and len(provider.vision_calls)==1
    assert not list((tmp_path/"inbox").iterdir())
    with pytest.raises(Exception):
        asyncio.run(owner_image_reply(Role.PUBLIC,service,runtime,bot,"photo",declared_size=len(bot.payload)))
    assert bot.downloads==1
    bad=FakeBot(b"not-an-image")
    with pytest.raises(ValueError):
        asyncio.run(owner_image_reply(Role.OWNER,service,runtime,bad,"photo",declared_size=len(bad.payload)))
    assert len(provider.vision_calls)==1

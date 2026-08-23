"""Bounded async admission for synchronous model lifecycle and inference."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import re

from local_ai_control.services.chat import ChatService


class FastPromptRequired(ValueError): pass


def parse_chat_request(text):
    """Select only a fixed runtime class; Telegram cannot supply model IDs."""
    match=re.match(r"^/fast(?:\s+([\s\S]+))?$",text.strip(),flags=re.I)
    if match:
        if not match.group(1): raise FastPromptRequired("FAST_PROMPT_REQUIRED")
        return "FAST",match.group(1).strip()
    return "CHAT",text


class RoutedChatProvider:
    def __init__(self,factory,task_type): self.factory=factory; self.task_type=task_type
    def generate(self,prompt,max_output_tokens=1024):
        return self.factory.generate(self.task_type,prompt,max_output_tokens=max_output_tokens)


def sync_chat_reply(provider_factory,repository,firewall,ctx,session_id,text):
    task,prompt=parse_chat_request(text)
    provider=RoutedChatProvider(provider_factory,task)
    # ChatService is called exactly once. Its single provider call may perform
    # one infrastructure failover internally, so history is never duplicated.
    return ChatService(repository,provider,firewall).reply(ctx,session_id,prompt)


class AsyncRuntimeExecutor:
    """One asyncio admission lock plus one dedicated synchronous worker.

    The worker owns the entire provider session and inference. Cancellation is
    delayed until the non-cancellable urllib/Metal call finishes, keeping the
    admission lock held so a second heavy operation cannot overlap it.
    """
    def __init__(self,provider_factory):
        self.provider_factory=provider_factory
        self._admission=asyncio.Lock()
        self._worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix="local-ai-heavy")

    async def _run(self,function,*args,**kwargs):
        async with self._admission:
            loop=asyncio.get_running_loop()
            future=loop.run_in_executor(self._worker,partial(function,*args,**kwargs))
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Never release admission while the synchronous call is alive.
                try: await future
                finally: raise

    async def chat(self,repository,firewall,ctx,session_id,text):
        return await self._run(sync_chat_reply,self.provider_factory,repository,firewall,ctx,session_id,text)

    @staticmethod
    def _vision_sync(provider_factory,image_service,request):
        with provider_factory.session("VISION") as provider:
            return image_service.infer(request,provider)

    async def vision(self,image_service,request):
        try:
            return await self._run(self._vision_sync,self.provider_factory,image_service,request)
        finally:
            image_service.discard(request)

    async def runtime_health(self):
        return await self._run(self.provider_factory.runtime_health)

    async def call(self,function,*args,**kwargs):
        return await self._run(function,*args,**kwargs)

    def shutdown(self):
        # Polling shutdown is outside request handling. Drain any admitted call
        # before repositories are closed, rather than leaving a worker racing a
        # SQLite close during process teardown.
        self._worker.shutdown(wait=True,cancel_futures=True)

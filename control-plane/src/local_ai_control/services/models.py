"""Unified registry and mutually-exclusive heavy-model lifecycle.

Registration is metadata, never proof that a model is production-qualified.
Runtime adapters are injected so model dependencies stay out of the control-plane
and existing oMLX virtual environments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import re
import subprocess
import threading
import time
from typing import Callable, Protocol


class ModelRole(StrEnum):
    MAIN="MAIN"; FAST="FAST"; FALLBACK="FALLBACK"; RAW="RAW"; DEEP="DEEP"
    CODE="CODE"; REVIEW="REVIEW"; VISION="VISION"; VIDEO_UNDERSTANDING="VIDEO_UNDERSTANDING"
    STT_MAIN="STT_MAIN"; TTS_MAIN="TTS_MAIN"; TTS_DESIGN="TTS_DESIGN"
    IMAGE_MAIN="IMAGE_MAIN"; VIDEO_MAIN="VIDEO_MAIN"; VIDEO_HIGH="VIDEO_HIGH"
    EMBED="EMBED"; RERANK="RERANK"
    AUDIO="AUDIO"; IMAGE="IMAGE"; VIDEO="VIDEO"  # compatibility aliases


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str; display_name: str; provider_id: str; model_id: str
    roles: dict[ModelRole, str]; local_or_remote: str; data_egress: str
    benchmark_version: str | None = None; benchmark_score: float | None = None
    last_evaluated: str | None = None; precision: str | None = None
    heavy: bool = True; owner_only: bool = False; runtime_env: str | None = None
    local_path: str | None = None; expected_memory_gib: float | None = None


QWEN36 = ModelProfile(
    "local-qwen36", "Qwen3.6-35B-A3B-4bit", "local-omlx",
    "mlx-community/Qwen3.6-35B-A3B-4bit",
    {ModelRole.FAST:"CURRENT", ModelRole.FALLBACK:"CURRENT", ModelRole.MAIN:"VALIDATED",
     ModelRole.DEEP:"NOT_QUALIFIED", ModelRole.CODE:"NO", ModelRole.REVIEW:"LIMITED"},
    "LOCAL", "NONE", "v1", precision="4bit", runtime_env="/Users/jerson/AI/runtime/omlx-venv",
    local_path="/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit", expected_memory_gib=28,
)
QWEN38 = ModelProfile(
    "local-qwen38", "Qwen3.8-27B-8bit", "local-mlx-vlm", "mlx-community/Qwen3.8-27B-8bit",
    {ModelRole.MAIN:"REGISTERED", ModelRole.VISION:"REGISTERED",
     ModelRole.VIDEO_UNDERSTANDING:"REGISTERED", ModelRole.DEEP:"REGISTERED"},
    "LOCAL", "NONE", precision="8bit", runtime_env="/Users/jerson/AI/runtime/qwen38-venv",
    local_path="/Users/jerson/AI/models/qwen38-27b-8bit", expected_memory_gib=34,
)


def _profile(pid, name, provider, model_id, role, *, precision=None, owner_only=False,
             runtime_env=None, expected_memory_gib=None):
    return ModelProfile(pid, name, provider, model_id, {role:"REGISTERED"}, "LOCAL", "NONE",
                        precision=precision, owner_only=owner_only, runtime_env=runtime_env,
                        expected_memory_gib=expected_memory_gib)


DEFAULT_MODELS = (
    QWEN36, QWEN38,
    _profile("owner-qwen38-raw", "Qwen3.8-27B RAW 8-bit", "local-mlx-vlm",
             "OrcaRouter/Qwen3.8-27B-Uncensored-Abliterated-8bit-MLX", ModelRole.RAW,
             precision="8bit", owner_only=True, runtime_env="/Users/jerson/AI/runtime/qwen38-venv", expected_memory_gib=34),
    _profile("whisper-large-v3", "Whisper large-v3 MLX", "local-mlx-audio",
             "mlx-community/whisper-large-v3-mlx", ModelRole.STT_MAIN,
             runtime_env="/Users/jerson/AI/runtime/audio-venv", expected_memory_gib=6),
    _profile("qwen3-tts-base", "Qwen3-TTS 1.7B Base BF16", "local-mlx-audio",
             "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", ModelRole.TTS_MAIN,
             precision="bf16", owner_only=True, runtime_env="/Users/jerson/AI/runtime/audio-venv", expected_memory_gib=8),
    _profile("qwen3-tts-design", "Qwen3-TTS 1.7B VoiceDesign BF16", "local-mlx-audio",
             "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16", ModelRole.TTS_DESIGN,
             precision="bf16", owner_only=True, runtime_env="/Users/jerson/AI/runtime/audio-venv", expected_memory_gib=8),
    _profile("flux2-klein", "FLUX.2 klein 4B BF16", "local-mlx-image",
             "mlx-community/FLUX.2-klein-4B-bf16", ModelRole.IMAGE_MAIN,
             precision="bf16", owner_only=True, runtime_env="/Users/jerson/AI/runtime/image-venv", expected_memory_gib=30),
    _profile("longcat-video", "LongCat Video q8", "local-mlx-video", "mlx-community/LongCat-Video-q8",
             ModelRole.VIDEO_HIGH, precision="q8", owner_only=True,
             runtime_env="/Users/jerson/AI/runtime/video-venv", expected_memory_gib=44),
    _profile("wan21-video", "Wan2.1 14B MLX distilled", "local-mlx-video",
             "REGISTERED_CANDIDATE_REQUIRES_EXACT_REPO", ModelRole.VIDEO_MAIN,
             owner_only=True, runtime_env="/Users/jerson/AI/runtime/video-venv", expected_memory_gib=32),
    _profile("qwen3-embedding", "Qwen3 Embedding 8B", "local-rag", "Qwen/Qwen3-Embedding-8B",
             ModelRole.EMBED, runtime_env="/Users/jerson/AI/runtime/rag-venv", expected_memory_gib=20),
    _profile("qwen3-reranker", "Qwen3 Reranker 8B", "local-rag", "Qwen/Qwen3-Reranker-8B",
             ModelRole.RERANK, runtime_env="/Users/jerson/AI/runtime/rag-venv", expected_memory_gib=20),
)


class ModelRoleRegistry:
    def __init__(self, models=DEFAULT_MODELS): self.models={m.profile_id:m for m in models}
    def eligible(self, role):
        return [m for m in self.models.values() if m.roles.get(role) in {"CURRENT","VALIDATED"}]
    def registered(self, role): return [m for m in self.models.values() if role in m.roles]
    def status(self, role):
        choices=self.eligible(role); return choices[0].profile_id if choices else "NOT_AVAILABLE"
    def require(self, profile_id, *, owner):
        if profile_id not in self.models: raise LookupError("model is not registered")
        result=self.models[profile_id]
        if result.owner_only and not owner: raise PermissionError("model is owner-only")
        return result


class ModelRegistry(ModelRoleRegistry): pass


class ModelRouter:
    _TASK_ROLE={"CHAT":ModelRole.FAST,"MAIN":ModelRole.MAIN,"CODE":ModelRole.CODE,"REVIEW":ModelRole.REVIEW,
                "VISION":ModelRole.VISION,"VIDEO_UNDERSTANDING":ModelRole.VIDEO_UNDERSTANDING,
                "AUDIO":ModelRole.STT_MAIN,"STT":ModelRole.STT_MAIN,"TTS":ModelRole.TTS_MAIN,
                "EMBEDDING":ModelRole.EMBED,"RERANK":ModelRole.RERANK,"IMAGE":ModelRole.IMAGE_MAIN,
                "VIDEO":ModelRole.VIDEO_MAIN,"DEEP_REASONING":ModelRole.DEEP}
    def __init__(self, registry=None): self.registry=registry or ModelRegistry()
    def route(self, task_type, user_override=None):
        role=self._TASK_ROLE[task_type]
        if user_override:
            candidate=self.registry.models.get(user_override)
            if candidate and candidate.roles.get(role) in {"CURRENT","VALIDATED"}: return candidate
            raise PermissionError("requested model is not eligible for this role")
        choices=self.registry.eligible(role)
        if not choices: raise LookupError(f"no validated model for {role}")
        return choices[0]


@dataclass(frozen=True)
class MemorySnapshot:
    total_gib: float; available_gib: float; swap_used_gib: float; pressure: str
    timestamp: str=field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class MemoryPreflightResult:
    allowed: bool; required_gib: float; available_gib: float; reason: str; snapshot: MemorySnapshot


class MemoryPreflight:
    def __init__(self, probe: Callable[[],MemorySnapshot]|None=None, reserve_gib=6):
        self.probe=probe or self._macos_snapshot; self.reserve_gib=reserve_gib
    @staticmethod
    def _macos_snapshot():
        total=int(subprocess.check_output(["sysctl","-n","hw.memsize"],text=True).strip())
        page=int(subprocess.check_output(["sysctl","-n","hw.pagesize"],text=True).strip())
        vm=subprocess.check_output(["vm_stat"],text=True); pages={}
        for line in vm.splitlines()[1:]:
            if ":" in line:
                k,v=line.split(":",1); pages[k]=int(v.strip().rstrip("."))
        available=(pages.get("Pages free",0)+pages.get("Pages inactive",0)+pages.get("Pages speculative",0))*page
        swap_text=subprocess.check_output(["sysctl","-n","vm.swapusage"],text=True)
        match=re.search(r"used = ([0-9.]+)([MG])",swap_text)
        swap=0 if not match else float(match.group(1))/(1024 if match.group(2)=="M" else 1)
        ratio=available/total if total else 0
        pressure="NORMAL" if ratio>=.25 else ("WARNING" if ratio>=.12 else "CRITICAL")
        return MemorySnapshot(total/1024**3,available/1024**3,swap,pressure)
    def check(self, required_gib):
        snapshot=self.probe(); allowed=snapshot.pressure!="CRITICAL" and snapshot.available_gib>=required_gib+self.reserve_gib
        reason="OK" if allowed else ("MEMORY_PRESSURE_CRITICAL" if snapshot.pressure=="CRITICAL" else "INSUFFICIENT_AVAILABLE_MEMORY")
        return MemoryPreflightResult(allowed,required_gib,snapshot.available_gib,reason,snapshot)


@dataclass(frozen=True)
class ModelHealth: healthy: bool; detail: str=""
@dataclass(frozen=True)
class ModelSwitchResult:
    requested_profile_id: str; status: str; active_profile_id: str|None
    rolled_back_to: str|None=None; failure_category: str|None=None; load_seconds: float|None=None
@dataclass(frozen=True)
class HeavyModelLease: profile_id: str; acquired_at: str


class RuntimeAdapter(Protocol):
    def load(self, profile): ...
    def unload(self, profile): ...
    def health(self, profile): ...


class ModelManager:
    def __init__(self, registry, adapters, preflight, sleep=time.sleep):
        self.registry=registry; self.adapters=adapters; self.preflight=preflight; self.sleep=sleep
        self._lock=threading.RLock(); self._active=None
    @property
    def active_profile_id(self): return self._active.profile_id if self._active else None
    def request(self, profile_id, *, owner=False):
        with self._lock:
            target=self.registry.require(profile_id,owner=owner)
            if self._active and self._active.profile_id==target.profile_id:
                health=self.adapters[target.provider_id].health(target)
                return ModelSwitchResult(profile_id,"READY" if health.healthy else "UNHEALTHY",self.active_profile_id)
            previous=self._active
            if previous and previous.heavy:
                self.adapters[previous.provider_id].unload(previous); self._active=None; self.sleep(0)
            check=self.preflight.check(target.expected_memory_gib or 0)
            if not check.allowed: return self._rollback(target,previous,check.reason)
            started=time.monotonic()
            try:
                adapter=self.adapters[target.provider_id]; adapter.load(target); health=adapter.health(target)
                if not health.healthy:
                    adapter.unload(target); raise RuntimeError("health gate failed")
                self._active=target
                return ModelSwitchResult(profile_id,"READY",profile_id,load_seconds=time.monotonic()-started)
            except Exception as exc: return self._rollback(target,previous,type(exc).__name__)
    def _rollback(self,target,previous,reason):
        rolled_back=None
        if previous:
            try:
                adapter=self.adapters[previous.provider_id]
                if self.preflight.check(previous.expected_memory_gib or 0).allowed:
                    adapter.load(previous)
                    if adapter.health(previous).healthy: self._active=previous; rolled_back=previous.profile_id
            except Exception: self._active=None
        return ModelSwitchResult(target.profile_id,"FAILED",self.active_profile_id,rolled_back,reason)


def model_center_text(registry=None):
    registry=registry or ModelRegistry(); q38=registry.models["local-qwen38"]
    return ("模型中心\n\n当前聊天 / FAST / FALLBACK：Qwen3.6-35B-A3B-4bit（已验证；运行状态以健康检查为准）\n"
            f"MAIN / VISION / VIDEO_UNDERSTANDING：{q38.display_name}（已注册，尚未完成本机 qualification）\n"
            "RAW：Owner only，尚未配置\nCODING：未通过；继续由外部 Codex Producer 承担\n"
            "STT / TTS / IMAGE / VIDEO / EMBED / RERANK：已注册候选，未完成本机 qualification\n\n"
            "注册不等于可用；模型切换必须通过互斥、内存预检、健康检查与失败回滚。")

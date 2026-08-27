"""Unified registry and mutually-exclusive heavy-model lifecycle.

Registration is metadata, never proof that a model is production-qualified.
Runtime adapters are injected so model dependencies stay out of the control-plane
and existing oMLX virtual environments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import json
import os
from pathlib import Path
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
    roles: frozenset[ModelRole]; local_or_remote: str; data_egress: str
    benchmark_version: str | None = None; benchmark_score: float | None = None
    last_evaluated: str | None = None; precision: str | None = None
    heavy: bool = True; owner_only: bool = False; runtime_env: str | None = None
    local_path: str | None = None; expected_memory_gib: float | None = None
    max_qualified_context_tokens: int | None = None


REGISTRY_PATH = Path(os.environ.get(
    "LOCAL_AI_MODEL_REGISTRY",
    str(Path(__file__).resolve().parents[4] / "config/model-registry-v0.1.json"),
))
ELIGIBLE_STATUSES = frozenset({"QUALIFIED", "VALIDATED"})
ALL_STATUSES = ELIGIBLE_STATUSES | {"REGISTERED_NOT_QUALIFIED", "REGISTERED_NOT_DOWNLOADED"}
IMMUTABLE_RUNTIME_ISOLATION = {
    "qwen38": "/Users/jerson/AI/runtime/qwen38-venv",
    "owner_raw": "/Users/jerson/AI/runtime/owner-raw",
    "audio": "/Users/jerson/AI/runtime/audio-venv",
    "image": "/Users/jerson/AI/runtime/image-venv",
    "video": "/Users/jerson/AI/runtime/video-venv",
    "rag": "/Users/jerson/AI/runtime/rag-venv",
}
IMMUTABLE_POLICY = {
    "one_heavy_model_resident": True,
    "registered_is_not_ready": True,
    "raw_owner_only": True,
    "public_privilege_expansion": False,
    "routine_codex_model_quota": False,
    "gemini_free_by_default": True,
    "gemini_private_egress": False,
    "silent_paid_upgrade": False,
}

IMMUTABLE_RAW_PROFILE = {
    "repo": "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF",
    "artifact": "Qwen3.8-27B-Uncensored-Q6_K.gguf",
    "revision": "dee0a3164d9e11bbbebf5b63f52ba99443d14fc3",
    "sha256": "a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6",
    "runtime": "llama.cpp-metal",
    "role": "OWNER_RAW_RESEARCH",
    "public": False,
    "auto_download": False,
    "shell": False,
    "credential_access": False,
    "document_command_execution": False,
}
IMMUTABLE_GEMINI_PROFILE = {
    "models": ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"],
    "provider": "google-gemini-developer-api",
    "tier": "FREE",
    "local": False,
    "billing_auto_upgrade": False,
    "role": "CLOUD_REVIEWER_MULTIMODAL",
    "thinking_level": "low",
    "fallback_on": ["TIMEOUT", "DEADLINE_EXCEEDED", "RATE_LIMIT", "MODEL_UNAVAILABLE"],
    "free_tier_data_use_warning": True,
    "search_grounding_required": False,
    "own_search_browser_layer": True,
}


@dataclass(frozen=True)
class RegistryAlias:
    role: ModelRole
    profile_id: str
    status: str
    max_context_tokens: int | None = None


QWEN36 = ModelProfile(
    "local-qwen36", "Qwen3.6-35B-A3B-4bit", "local-omlx",
    "mlx-community/Qwen3.6-35B-A3B-4bit",
    frozenset({ModelRole.FAST,ModelRole.FALLBACK,ModelRole.MAIN,ModelRole.REVIEW}),
    "LOCAL", "NONE", "v1", precision="4bit", runtime_env="/Users/jerson/AI/runtime/omlx-venv",
    local_path="/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit", expected_memory_gib=28,
)
QWEN38 = ModelProfile(
    "local-qwen38", "Qwen3.8-27B-8bit", "local-mlx-vlm", "mlx-community/Qwen3.8-27B-8bit",
    frozenset({ModelRole.MAIN,ModelRole.VISION,ModelRole.VIDEO_UNDERSTANDING,ModelRole.DEEP}),
    "LOCAL", "NONE", precision="8bit", runtime_env="/Users/jerson/AI/runtime/qwen38-venv",
    local_path="/Users/jerson/AI/models/qwen38-27b-8bit", expected_memory_gib=34,
    max_qualified_context_tokens=16384,
)


def _profile(pid, name, provider, model_id, role, *, precision=None, owner_only=False,
             runtime_env=None, expected_memory_gib=None):
    return ModelProfile(pid, name, provider, model_id, frozenset({role}), "LOCAL", "NONE",
                        precision=precision, owner_only=owner_only, runtime_env=runtime_env,
                        expected_memory_gib=expected_memory_gib)


DEFAULT_MODELS = (
    QWEN36, QWEN38,
    ModelProfile(
        "owner-qwen38-raw-q6k", "Qwen3.8-27B Uncensored Q6_K", "local-llama-cpp-raw",
        "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/Qwen3.8-27B-Uncensored-Q6_K.gguf",
        frozenset({ModelRole.RAW}), "LOCAL", "NONE", precision="Q6_K", owner_only=True,
        runtime_env="/Users/jerson/AI/runtime/owner-raw",
        local_path="/Users/jerson/AI/models/qwen38-owner-raw-q6k/Qwen3.8-27B-Uncensored-Q6_K.gguf",
        expected_memory_gib=30,
    ),
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
    """Runtime registry whose eligibility comes only from versioned config.

    Provider IDs, model IDs, paths, data-egress and owner-only policy remain
    immutable code metadata. The JSON file may select a known profile and its
    qualification status, but cannot introduce or weaken a profile.
    """
    def __init__(self, models=DEFAULT_MODELS, *, config_path=REGISTRY_PATH, aliases=None):
        self.models={m.profile_id:m for m in models}
        self.aliases = self._load_aliases(Path(config_path)) if aliases is None else self._validate_aliases(aliases)

    def _load_aliases(self, path):
        try:
            payload=json.loads(path.read_text())
        except (OSError,json.JSONDecodeError) as exc:
            raise ValueError("invalid model registry JSON") from exc
        if set(payload)!={"schema_version","production_aliases","profiles","runtime_isolation","policy"} or payload["schema_version"]!="0.1":
            raise ValueError("invalid model registry schema")
        if payload["runtime_isolation"] != IMMUTABLE_RUNTIME_ISOLATION or payload["policy"] != IMMUTABLE_POLICY:
            raise ValueError("immutable model safety policy mismatch")
        profiles=payload["profiles"]
        if (not isinstance(profiles,dict) or set(profiles)!={"owner-qwen38-raw-q6k","gemini-free-review-chain"} or
                profiles.get("owner-qwen38-raw-q6k")!=IMMUTABLE_RAW_PROFILE or
                profiles.get("gemini-free-review-chain")!=IMMUTABLE_GEMINI_PROFILE):
            raise ValueError("immutable provider profile mismatch")
        aliases=payload["production_aliases"]
        required={"MAIN","FAST","FALLBACK","VISION","VIDEO_UNDERSTANDING","STT_MAIN","TTS_MAIN","TTS_DESIGN","IMAGE_MAIN","VIDEO_MAIN","VIDEO_HIGH","EMBED","RERANK","RAW"}
        provider_aliases={"CLOUD_REVIEW","PREMIUM_PLAN_ACCEPT"}
        if not isinstance(aliases,dict) or set(aliases)!=required|provider_aliases:
            raise ValueError("invalid production aliases")
        raw=aliases["RAW"]
        if (raw.get("profile")!="owner-qwen38-raw-q6k" or raw.get("status") not in ALL_STATUSES or
                raw.get("owner_only") is not True or raw.get("host_permission_profile")!="OWNER_RAW_RESEARCH" or
                set(raw)!={"profile","status","owner_only","host_permission_profile"}):
            raise ValueError("RAW alias safety metadata mismatch")
        if aliases["CLOUD_REVIEW"].get("profile")!="gemini-free-review-chain":
            raise ValueError("cloud review alias mismatch")
        if aliases["PREMIUM_PLAN_ACCEPT"].get("profile")!="openai-codex-premium":
            raise ValueError("premium alias mismatch")
        return self._validate_aliases({name:value for name,value in aliases.items() if name in required})

    def _validate_aliases(self, raw_aliases):
        result={}
        for role_name, raw in raw_aliases.items():
            try: role=ModelRole(role_name)
            except ValueError as exc: raise ValueError("unknown model role") from exc
            allowed={"profile","status","max_context_tokens"}
            if role is ModelRole.RAW:
                allowed|={"owner_only","host_permission_profile"}
            if not isinstance(raw,dict) or not {"profile","status"} <= set(raw) or set(raw)-allowed:
                raise ValueError("malformed model alias")
            profile_id=raw["profile"]; status=raw["status"]
            if not isinstance(profile_id,str) or profile_id not in self.models or status not in ALL_STATUSES:
                raise ValueError("unknown profile or status")
            profile=self.models[profile_id]
            if role is ModelRole.RAW and (raw.get("owner_only") is not True or raw.get("host_permission_profile")!="OWNER_RAW_RESEARCH"):
                raise ValueError("RAW alias safety metadata mismatch")
            if role not in profile.roles:
                raise ValueError("profile does not support role")
            maximum=raw.get("max_context_tokens")
            if maximum is not None and (not isinstance(maximum,int) or isinstance(maximum,bool) or maximum < 1024 or maximum > 262144):
                raise ValueError("invalid max context")
            if status not in ELIGIBLE_STATUSES and maximum is not None:
                raise ValueError("unqualified profile cannot publish context")
            if role is ModelRole.MAIN and status in ELIGIBLE_STATUSES and maximum is None:
                raise ValueError("qualified MAIN requires max context")
            if maximum is not None and profile.max_qualified_context_tokens is not None and maximum>profile.max_qualified_context_tokens:
                raise ValueError("context exceeds immutable qualified envelope")
            result[role]=RegistryAlias(role,profile_id,status,maximum)
        return result

    def eligible(self, role):
        role=ModelRole(role); alias=self.aliases.get(role)
        return [self.models[alias.profile_id]] if alias and alias.status in ELIGIBLE_STATUSES else []
    def registered(self, role): return [m for m in self.models.values() if role in m.roles]
    def status(self, role):
        choices=self.eligible(role); return choices[0].profile_id if choices else "NOT_AVAILABLE"
    def alias(self, role): return self.aliases.get(ModelRole(role))
    def is_profile_eligible(self, profile_id):
        return any(alias.profile_id==profile_id and alias.status in ELIGIBLE_STATUSES for alias in self.aliases.values())
    def require(self, profile_id, *, owner):
        if profile_id not in self.models: raise LookupError("model is not registered")
        result=self.models[profile_id]
        if result.owner_only and not owner: raise PermissionError("model is owner-only")
        return result


class ModelRegistry(ModelRoleRegistry): pass


class ModelRouter:
    _TASK_ROLE={"CHAT":ModelRole.MAIN,"FAST":ModelRole.FAST,"CHAT_FAST":ModelRole.FAST,"MAIN":ModelRole.MAIN,"CODE":ModelRole.CODE,"REVIEW":ModelRole.REVIEW,
                "VISION":ModelRole.VISION,"VIDEO_UNDERSTANDING":ModelRole.VIDEO_UNDERSTANDING,
                "AUDIO":ModelRole.STT_MAIN,"STT":ModelRole.STT_MAIN,"TTS":ModelRole.TTS_MAIN,
                "EMBEDDING":ModelRole.EMBED,"RERANK":ModelRole.RERANK,"IMAGE":ModelRole.IMAGE_MAIN,
                "VIDEO":ModelRole.VIDEO_MAIN,"DEEP_REASONING":ModelRole.DEEP}
    def __init__(self, registry=None, *, health_check=None, resource_check=None):
        self.registry=registry or ModelRegistry(); self.health_check=health_check or (lambda _:True); self.resource_check=resource_check or (lambda _:True)
    def _usable(self, profile): return bool(self.health_check(profile) and self.resource_check(profile))
    def route(self, task_type, user_override=None):
        role=self._TASK_ROLE[task_type]
        if user_override:
            candidate=self.registry.models.get(user_override)
            if candidate and candidate in self.registry.eligible(role) and self._usable(candidate): return candidate
            raise PermissionError("requested model is not eligible for this role")
        roles=(role,ModelRole.FALLBACK,ModelRole.FAST) if task_type in {"CHAT","MAIN"} else (role,)
        seen=set()
        for candidate_role in roles:
            for candidate in self.registry.eligible(candidate_role):
                if candidate.profile_id not in seen and self._usable(candidate): return candidate
                seen.add(candidate.profile_id)
        raise LookupError(f"no healthy validated model for {role}")


@dataclass(frozen=True)
class MemorySnapshot:
    total_gib: float; available_gib: float; swap_used_gib: float; pressure: str
    reclaimable_gib: float|None=None; compressed_gib: float=0; swap_delta_gib: float=0
    owned_heavy_profile_id: str|None=None
    timestamp: str=field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class MemoryPreflightResult:
    allowed: bool; required_gib: float; available_gib: float; reason: str; snapshot: MemorySnapshot


class MemoryPreflight:
    def __init__(self, probe: Callable[[],MemorySnapshot]|None=None, reserve_gib=4,max_swap_delta_gib=2,max_swap_used_gib=6):
        # Qwen3.8 qualification completed safely at 3.999 GiB swap. The 6 GiB
        # ceiling preserves roughly 2 GiB operational headroom on this 48GB Mac.
        self.probe=probe or self._macos_snapshot; self.reserve_gib=reserve_gib
        self.max_swap_delta_gib=max_swap_delta_gib; self.max_swap_used_gib=max_swap_used_gib
        self._last_swap_used_gib=None
    @staticmethod
    def _macos_snapshot():
        total=int(subprocess.check_output(["sysctl","-n","hw.memsize"],text=True).strip())
        page=int(subprocess.check_output(["sysctl","-n","hw.pagesize"],text=True).strip())
        vm=subprocess.check_output(["vm_stat"],text=True); pages={}
        for line in vm.splitlines()[1:]:
            if ":" in line:
                k,v=line.split(":",1); pages[k]=int(v.strip().rstrip("."))
        available=(pages.get("Pages free",0)+pages.get("Pages inactive",0)+pages.get("Pages speculative",0))*page
        reclaimable=available+pages.get("Pages purgeable",0)*page
        compressed=pages.get("Pages occupied by compressor",0)*page
        swap_text=subprocess.check_output(["sysctl","-n","vm.swapusage"],text=True)
        match=re.search(r"used = ([0-9.]+)([MG])",swap_text)
        swap=0 if not match else float(match.group(1))/(1024 if match.group(2)=="M" else 1)
        pressure_text=subprocess.check_output(["memory_pressure","-Q"],text=True)
        pressure_match=re.search(r"free percentage: (\d+)%",pressure_text); free_percent=int(pressure_match.group(1)) if pressure_match else 0
        pressure="NORMAL" if free_percent>=20 else ("WARNING" if free_percent>=8 else "CRITICAL")
        return MemorySnapshot(total/1024**3,available/1024**3,swap,pressure,reclaimable/1024**3,compressed/1024**3,0,None)
    def check(self, required_gib, *, owned_reclaimable_gib=0):
        snapshot=self.probe(); reclaimable=snapshot.reclaimable_gib if snapshot.reclaimable_gib is not None else snapshot.available_gib
        sampled_delta=0 if self._last_swap_used_gib is None else max(0,snapshot.swap_used_gib-self._last_swap_used_gib)
        self._last_swap_used_gib=snapshot.swap_used_gib
        swap_delta=max(snapshot.swap_delta_gib,sampled_delta)
        if snapshot.pressure=="CRITICAL": allowed=False; reason="MEMORY_PRESSURE_CRITICAL"
        elif snapshot.swap_used_gib>self.max_swap_used_gib: allowed=False; reason="SWAP_ABSOLUTE_LIMIT"
        elif swap_delta>self.max_swap_delta_gib: allowed=False; reason="SWAP_RUNAWAY"
        else:
            # Qualification proved that 34 GiB peak is safe on a 48 GiB Mac
            # with normal pressure and ~34 GiB reclaimable. Require total-system
            # headroom plus 85% reclaimability instead of fixed free+reserve.
            # A platform-owned resident model is reclaimable by the lifecycle
            # manager before the target starts. Unknown user processes are never
            # counted here and are never terminated by this policy.
            effective_reclaimable=min(snapshot.total_gib,reclaimable+max(0,owned_reclaimable_gib))
            allowed=snapshot.total_gib>=required_gib+self.reserve_gib and effective_reclaimable>=required_gib*.85
            reason="OK" if allowed else "INSUFFICIENT_RECLAIMABLE_MEMORY"
        return MemoryPreflightResult(allowed,required_gib,snapshot.available_gib,reason,snapshot)

    def admit_owned_transition(self, required_gib):
        """Allow an exact-owned runtime to be stopped before the final gate.

        This is deliberately not start authorization.  A resident managed
        model can keep macOS swap above the absolute start ceiling even though
        stopping that exact model will reclaim the resources needed by the
        replacement.  Admission therefore checks only the system condition
        that makes an orderly transition itself unsafe.  The normal ``check``
        method remains mandatory after the old runtime is proven absent and
        retains every start policy, including the 6 GiB absolute swap limit.
        """
        snapshot=self.probe()
        self._last_swap_used_gib=snapshot.swap_used_gib
        allowed=snapshot.pressure!="CRITICAL"
        reason="OK" if allowed else "MEMORY_PRESSURE_CRITICAL"
        return MemoryPreflightResult(
            allowed,required_gib,snapshot.available_gib,reason,snapshot,
        )


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
            if not self.registry.is_profile_eligible(profile_id):
                return ModelSwitchResult(profile_id,"NOT_ELIGIBLE",self.active_profile_id,failure_category="NOT_QUALIFIED")
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


def model_center_text(registry=None,runtime_health=None):
    registry=registry or ModelRegistry()
    main=registry.alias(ModelRole.MAIN); vision=registry.alias(ModelRole.VISION)
    main_profile=registry.models[main.profile_id]; vision_profile=registry.models[vision.profile_id]
    context_text=f"，最大实测上下文 {main.max_context_tokens} tokens" if main.max_context_tokens else ""
    main_state=(runtime_health or {}).get("MAIN","NOT_CHECKED")
    fast_state=(runtime_health or {}).get("FAST","NOT_CHECKED")
    vision_state=(runtime_health or {}).get("VISION","NOT_CHECKED")
    main_text=(f"{main_profile.display_name}（已资格验证{context_text}；运行：{main_state}）" if main.status in ELIGIBLE_STATUSES else "当前不可用，聊天自动回退 FAST")
    vision_text=(f"{vision_profile.display_name}（已资格验证；运行：{vision_state}）" if vision.status in ELIGIBLE_STATUSES else f"{vision_profile.display_name}（未资格验证）")
    return (f"模型中心\n\n默认聊天 / MAIN：{main_text}\n"
            f"FAST / FALLBACK：Qwen3.6-35B-A3B-4bit（已验证；运行：{fast_state}）\n"
            f"VISION：{vision_text}\nVIDEO_UNDERSTANDING：未资格验证\n"
            "RAW：Owner only，尚未配置\nCODING：未通过；继续由外部 Codex Producer 承担\n"
            "STT / TTS / IMAGE / VIDEO / EMBED / RERANK：已注册候选，未完成本机 qualification\n\n"
            "注册不等于可用；模型切换必须通过互斥、内存预检、健康检查与失败回滚。")

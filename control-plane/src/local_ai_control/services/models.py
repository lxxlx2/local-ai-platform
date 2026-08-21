"""Model roles and routing are metadata-only until an eligible model is validated."""
from dataclasses import dataclass
from enum import StrEnum


class ModelRole(StrEnum):
    FAST = "FAST"; DEEP = "DEEP"; CODE = "CODE"; REVIEW = "REVIEW"; VISION = "VISION"
    AUDIO = "AUDIO"; EMBED = "EMBED"; IMAGE = "IMAGE"; VIDEO = "VIDEO"


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str; display_name: str; provider_id: str; model_id: str
    roles: dict[ModelRole, str]; local_or_remote: str; data_egress: str
    benchmark_version: str | None = None; benchmark_score: float | None = None
    last_evaluated: str | None = None


QWEN36 = ModelProfile("local-qwen36", "Qwen3.6-35B-A3B-4bit", "local-omlx", "Qwen3.6-35B-A3B-4bit", {ModelRole.FAST: "CURRENT", ModelRole.DEEP: "NOT_VALIDATED", ModelRole.CODE: "NO", ModelRole.REVIEW: "LIMITED"}, "LOCAL", "NONE", "v1", None, None)


class ModelRoleRegistry:
    def __init__(self, models=(QWEN36,)):
        self.models = {model.profile_id: model for model in models}

    def eligible(self, role: ModelRole):
        return [model for model in self.models.values() if model.roles.get(role) in {"CURRENT", "VALIDATED"}]

    def status(self, role: ModelRole) -> str:
        choices = self.eligible(role)
        return choices[0].profile_id if choices else "NOT_AVAILABLE"


class ModelRouter:
    _TASK_ROLE = {"CHAT": ModelRole.FAST, "CODE": ModelRole.CODE, "REVIEW": ModelRole.REVIEW, "VISION": ModelRole.VISION, "AUDIO": ModelRole.AUDIO, "EMBEDDING": ModelRole.EMBED, "IMAGE": ModelRole.IMAGE, "VIDEO": ModelRole.VIDEO, "DEEP_REASONING": ModelRole.DEEP}

    def __init__(self, registry=None): self.registry = registry or ModelRoleRegistry()

    def route(self, task_type: str, user_override: str | None = None) -> ModelProfile:
        role = self._TASK_ROLE[task_type]
        if user_override:
            candidate = self.registry.models.get(user_override)
            if candidate and candidate.roles.get(role) in {"CURRENT", "VALIDATED"}:
                return candidate
            raise PermissionError("requested model is not eligible for this role")
        choices = self.registry.eligible(role)
        if not choices: raise LookupError(f"no validated model for {role}")
        return choices[0]


def model_center_text() -> str:
    return "模型中心\n\n当前聊天 / FAST：Qwen3.6-35B-A3B-4bit（本机 oMLX，已加载）\nDEEP：未验证\nCODING：未通过，不会用于 Coding Agent\nREVIEW：能力有限，等待独立评测\nVISION / AUDIO / EMBED / IMAGE / VIDEO：未安装\n\n模型变更必须先预览、兼容性与健康检查，并经 Owner 确认；失败会保留原配置。"

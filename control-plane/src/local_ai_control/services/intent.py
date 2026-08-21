from dataclasses import dataclass
import re

from local_ai_control.domain.identity import Role


@dataclass(frozen=True)
class Intent:
    kind: str
    project: str | None = None
    action: str | None = None
    scope: dict | None = None


def classify_owner_text(role: Role, text: str) -> Intent:
    """A deliberately narrow deterministic control gate; ordinary text remains chat."""
    normalized = text.replace(" ", "")
    identity_query = re.sub(r"[，。！？?!.、：:]", "", normalized)
    identity_patterns = (
        r"(?:请问)?你?(?:现在|当前)?(?:用的|使用的|跑的|运行的)?是?(?:什么|哪个)模型(?:名称)?",
        r"(?:请问)?你?(?:现在|当前)模型(?:名称)?是什么",
        r"(?:请问)?你?(?:现在|当前)(?:用的|使用的|运行的)?后端是什么",
        r"(?:请问)?你?(?:现在|当前)(?:用的|使用的|运行的)?是?(?:什么|哪个)后端",
    )
    if any(re.fullmatch(pattern, identity_query) for pattern in identity_patterns):
        return Intent("MODEL_IDENTITY_INTENT")
    capability_patterns = (
        r"(?:你好)?(?:请)?(?:简单|详细)?(?:介绍一下)?你(?:现在)?能帮我做什么",
        r"(?:你好)?(?:请)?(?:简单|详细)?介绍一下你(?:现在)?(?:能做什么|的功能|的能力)?",
        r"(?:请问)?你(?:现在)?支持什么(?:功能|能力)?",
        r"(?:请问)?你有什么(?:功能|能力)",
        r"(?:请问)?当前有什么(?:功能|能力)",
    )
    if any(re.fullmatch(pattern, identity_query) for pattern in capability_patterns):
        return Intent("CAPABILITY_INTENT")
    if role is Role.OWNER and "归灯记" in text and "人物" in text and "最近5章" in text and any(word in text for word in ("检查", "冲突")):
        return Intent("CONTROL_INTENT", "guidengji", "character_consistency_check", {"last_chapters": 5})
    return Intent("CHAT_INTENT")


def preview_text(intent: Intent) -> str:
    if intent.kind != "CONTROL_INTENT":
        raise ValueError("not a control intent")
    return "📝 任务预览\n\n项目：归灯记\n任务：检查最近 5 章人物设定冲突\n范围：仅预览；不会读取或修改业务项目。\n\n确认功能将在后续受控任务阶段开放。"

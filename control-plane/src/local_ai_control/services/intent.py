from dataclasses import dataclass

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
    capability_phrases = ("你能做什么", "介绍一下你", "你现在支持什么", "你有什么功能", "当前有什么能力", "现在是什么模型", "你用的什么模型", "用的是什么模型")
    if any(phrase in normalized for phrase in capability_phrases):
        return Intent("CAPABILITY_INTENT")
    if role is Role.OWNER and "归灯记" in text and "人物" in text and "最近5章" in text and any(word in text for word in ("检查", "冲突")):
        return Intent("CONTROL_INTENT", "guidengji", "character_consistency_check", {"last_chapters": 5})
    return Intent("CHAT_INTENT")


def preview_text(intent: Intent) -> str:
    if intent.kind != "CONTROL_INTENT":
        raise ValueError("not a control intent")
    return "📝 任务预览\n\n项目：归灯记\n任务：检查最近 5 章人物设定冲突\n范围：仅预览；不会读取或修改业务项目。\n\n确认功能将在后续受控任务阶段开放。"

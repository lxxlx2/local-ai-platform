from local_ai_control.domain.identity import Role
from local_ai_control.services.models import ELIGIBLE_STATUSES,ModelRegistry,ModelRole
from local_ai_control.services.multimodal import MediaIntent,RouteDecision

MODEL_NAME="Qwen3.8-27B-8bit"
BACKEND_NAME="本机 MLX sidecar"


def _role_line(registry,role,runtime_health):
    alias=registry.alias(role); profile=registry.models[alias.profile_id]
    if alias.status not in ELIGIBLE_STATUSES: return profile.display_name,"已注册，尚未完成本机 qualification；当前生产 Bot 不会执行"
    health=(runtime_health or {}).get(role.value,"NOT_CHECKED")
    state={"HEALTHY":"正在运行且健康","NOT_RUNNING":"已资格验证，当前未运行","CONFLICT":"运行冲突，已拒绝路由","NOT_CHECKED":"已资格验证，运行状态未检查"}.get(health,"运行状态未知")
    return profile.display_name,state


def model_identity(healthy=None,*,registry=None,runtime_health=None)->str:
    registry=registry or ModelRegistry(); name,state=_role_line(registry,ModelRole.MAIN,runtime_health)
    if healthy is not None: state="正在运行且健康" if healthy else "已资格验证，当前未运行"
    return f"当前对话模型：{name}\n运行方式：{BACKEND_NAME}\n状态：{state}"


def capability_intro(role:Role,healthy=None,*,registry=None,runtime_health=None)->str:
    registry=registry or ModelRegistry(); model=model_identity(healthy,registry=registry,runtime_health=runtime_health)
    _,vision_state=_role_line(registry,ModelRole.VISION,runtime_health)
    if role is Role.OWNER:
        return (f"{model}\n\n你可以直接和本地 AI 对话，并继续最近上下文。\n"
                "也可以管理私人项目、任务和审批；自然语言请求会先生成受控任务预览。\n"
                "可查看模型、系统与任务状态，并使用会话历史和记忆入口。\n"
                f"Owner 图片理解：{vision_state}。\n\n"
                "视频理解、Whisper、TTS、图片/视频生成、Embedding 与 Reranker 仍需各自 qualification 或 provider。\n"
                "当前未配置：远程 PostgreSQL 与对象存储。")
    return (f"{model}\n\n你可以直接提问并继续自己的最近对话，也可查看自己的任务、记忆与额度入口。\n"
            "Public 图片、视频与私人系统能力当前未开放。\n\n"
            "请不要发送助记词、私钥、密码或 API Token。")


def routed_capability_text(decision:RouteDecision,*,registry=None,runtime_health=None)->str:
    labels={MediaIntent.VISION:"视觉理解",MediaIntent.VIDEO_UNDERSTANDING:"视频理解",MediaIntent.STT:"语音转写",MediaIntent.TTS:"语音生成",MediaIntent.IMAGE_GENERATION:"图片生成",MediaIntent.IMAGE_EDIT:"图片编辑",MediaIntent.VIDEO_GENERATION:"视频生成",MediaIntent.WEB:"联网研究"}
    label=labels.get(decision.intent,"该能力")
    if decision.model_role is None: return f"{label}\n\n当前 provider 状态以系统运行检查为准。"
    name,state=_role_line(registry or ModelRegistry(),decision.model_role,runtime_health)
    return f"{label}\n\n模型：{name}\n状态：{state}。"

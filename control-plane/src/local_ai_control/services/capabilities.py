from local_ai_control.domain.identity import Role


MODEL_NAME = "Qwen3.6-35B-A3B-4bit"
BACKEND_NAME = "本机 oMLX"


def model_identity(healthy=True) -> str:
    return f"当前对话模型：{MODEL_NAME}\n运行方式：{BACKEND_NAME}\n状态：{'已加载且健康' if healthy else '暂时不可用'}"


def capability_intro(role: Role, healthy=True) -> str:
    status = "已加载且健康" if healthy else "暂时不可用"
    model = f"当前对话模型：{MODEL_NAME}\n运行：{BACKEND_NAME}（{status}）"
    if role is Role.OWNER:
        return (
            f"{model}\n\n"
            "你可以直接和本地 AI 对话，并继续最近上下文。\n"
            "也可以管理私人项目、任务和审批；自然语言请求会先生成受控任务预览。\n"
            "可查看模型、系统与任务状态，并使用会话历史和记忆入口。\n"
            "文件处理入口已开放为受控流程。\n\n"
            "当前未启用：图片理解、Whisper/视频 AI、Embedding 语义检索、远程 PostgreSQL 与对象存储。"
        )
    return (
        f"{model}\n\n"
        "你可以直接提问并继续自己的最近对话，也可查看自己的任务、记忆与额度入口。\n"
        "文件与媒体能力目前仅提供受控入口。\n\n"
        "当前未启用：图片理解、视频 AI 与语义向量检索。请不要发送助记词、私钥、密码或 API Token。"
    )

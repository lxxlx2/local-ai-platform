from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows])


def owner_dashboard(pending_count=0):
    pending = f"待审批 ({pending_count})" if pending_count else "待审批"
    return inline([
        [("AI 对话", "chat:new"), (pending, "owner:approvals")],
        [("私人项目", "private:projects"), ("私人任务", "owner:tasks")],
        [("文件与媒体", "menu:media"), ("我的记忆", "owner:memory")],
        [("系统管理", "menu:system"), ("设置", "owner:settings")],
    ])


def public_dashboard():
    return inline([
        [("问 AI", "chat:new"), ("我的任务", "public:tasks")],
        [("文件与媒体", "menu:public_media"), ("我的记忆", "public:memory")],
        [("我的额度", "public:usage"), ("使用帮助", "public:help")],
    ])


def media_menu(owner=False):
    prefix = "owner" if owner else "public"
    return inline([[("文件分析", f"{prefix}:file"), ("图片处理", f"{prefix}:image")], [("视频处理", f"{prefix}:video")], [("返回", "home")]])


def system_menu():
    return inline([[("模型", "owner:model"), ("系统状态", "owner:system")], [("功能管理", "owner:features"), ("报告", "owner:reports")], [("公共视角预览", "public:preview")], [("返回", "home")]])


BACK = inline([[("返回", "home")]])

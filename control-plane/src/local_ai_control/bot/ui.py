from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def inline(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows])


@dataclass(frozen=True)
class NavigationRoute:
    route_id: str
    parent_route: str | None
    home_route: str
    render_callback: str


NAVIGATION_ROUTES = {
    "home": NavigationRoute("home", None, "home", "home"),
    "menu:media": NavigationRoute("menu:media", "home", "home", "owner_media_menu"),
    "menu:public_media": NavigationRoute("menu:public_media", "home", "home", "public_media_menu"),
    "menu:system": NavigationRoute("menu:system", "home", "home", "owner_system_menu"),
    "menu:learning": NavigationRoute("menu:learning", "owner:settings", "home", "learning_menu"),
    "owner:file": NavigationRoute("owner:file", "menu:media", "home", "owner_capability"),
    "owner:image": NavigationRoute("owner:image", "menu:media", "home", "owner_capability"),
    "owner:video": NavigationRoute("owner:video", "menu:media", "home", "owner_capability"),
    "public:file": NavigationRoute("public:file", "menu:public_media", "home", "public_route"),
    "public:image": NavigationRoute("public:image", "menu:public_media", "home", "public_route"),
    "public:video": NavigationRoute("public:video", "menu:public_media", "home", "public_route"),
    "owner:model": NavigationRoute("owner:model", "menu:system", "home", "models"),
    "owner:system": NavigationRoute("owner:system", "menu:system", "home", "system"),
    "owner:features": NavigationRoute("owner:features", "menu:system", "home", "private_route"),
    "owner:reports": NavigationRoute("owner:reports", "menu:system", "home", "private_route"),
    "public:preview": NavigationRoute("public:preview", "menu:system", "home", "public_preview"),
}


def parent_route(route_id: str) -> str:
    route = NAVIGATION_ROUTES.get(route_id)
    return route.parent_route if route and route.parent_route else (route.home_route if route else "home")


def back_for(route_id: str) -> InlineKeyboardMarkup:
    return inline([[("返回", parent_route(route_id))]])


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


def settings_menu():
    return inline([[("学习与训练", "menu:learning"), ("隐私说明", "owner:learning_privacy")], [("返回", "home")]])


def learning_menu():
    return inline([[("训练候选", "learning:candidates"), ("我的反馈", "learning:feedback")],
                   [("数据集", "learning:datasets"), ("评估", "learning:evals")],
                   [("Adapter", "learning:adapters"), ("隐私设置", "learning:privacy")],
                   [("返回", "owner:settings")]])


def learning_feedback(message_id: str):
    return inline([[("加入训练候选", f"learning:good:{message_id}"),
                    ("不满意", f"learning:bad:{message_id}")],
                   [("跳过", f"learning:skip:{message_id}")]])


BACK = back_for("home")

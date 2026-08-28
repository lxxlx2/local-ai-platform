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
    "menu:owner_tasks": NavigationRoute("menu:owner_tasks", "home", "home", "owner_task_menu"),
    "menu:workflows": NavigationRoute("menu:workflows", "menu:owner_tasks", "home", "workflow_menu"),
    "owner:file": NavigationRoute("owner:file", "menu:media", "home", "owner_capability"),
    "owner:image": NavigationRoute("owner:image", "menu:media", "home", "owner_capability"),
    "owner:video": NavigationRoute("owner:video", "menu:media", "home", "owner_capability"),
    "owner:audio": NavigationRoute("owner:audio", "menu:media", "home", "owner_capability"),
    "owner:image_generate": NavigationRoute("owner:image_generate", "menu:media", "home", "owner_capability"),
    "owner:video_generate": NavigationRoute("owner:video_generate", "menu:media", "home", "owner_capability"),
    "media:video": NavigationRoute("media:video", "menu:media", "home", "video_production_menu"),
    "owner:media_jobs": NavigationRoute("owner:media_jobs", "menu:media", "home", "owner_capability"),
    "public:file": NavigationRoute("public:file", "menu:public_media", "home", "public_route"),
    "public:image": NavigationRoute("public:image", "menu:public_media", "home", "public_route"),
    "public:video": NavigationRoute("public:video", "menu:public_media", "home", "public_route"),
    "owner:model": NavigationRoute("owner:model", "menu:system", "home", "models"),
    "owner:system": NavigationRoute("owner:system", "menu:system", "home", "system"),
    "owner:features": NavigationRoute("owner:features", "menu:system", "home", "private_route"),
    "owner:reports": NavigationRoute("owner:reports", "menu:system", "home", "private_route"),
    "owner:web": NavigationRoute("owner:web", "menu:system", "home", "owner_capability"),
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
    if owner:
        return inline([
            [("文件分析", "owner:file"), ("视觉理解", "owner:image")],
            [("语音", "owner:audio"), ("图片生成", "owner:image_generate")],
            [("视频", "media:video")],
            [("任务与进度", "owner:media_jobs")], [("返回", "home")],
        ])
    return inline([[("文件分析", f"{prefix}:file"), ("图片处理", f"{prefix}:image")], [("视频处理", f"{prefix}:video")], [("返回", "home")]])


def video_production_menu():
    return inline([[('新建视频','media:new')],[('视频理解','owner:video'),('生成能力状态','owner:video_generate')],[('返回','menu:media')]])


def source_mode_menu():
    return inline([[('上传材料','mw:source:UPLOADS'),('链接','mw:source:LINKS')],[('上传与链接','mw:source:UPLOADS_AND_LINKS'),('直接说明','mw:source:DIRECT_BRIEF')],[('取消','mw:cancel')]])


def materials_menu(mode):
    if mode == "LINKS":
        return inline([
            [('继续添加链接','mw:materials:more'),('链接齐了','mw:materials:done')],
            [('取消','mw:cancel')],
        ])
    if mode == "UPLOADS":
        return inline([
            [('继续上传','mw:materials:more'),('材料齐了','mw:materials:done')],
            [('取消','mw:cancel')],
        ])
    return inline([
        [('继续添加','mw:materials:more'),('材料齐了','mw:materials:done')],
        [('取消','mw:cancel')],
    ])


def execution_mode_menu(): return inline([[('自动完成','mw:exec:AUTO'),('先审文稿','mw:exec:REVIEW_SCRIPT')],[('取消','mw:cancel')]])
def language_menu(): return inline([[('自动识别','mw:lang:auto'),('中文','mw:lang:zh'),('English','mw:lang:en')],[('取消','mw:cancel')]])
def voice_menu(): return inline([[('自动选择','mw:voice:auto')],[('中文声音','mw:voice:zh-male-25-default'),('English Voice','mw:voice:en-male-25-default')],[('取消','mw:cancel')]])
def completion_mode_menu(): return inline([[('完成后审视频','mw:complete:AUTO_COMPLETE')],[('先审文稿再生成','mw:complete:SCRIPT_REVIEW_FIRST')],[('取消','mw:cancel')]])
def confirmation_menu(): return inline([[('确认生成','mw:confirm'),('取消','mw:cancel')]])
def review_video_menu(): return inline([[('通过并发布','mw:approve'),('重新生成','mw:regenerate')],[('修改文稿','mw:edit-script'),('取消','mw:cancel')]])
def script_review_menu(): return inline([[('文稿通过并生成','mw:script-approve'),('修改文稿','mw:edit-script')],[('取消','mw:cancel')]])


def system_menu():
    return inline([[("模型", "owner:model"), ("联网搜索", "owner:web")], [("系统状态", "owner:system"), ("功能管理", "owner:features")], [("报告", "owner:reports"), ("公共视角预览", "public:preview")], [("返回", "home")]])


def owner_task_menu():
    return inline([[("任务预览", "owner:task_previews"), ("自动工作流", "menu:workflows")], [("返回", "home")]])


def workflow_menu():
    return inline([[("刷新状态", "supervisor:status"), ("创建安全演示", "supervisor:demo")], [("返回", "menu:owner_tasks")]])


def workflow_controls(job_id: str, status: str):
    rows = []
    if status in {"QUEUED", "RUNNING"}:
        rows.append([("暂停", f"supervisor:pause:{job_id}"), ("取消", f"supervisor:cancel:{job_id}")])
    elif status == "WAITING":
        rows.append([("继续", f"supervisor:resume:{job_id}"), ("取消", f"supervisor:cancel:{job_id}")])
    elif status in {"FAILED", "BLOCKED"}:
        rows.append([("重试", f"supervisor:retry:{job_id}")])
    rows.extend([[("刷新", f"supervisor:view:{job_id}")], [("返回", "menu:workflows")]])
    return inline(rows)


BACK = back_for("home")

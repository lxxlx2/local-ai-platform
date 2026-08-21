import asyncio
import logging
import subprocess

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from local_ai_control.bot.ui import (BACK, back_for, inline, learning_feedback, learning_menu,
    media_menu, owner_dashboard, public_dashboard, settings_menu, system_menu)
from local_ai_control.services.capabilities import capability_intro, model_identity
from local_ai_control.config.settings import Settings
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.chat import ChatService
from local_ai_control.services.control import ControlPlane
from local_ai_control.services.intent import classify_owner_text, preview_text
from local_ai_control.services.models import model_center_text
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.rate_limit import PublicRateLimiter
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository
from local_ai_control.services.learning import (
    BoundedLocalContentStore, FeedbackService, FeedbackType, LearningRepository, LearningService,
)

OWNER_HOME = "本地 AI 控制中心\n\n请选择一个功能："
PUBLIC_HOME = "AI 助手\n\n你好！可以直接发送问题，或选择一个功能："


def owner_keyboard():
    return owner_dashboard()


def public_keyboard():
    return public_dashboard()


def home_for(identity):
    return (OWNER_HOME, owner_keyboard()) if identity.role is Role.OWNER else (PUBLIC_HOME, public_keyboard())


def safe_command(command, fallback="不可用"):
    try:
        return subprocess.check_output(command, text=True, timeout=3).strip() or fallback
    except (OSError, subprocess.SubprocessError):
        return fallback


def _health_ok():
    try:
        OmlxProvider().health()
        return True
    except Exception:
        return False


async def send_start_dashboard(target, title, keyboard):
    """Remove the legacy reply keyboard without leaving a ghost message."""
    cleanup = await target.answer("正在打开控制中心…", reply_markup=ReplyKeyboardRemove())
    dashboard = await target.answer(title, reply_markup=keyboard)
    try:
        await cleanup.delete()
    except Exception as error:
        logging.warning("start cleanup delete failed type=%s", type(error).__name__)
    return dashboard


async def send_rendered_output(message, renderer, text, reply_markup=None):
    rendered = renderer.package(text)
    for index, chunk in enumerate(rendered.chunks):
        kwargs = {"parse_mode": rendered.parse_mode}
        if reply_markup is not None and index == len(rendered.chunks) - 1:
            kwargs["reply_markup"] = reply_markup
        await message.answer(chunk, **kwargs)
    logging.info("telegram output chars=%s chunks=%s", len(rendered.canonical_text), len(rendered.chunks))
    return rendered


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.load()
    if not settings.token or not settings.owner_id:
        raise RuntimeError("ACTION_REQUIRED: TELEGRAM_BOT_CREDENTIALS")
    private_control = ControlPlane(settings.private_db_path)
    private_control.migrate()
    private_repo = ScopedSQLiteRepository(settings.private_memory_db_path, "private")
    public_repo = ScopedSQLiteRepository(settings.public_db_path, "public")
    private_repo.migrate(); public_repo.migrate()
    learning_repo = LearningRepository(); learning_repo.migrate()
    learning_service = LearningService(learning_repo, BoundedLocalContentStore())
    feedback_service = FeedbackService(learning_service)
    firewall = SecretFirewall()
    renderer = TelegramOutputRenderer()
    rate_limiter = PublicRateLimiter(settings.public_messages_per_minute, settings.public_messages_per_hour, settings.public_messages_per_day)
    bot = Bot(settings.token)
    dp = Dispatcher()

    def identity(update):
        return identity_from_telegram(update.from_user.id, settings.owner_id)

    def repo_for(ctx):
        return private_repo if ctx.role is Role.OWNER else public_repo

    async def chat_session(ctx):
        repo = repo_for(ctx)
        sessions = repo.list_sessions(ctx, 1)
        return sessions[0]["id"] if sessions else repo.create_session(ctx)

    async def respond_home(target, ctx):
        title, keyboard = home_for(ctx)
        await send_start_dashboard(target, title, keyboard)

    async def edit_page(query, text, keyboard=BACK):
        await query.message.edit_text(text, reply_markup=keyboard)
        await query.answer()

    async def send_chat_output(message, text, reply_markup=None):
        return await send_rendered_output(message, renderer, text, reply_markup)

    @dp.message(CommandStart())
    async def start(message: Message):
        await respond_home(message, identity(message))

    @dp.message(F.text)
    async def plain_chat(message: Message):
        ctx = identity(message)
        decision = firewall.inspect(message.text)
        if decision.action == "BLOCK":
            logging.warning("security blocked type=%s user=%s", decision.category, ctx.internal_user_id)
            await message.answer(SECRET_BLOCK_MESSAGE)
            return
        if not rate_limiter.allow(ctx):
            await message.answer("请求过于频繁，请稍后再试。")
            return
        intent = classify_owner_text(ctx.role, message.text)
        if intent.kind == "MODEL_IDENTITY_INTENT":
            await send_chat_output(message, model_identity(healthy=_health_ok()))
            return
        if intent.kind == "CAPABILITY_INTENT":
            await send_chat_output(message, capability_intro(ctx.role, healthy=_health_ok()))
            return
        if intent.kind == "CONTROL_INTENT":
            await message.answer(preview_text(intent), reply_markup=inline([[("⬅️ 返回首页", "home")]]))
            return
        session_id = await chat_session(ctx)
        try:
            result = ChatService(repo_for(ctx), OmlxProvider(), firewall).reply(ctx, session_id, message.text)
        except Exception as error:
            logging.warning("chat unavailable type=%s role=%s", type(error).__name__, ctx.role)
            result = None
        if result is None:
            await send_chat_output(message, "AI 服务暂时不可用，请稍后重试。")
            return
        feedback_markup = None
        if ctx.role is Role.OWNER:
            recent = private_repo.recent_messages(ctx, session_id, 2)
            assistant = next((row for row in reversed(recent) if row["role"] == "assistant"), None)
            if assistant:
                feedback_markup = learning_feedback(assistant["id"])
        await send_chat_output(message, result.text, feedback_markup)
        logging.info("chat completion status=%s output_tokens=%s requested_limit=%s", result.finish_reason, result.output_tokens, result.requested_max_output_tokens)

    @dp.callback_query(F.data == "chat:new")
    async def new_chat(query: CallbackQuery):
        ctx = identity(query)
        session_id = repo_for(ctx).create_session(ctx)
        await edit_page(query, "已创建新对话。现在可以直接发送中文问题。")
        logging.info("new chat role=%s session=%s", ctx.role, session_id)

    @dp.callback_query(F.data == "public:preview")
    async def public_preview(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True)
            return
        await edit_page(query, "公共视角预览\n\nPublic 用户可使用：AI 对话、受限文件入口、自己的任务与记忆。\n不包含私人项目、审批、系统状态或模型管理。", back_for(query.data))

    @dp.callback_query(F.data == "home")
    async def home(query: CallbackQuery):
        title, keyboard = home_for(identity(query))
        await edit_page(query, title, keyboard)

    @dp.callback_query(F.data == "menu:media")
    async def owner_media_menu(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "文件与媒体\n\n请选择一个入口：", media_menu(owner=True))

    @dp.callback_query(F.data == "menu:public_media")
    async def public_media_menu(query: CallbackQuery):
        await edit_page(query, "文件与媒体\n\n请选择一个入口：", media_menu(owner=False))

    @dp.callback_query(F.data == "menu:system")
    async def owner_system_menu(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "系统管理\n\n请选择一个功能：", system_menu())

    @dp.callback_query(F.data == "owner:settings")
    async def owner_settings(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:settings")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "设置\n\n学习与训练默认只使用 Owner 明确反馈；Public 训练默认关闭。", settings_menu())

    @dp.callback_query(F.data == "menu:learning")
    async def owner_learning(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:learning")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        metrics = learning_repo.metrics()
        await edit_page(query, "学习与训练\n\n"
                        f"候选：{metrics['candidate_count']}\n已批准：{metrics['approved_count']}\n"
                        f"数据集：{metrics['dataset_count']}\nAdapter：{metrics['adapter_count']}\n\n"
                        "Memory 与 Training 相互独立；Public 数据默认不训练。", learning_menu())

    @dp.callback_query(F.data.startswith("learning:"))
    async def learning_route(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:learning")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        parts = query.data.split(":", 2)
        if len(parts) == 3 and parts[1] in {"good", "bad", "skip"}:
            try:
                prompt, answer = private_repo.message_pair_for_feedback(ctx, parts[2])
                feedback = {"good": FeedbackType.GOOD, "bad": FeedbackType.BAD,
                            "skip": FeedbackType.SKIP}[parts[1]]
                feedback_service.record(feedback=feedback, prompt=prompt["content"], response=answer["content"],
                                        namespace="personal-general", source_ref=answer["id"])
            except (KeyError, ValueError):
                await query.answer("反馈对象不可用或已处理。", show_alert=True); return
            await query.message.edit_reply_markup(reply_markup=None)
            await query.answer("反馈已记录；不会自动训练或切换模型。", show_alert=True)
            return
        labels = {
            "learning:candidates": "训练候选只来自 Owner 明确反馈或安全人工导入。",
            "learning:feedback": "GOOD 可进入 SFT 候选；BAD 只作为 rejected；更好回答形成 PreferencePair。",
            "learning:datasets": "数据集按 namespace 独立版本化，并隔离 Golden Holdout。",
            "learning:evals": "Adapter 必须通过 Golden Eval 与安全回归才能升级。",
            "learning:adapters": "当前不会自动启用 Adapter；Base 模型始终保留。",
            "learning:privacy": "Secret 永不保存；个人标识默认脱敏；Public Training 为 OFF。",
        }
        await edit_page(query, labels.get(query.data, "学习功能暂不可用。"), back_for("menu:learning"))

    @dp.callback_query(F.data == "owner:learning_privacy")
    async def learning_privacy(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:learning")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "学习隐私\n\nPublic Training：OFF\nSecret：拒绝且不保存正文\n个人标识：默认脱敏\n删除只影响未来训练；已训练 Adapter 需要重训才能真正 forget。", back_for("owner:settings"))

    @dp.callback_query(F.data == "owner:system")
    async def system(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True)
            return
        try:
            OmlxProvider().health(); health = "正常"
        except Exception:
            health = "暂不可用"
        swap = safe_command(["sysctl", "-n", "vm.swapusage"])
        await edit_page(query, f"系统状态\n\n本地 AI：{health}\nQwen3.6：已加载\noMLX：{health}\nSwap：{swap}", back_for(query.data))

    @dp.callback_query(F.data == "owner:model")
    async def models(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, model_center_text(), back_for(query.data))

    @dp.callback_query(F.data == "owner:memory")
    async def owner_memory(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:memory")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "我的记忆\n\n可用：最近记忆、长期偏好、项目记忆、搜索、删除/设置。\n当前为本地开发存储；语义向量检索仍等待 Embedding Provider。")

    @dp.callback_query(F.data.in_({"owner:image", "owner:video", "owner:file"}))
    async def owner_capability(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, query.data)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        text = {"owner:image": "当前尚未安装图片理解模型。", "owner:video": "当前尚未安装 Whisper 或视频分析模型。", "owner:file": "当前仅规划 txt / md 安全文件分析；不会读取私人项目文件。"}[query.data]
        await edit_page(query, text, back_for(query.data))

    @dp.callback_query(F.data.startswith(("owner:", "private:", "guidengji:")))
    async def private_route(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, query.data)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "此私人功能已受权限层保护。当前 V0.2 仅提供安全预览，不会执行文件、Git、发布或模型变更。", back_for(query.data))

    @dp.callback_query(F.data.startswith("public:"))
    async def public_route(query: CallbackQuery):
        ctx = identity(query)
        labels = {"public:image": "当前尚未安装图片理解模型。", "public:video": "当前尚未安装 Whisper 或视频分析模型。", "public:file": "当前仅规划 txt / md 安全文件入口；不接受压缩包或可执行文件。", "public:tasks": "当前没有可显示的任务。", "public:memory": "长期记忆默认未启用。启用后只会保存你的记忆；当前可用功能仍在本地开发模式。", "public:usage": "公共额度处于保守本地开发配置。", "public:help": "请不要发送助记词、私钥、密码或 API Token。链接不会自动下载。"}
        await edit_page(query, labels.get(query.data, "该功能暂不可用。"), back_for(query.data))

    @dp.callback_query()
    async def invalid_callback(query: CallbackQuery):
        await query.answer("该操作不存在或已过期。", show_alert=True)

    try:
        await dp.start_polling(bot)
    finally:
        private_control.close(); private_repo.close(); public_repo.close(); learning_repo.close(); await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())

import asyncio
import logging
import subprocess

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from local_ai_control.bot.ui import BACK, inline, media_menu, owner_dashboard, public_dashboard, system_menu
from local_ai_control.services.capabilities import capability_intro
from local_ai_control.config.settings import Settings
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.chat import ChatService
from local_ai_control.services.control import ControlPlane
from local_ai_control.services.intent import classify_owner_text, preview_text
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.rate_limit import PublicRateLimiter
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository

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
        await target.answer(title, reply_markup=ReplyKeyboardRemove())
        await target.answer(title, reply_markup=keyboard)

    async def edit_page(query, text, keyboard=BACK):
        await query.message.edit_text(text, reply_markup=keyboard)
        await query.answer()

    async def send_chat_output(message, text):
        rendered = renderer.package(text)
        for chunk in rendered.chunks:
            await message.answer(chunk, parse_mode=None)
        logging.info("telegram output chars=%s chunks=%s", len(rendered.canonical_text), len(rendered.chunks))
        return rendered

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
        intent = classify_owner_text(ctx.role, message.text)
        if intent.kind == "CAPABILITY_INTENT":
            await send_chat_output(message, capability_intro(ctx.role, healthy=_health_ok()))
            return
        if intent.kind == "CONTROL_INTENT":
            await message.answer(preview_text(intent), reply_markup=inline([[("⬅️ 返回首页", "home")]]))
            return
        if not rate_limiter.allow(ctx):
            await message.answer("请求过于频繁，请稍后再试。")
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
        await send_chat_output(message, result.text)
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
        await edit_page(query, "公共视角预览\n\nPublic 用户可使用：AI 对话、受限文件入口、自己的任务与记忆。\n不包含私人项目、审批、系统状态或模型管理。", BACK)

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
        await edit_page(query, f"系统状态\n\n本地 AI：{health}\nQwen3.6：已加载\noMLX：{health}\nSwap：{swap}")

    @dp.callback_query(F.data == "owner:model")
    async def models(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "模型\n\nQwen3.6：已加载｜TEXT：可用｜CODING_AGENT：不可用\nVision / Audio / Embedding / Image Generation：未安装。\n不会自动下载模型。")

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
        await edit_page(query, text)

    @dp.callback_query(F.data.startswith(("owner:", "private:", "guidengji:")))
    async def private_route(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, query.data)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "此私人功能已受权限层保护。当前 V0.2 仅提供安全预览，不会执行文件、Git、发布或模型变更。")

    @dp.callback_query(F.data.startswith("public:"))
    async def public_route(query: CallbackQuery):
        ctx = identity(query)
        labels = {"public:image": "当前尚未安装图片理解模型。", "public:video": "当前尚未安装 Whisper 或视频分析模型。", "public:file": "当前仅规划 txt / md 安全文件入口；不接受压缩包或可执行文件。", "public:tasks": "当前没有可显示的任务。", "public:memory": "长期记忆默认未启用。启用后只会保存你的记忆；当前可用功能仍在本地开发模式。", "public:usage": "公共额度处于保守本地开发配置。", "public:help": "请不要发送助记词、私钥、密码或 API Token。链接不会自动下载。"}
        await edit_page(query, labels.get(query.data, "该功能暂不可用。"))

    @dp.callback_query()
    async def invalid_callback(query: CallbackQuery):
        await query.answer("该操作不存在或已过期。", show_alert=True)

    try:
        await dp.start_polling(bot)
    finally:
        private_control.close(); private_repo.close(); public_repo.close(); await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())

import asyncio
import logging
import subprocess

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from local_ai_control.bot.ui import inline
from local_ai_control.config.settings import Settings
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.chat import ChatService
from local_ai_control.services.control import ControlPlane
from local_ai_control.services.omlx import OmlxProvider
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository

OWNER_HOME = "🤖 本地 AI 控制中心\n\n请选择一个功能："
PUBLIC_HOME = "🤖 AI 助手\n\n你好！可以直接发送问题，或选择一个功能："


def owner_keyboard():
    return inline([
        [("💬 AI 对话", "chat:new"), ("✅ 待我审批", "owner:approvals")],
        [("📋 私人任务", "owner:tasks"), ("📁 私人项目", "private:projects")],
        [("🖼 图片处理", "owner:image"), ("🎬 视频处理", "owner:video")],
        [("📄 文件分析", "owner:file"), ("🧠 我的记忆", "owner:memory")],
        [("🧠 模型", "owner:model"), ("💻 系统状态", "owner:system")],
        [("⚙️ 功能管理", "owner:features"), ("📊 报告", "owner:reports")],
        [("🌐 公共视角预览", "public:preview"), ("🔧 设置", "owner:settings")],
    ])


def public_keyboard():
    return inline([
        [("💬 问 AI", "chat:new"), ("🖼 图片处理", "public:image")],
        [("🎬 视频处理", "public:video"), ("📄 文件分析", "public:file")],
        [("🧾 我的任务", "public:tasks"), ("🧠 我的记忆", "public:memory")],
        [("📊 我的额度", "public:usage"), ("ℹ️ 使用帮助", "public:help")],
    ])


def home_for(identity):
    return (OWNER_HOME, owner_keyboard()) if identity.role is Role.OWNER else (PUBLIC_HOME, public_keyboard())


def safe_command(command, fallback="不可用"):
    try:
        return subprocess.check_output(command, text=True, timeout=3).strip() or fallback
    except (OSError, subprocess.SubprocessError):
        return fallback


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
        await target.answer(title, reply_markup=keyboard)

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
        session_id = await chat_session(ctx)
        try:
            answer = ChatService(repo_for(ctx), OmlxProvider(), firewall).reply(ctx, session_id, message.text)
        except Exception as error:
            logging.warning("chat unavailable type=%s role=%s", type(error).__name__, ctx.role)
            answer = "AI 服务暂时不可用，请稍后重试。"
        await message.answer(answer)

    @dp.callback_query(F.data == "chat:new")
    async def new_chat(query: CallbackQuery):
        ctx = identity(query)
        session_id = repo_for(ctx).create_session(ctx)
        await query.message.answer("已创建新对话。现在可以直接发送中文问题。")
        await query.answer()
        logging.info("new chat role=%s session=%s", ctx.role, session_id)

    @dp.callback_query(F.data == "public:preview")
    async def public_preview(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True)
            return
        await query.message.answer("🌐 公共视角预览\n\nPublic 用户可使用：AI 对话、受限文件入口、自己的任务与记忆。\n不包含私人项目、审批、系统状态或模型管理。", reply_markup=inline([[("⬅️ 返回私人控制中心", "home")]]))
        await query.answer()

    @dp.callback_query(F.data == "home")
    async def home(query: CallbackQuery):
        await respond_home(query.message, identity(query))
        await query.answer()

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
        await query.message.answer(f"💻 系统状态\n\n本地 AI：{health}\nQwen3.6：已加载\noMLX：{health}\nSwap：{swap}", reply_markup=inline([[("⬅️ 返回首页", "home")]]))
        await query.answer()

    @dp.callback_query(F.data == "owner:model")
    async def models(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await query.message.answer("🧠 模型\n\nQwen3.6：已加载｜TEXT：可用｜CODING_AGENT：不可用\nVision / Audio / Embedding / Image Generation：未安装。\n不会自动下载模型。", reply_markup=inline([[("⬅️ 返回首页", "home")]]))
        await query.answer()

    @dp.callback_query(F.data.startswith(("owner:", "private:", "guidengji:")))
    async def private_route(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, query.data)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await query.message.answer("此私人功能已受权限层保护。当前 V0.2 仅提供安全预览，不会执行文件、Git、发布或模型变更。", reply_markup=inline([[("⬅️ 返回首页", "home")]]))
        await query.answer()

    @dp.callback_query(F.data.startswith("public:"))
    async def public_route(query: CallbackQuery):
        ctx = identity(query)
        labels = {"public:image": "图片 AI 理解模型尚未安装。", "public:video": "视频 AI 分析模型尚未安装。", "public:file": "当前仅规划 txt / md 安全文件入口；不接受压缩包或可执行文件。", "public:tasks": "当前没有可显示的任务。", "public:memory": "长期记忆默认关闭；未来可在这里明确启用、查看和删除。", "public:usage": "公共额度处于保守本地开发配置。", "public:help": "请不要发送助记词、私钥、密码或 API Token。链接不会自动下载。"}
        await query.message.answer(labels.get(query.data, "该功能暂不可用。"), reply_markup=inline([[("⬅️ 返回首页", "home")]]))
        await query.answer()

    @dp.callback_query()
    async def invalid_callback(query: CallbackQuery):
        await query.answer("该操作不存在或已过期。", show_alert=True)

    try:
        await dp.start_polling(bot)
    finally:
        private_control.close(); private_repo.close(); public_repo.close(); await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())

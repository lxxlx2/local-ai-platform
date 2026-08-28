import asyncio
import hashlib
import logging
import subprocess

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from local_ai_control.bot.ui import (
    BACK, back_for, inline, media_menu, owner_dashboard, owner_task_menu,
    public_dashboard, system_menu, workflow_controls, workflow_menu, video_production_menu,
    source_mode_menu, execution_mode_menu, language_menu, voice_menu, completion_mode_menu,
    confirmation_menu,
)
from local_ai_control.bot.media_wizard import MediaWizardController,MediaWizardStore,WizardStep,wizard_summary
from local_ai_control.services.capabilities import capability_intro, model_identity, routed_capability_text
from local_ai_control.config.settings import Settings
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.async_runtime import AsyncRuntimeExecutor,FastPromptRequired,parse_chat_request,sync_chat_reply
from local_ai_control.services.control import ControlPlane
from local_ai_control.services.intent import classify_owner_text, preview_text
from local_ai_control.services.models import ModelRegistry, model_center_text
from local_ai_control.services.multimodal import AttachmentValidationError, MultimodalRouter
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.rate_limit import PublicRateLimiter
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall
from local_ai_control.services.storage import ScopedSQLiteRepository
from local_ai_control.services.supervisor import JobStatus, SupervisorRepository, WorkflowSupervisor, default_demo_runners
from local_ai_control.services.qwen38_runtime import ContextLimitExceeded, RuntimeUnavailable
from local_ai_control.services.runtime_providers import HeavyModelConflict, RuntimeProviderFactory
from local_ai_control.services.vision import TelegramImageService

OWNER_HOME = "本地 AI 控制中心\n\n请选择一个功能："
PUBLIC_HOME = "AI 助手\n\n你好！可以直接发送问题，或选择一个功能："
SUPERVISOR_CONTROL_ACTIONS = {"pause", "resume", "cancel", "retry"}


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


def chat_request(text):
    return parse_chat_request(text)


def chat_reply_with_runtime(provider_factory,repository,firewall,ctx,session_id,text):
    return sync_chat_reply(provider_factory,repository,firewall,ctx,session_id,text)


async def owner_image_reply(role,image_service,runtime_executor,bot,file_ref,*,declared_size,caption=""):
    if role is not Role.OWNER: raise AuthorizationDenied("Owner image inference only")
    request=await image_service.stage(bot,file_ref,declared_size=declared_size,caption=caption)
    return await runtime_executor.vision(image_service,request)


async def send_start_dashboard(target, title, keyboard):
    """Remove the legacy reply keyboard without leaving a ghost message."""
    cleanup = await target.answer("正在打开控制中心…", reply_markup=ReplyKeyboardRemove())
    dashboard = await target.answer(title, reply_markup=keyboard)
    try:
        await cleanup.delete()
    except Exception as error:
        logging.warning("start cleanup delete failed type=%s", type(error).__name__)
    return dashboard


async def send_rendered_output(message, renderer, text):
    rendered = renderer.package(text)
    for chunk in rendered.chunks:
        await message.answer(chunk, parse_mode=rendered.parse_mode)
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
    supervisor_repo = SupervisorRepository(); supervisor_repo.migrate()
    workflow_supervisor = WorkflowSupervisor(supervisor_repo, default_demo_runners(real_validation=True))
    firewall = SecretFirewall()
    renderer = TelegramOutputRenderer()
    rate_limiter = PublicRateLimiter(settings.public_messages_per_minute, settings.public_messages_per_hour, settings.public_messages_per_day)
    multimodal_router = MultimodalRouter()
    registry = ModelRegistry()
    provider_factory = RuntimeProviderFactory(registry)
    runtime_executor = AsyncRuntimeExecutor(provider_factory)
    image_service = TelegramImageService(provider_factory.main)
    media_wizard_store=MediaWizardStore()
    media_wizard=MediaWizardController(media_wizard_store)
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

    async def send_chat_output(message, text):
        return await send_rendered_output(message, renderer, text)

    @dp.message(CommandStart())
    async def start(message: Message):
        await respond_home(message, identity(message))

    @dp.message(F.photo | F.video | F.audio | F.voice)
    async def media_input(message: Message):
        ctx = identity(message)
        if ctx.role is not Role.OWNER:
            await send_chat_output(message, "Public 媒体能力尚未开放；请使用文字提问。")
            return
        mime = "image/jpeg" if message.photo else ("video/mp4" if message.video else "audio/mpeg")
        decision = multimodal_router.route(ctx.role, message.caption or "", mime)
        if message.photo:
            photo=message.photo[-1]
            try:
                answer=await owner_image_reply(ctx.role,image_service,runtime_executor,bot,photo,
                                               declared_size=photo.file_size or 0,caption=message.caption or "")
            except (AttachmentValidationError,ValueError,PermissionError):
                answer="图片未通过安全校验，请发送 20MB 以内的 JPEG 图片。"
            except (RuntimeUnavailable,ContextLimitExceeded,HeavyModelConflict,RuntimeError) as error:
                logging.warning("vision unavailable type=%s",type(error).__name__)
                answer="图片理解服务暂时不可用，请稍后重试。"
            await send_chat_output(message,answer)
            return
        runtime_health=await runtime_executor.runtime_health()
        await send_chat_output(message, routed_capability_text(decision,registry=registry,runtime_health=runtime_health))

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
            runtime_health=await runtime_executor.runtime_health()
            await send_chat_output(message, model_identity(registry=registry,runtime_health=runtime_health))
            return
        if intent.kind == "CAPABILITY_INTENT":
            runtime_health=await runtime_executor.runtime_health()
            await send_chat_output(message, capability_intro(ctx.role,registry=registry,runtime_health=runtime_health))
            return
        if intent.kind == "CONTROL_INTENT":
            await message.answer(preview_text(intent), reply_markup=inline([[("⬅️ 返回首页", "home")]]))
            return
        try:
            routed = multimodal_router.route(ctx.role, message.text)
        except PermissionError:
            await send_chat_output(message, "该生成能力当前仅限 Owner 使用。")
            return
        if routed.intent.value != "CHAT":
            runtime_health=await runtime_executor.runtime_health()
            await send_chat_output(message, routed_capability_text(routed,registry=registry,runtime_health=runtime_health))
            return
        session_id = await chat_session(ctx)
        try:
            result = await runtime_executor.chat(repo_for(ctx),firewall,ctx,session_id,message.text)
        except ContextLimitExceeded:
            await send_chat_output(message,"上下文超过 MAIN 的 16K 安全限制，请缩短问题或新建对话。")
            return
        except FastPromptRequired:
            await send_chat_output(message,"请在 /fast 后输入问题。")
            return
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

    @dp.callback_query(F.data == "media:video")
    async def video_production(query:CallbackQuery):
        ctx=identity(query)
        try: authorize(ctx,"owner:system")
        except AuthorizationDenied: await query.answer("当前账号没有此操作权限。",show_alert=True); return
        await edit_page(query,"视频\n\n创建演示视频，或查看当前视频能力。",video_production_menu())

    @dp.callback_query(F.data == "media:new")
    async def media_new(query:CallbackQuery):
        ctx=identity(query)
        try: media_wizard.start(ctx.role,ctx.internal_user_id)
        except PermissionError: await query.answer("当前账号没有此操作权限。",show_alert=True); return
        await edit_page(query,"新建视频\n\n请发送一个简短的任务名称。",inline([[('取消','mw:cancel')]]))

    async def active_media_wizard(message:Message):
        ctx=identity(message); session=media_wizard_store.get(ctx.internal_user_id)
        return bool(session and session.step in {WizardStep.TASK_NAME,WizardStep.MATERIALS})

    @dp.message(active_media_wizard,F.text)
    async def media_wizard_text(message:Message):
        ctx=identity(message)
        try: session=media_wizard.text(ctx.role,ctx.internal_user_id,message.text)
        except (PermissionError,KeyError,ValueError): await message.answer("输入无效，请返回视频菜单重新开始。"); return
        if session.step is WizardStep.SOURCE_MODE:
            await message.answer("请选择材料来源。",reply_markup=source_mode_menu())
        else:
            await message.answer("请选择执行方式。",reply_markup=execution_mode_menu())

    @dp.callback_query(F.data.startswith("mw:"))
    async def media_wizard_callback(query:CallbackQuery):
        ctx=identity(query); data=query.data
        try:
            if data=="mw:cancel": media_wizard_store.cancel(ctx.internal_user_id); await edit_page(query,"视频任务已取消。",video_production_menu()); return
            if data.startswith("mw:source:"):
                value=data.split(":",2)[2]; media_wizard.choice(ctx.role,ctx.internal_user_id,"source_mode",value)
                await edit_page(query,"请发送材料说明、公开链接或简要需求。\n\n一次只发送一项；稍后可以在任务中补充。",inline([[('取消','mw:cancel')]])); return
            if data.startswith("mw:exec:"):
                media_wizard.choice(ctx.role,ctx.internal_user_id,"execution_mode",data.split(":",2)[2]); await edit_page(query,"请选择主要语言。",language_menu()); return
            if data.startswith("mw:lang:"):
                media_wizard.choice(ctx.role,ctx.internal_user_id,"language",data.split(":",2)[2]); await edit_page(query,"请选择声音。",voice_menu()); return
            if data.startswith("mw:voice:"):
                media_wizard.choice(ctx.role,ctx.internal_user_id,"voice",data.split(":",2)[2]); await edit_page(query,"请选择完成方式。",completion_mode_menu()); return
            if data.startswith("mw:complete:"):
                session=media_wizard.choice(ctx.role,ctx.internal_user_id,"completion_mode",data.split(":",2)[2]); await edit_page(query,wizard_summary(session),confirmation_menu()); return
            if data=="mw:confirm":
                media_wizard.confirm(ctx.role,ctx.internal_user_id); await edit_page(query,"视频任务已创建。\n\n材料会在私有工作区处理；生成完成后会在这里等待你的最终确认。",video_production_menu()); return
            await query.answer("该操作尚未就绪。",show_alert=True)
        except (PermissionError,KeyError,ValueError): await query.answer("步骤无效或已过期，请重新开始。",show_alert=True)

    @dp.callback_query(F.data == "menu:system")
    async def owner_system_menu(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "系统管理\n\n请选择一个功能：", system_menu())

    def owner_workflow(query):
        ctx = identity(query)
        authorize(ctx, "owner:tasks")
        return ctx

    def workflow_summary(owner_id):
        jobs = workflow_supervisor.list_jobs(owner_id, 20)
        running = sum(job.status is JobStatus.RUNNING for job in jobs)
        queued = sum(job.status is JobStatus.QUEUED for job in jobs)
        blocked = sum(job.status in {JobStatus.BLOCKED, JobStatus.FAILED} for job in jobs)
        completed = next((job for job in jobs if job.status is JobStatus.COMPLETED), None)
        return (
            f"自动工作流\n\n运行中：{running}\n排队中：{queued}\n失败或阻塞：{blocked}"
            f"\n最近完成：{completed.title if completed else '无'}",
            jobs,
        )

    @dp.callback_query(F.data == "owner:tasks")
    async def owner_tasks(query: CallbackQuery):
        try:
            owner_workflow(query)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "私人任务\n\n选择任务预览或程序级自动工作流：", owner_task_menu())

    @dp.callback_query(F.data == "menu:workflows")
    @dp.callback_query(F.data == "supervisor:status")
    async def workflow_status(query: CallbackQuery):
        try:
            ctx = owner_workflow(query)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        text, jobs = workflow_summary(ctx.internal_user_id)
        keyboard = workflow_menu()
        if jobs:
            latest = jobs[0]
            text += (f"\n\n最近任务：{latest.title}\n状态：{latest.status.value}"
                     f"\n阶段：{latest.current_stage.value}\nReview Round：{latest.review_round}"
                     f"\n最近活动：{latest.updated_at}")
            if latest.last_error:
                text += f"\n错误：{latest.last_error[:200]}"
            keyboard = workflow_controls(latest.job_id, latest.status.value)
        await edit_page(query, text, keyboard)

    @dp.callback_query(F.data == "supervisor:demo")
    async def workflow_demo(query: CallbackQuery):
        try:
            ctx = owner_workflow(query)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        demo_key = hashlib.sha256(query.id.encode()).hexdigest()[:32]
        job = workflow_supervisor.create_demo(ctx.internal_user_id, job_id=f"telegram-demo:{demo_key}")
        await edit_page(query, f"已创建安全演示工作流。\n\n状态：{job.status.value}\n阶段：{job.current_stage.value}",
                        workflow_controls(job.job_id, job.status.value))

    @dp.callback_query(F.data.startswith("supervisor:"))
    async def workflow_control(query: CallbackQuery):
        try:
            ctx = owner_workflow(query)
            _, action, job_id = query.data.split(":", 2)
            if action == "view":
                job = workflow_supervisor.status(job_id, ctx.internal_user_id)
            elif action in SUPERVISOR_CONTROL_ACTIONS:
                job = getattr(workflow_supervisor, action)(job_id, ctx.internal_user_id)
            else:
                raise ValueError("unsupported supervisor action")
        except (AuthorizationDenied, PermissionError):
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        except (KeyError, ValueError, AttributeError):
            await query.answer("任务不存在或操作无效。", show_alert=True); return
        await edit_page(query, f"工作流任务\n\n任务：{job.title}\n状态：{job.status.value}"
                        f"\n阶段：{job.current_stage.value}\nReview Round：{job.review_round}",
                        workflow_controls(job.job_id, job.status.value))

    @dp.callback_query(F.data == "owner:system")
    async def system(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True)
            return
        runtime=await runtime_executor.runtime_health()
        swap = await runtime_executor.call(safe_command,["sysctl", "-n", "vm.swapusage"])
        await edit_page(query, f"系统状态\n\nMAIN / Qwen3.8：{runtime['MAIN']}\nFAST / Qwen3.6：{runtime['FAST']}\nSwap：{swap}", back_for(query.data))

    @dp.callback_query(F.data == "owner:model")
    async def models(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:system")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        runtime=await runtime_executor.runtime_health()
        await edit_page(query, model_center_text(registry,runtime), back_for(query.data))

    @dp.callback_query(F.data == "owner:memory")
    async def owner_memory(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, "owner:memory")
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        await edit_page(query, "我的记忆\n\n可用：最近记忆、长期偏好、项目记忆、搜索、删除/设置。\n当前为本地开发存储；语义向量检索仍等待 Embedding Provider。")

    @dp.callback_query(F.data.in_({"owner:image", "owner:video", "owner:file", "owner:audio", "owner:image_generate", "owner:video_generate", "owner:media_jobs", "owner:web"}))
    async def owner_capability(query: CallbackQuery):
        ctx = identity(query)
        try:
            authorize(ctx, query.data)
        except AuthorizationDenied:
            await query.answer("当前账号没有此操作权限。", show_alert=True); return
        routed_by_callback={
            "owner:image": multimodal_router.route(ctx.role,"描述图片","image/jpeg"),
            "owner:video": multimodal_router.route(ctx.role,"总结视频","video/mp4"),
            "owner:audio": multimodal_router.route(ctx.role,"转写语音","audio/mpeg"),
            "owner:image_generate": multimodal_router.route(ctx.role,"生成图片"),
            "owner:video_generate": multimodal_router.route(ctx.role,"生成视频"),
        }
        if query.data in routed_by_callback:
            runtime=await runtime_executor.runtime_health()
            text=routed_capability_text(routed_by_callback[query.data],registry=registry,runtime_health=runtime)
        else:
            text = {
            "owner:file": "文件分析\n\n当前仅开放受控 txt / md 入口；不会任意读取私人项目文件。",
            "owner:media_jobs": "任务与进度\n\nMediaJob 支持排队、进度、取消与失败状态；当前生产 Bot 尚未部署此版本。",
            "owner:web": "联网研究\n\n安全 URL Fetch 与 Search Provider 已接入代码；当前 provider 尚未部署。",
            }[query.data]
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
        runtime_executor.shutdown(); private_control.close(); private_repo.close(); public_repo.close(); supervisor_repo.close(); media_wizard_store.close(); await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())

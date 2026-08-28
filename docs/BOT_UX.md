# Bot UX

Owner homepage: AI chat, approvals, private tasks/projects, media/file entry points, memory, model/system status, feature/report settings, and a public-view preview. Public homepage: AI chat, media/file placeholders, the user’s own tasks/memory/usage, and help. Buttons are Chinese-first and callbacks are authorized server-side.

Users may always send ordinary Chinese text directly. It becomes a chat request with bounded history and no system tools. The help and memory surfaces explicitly state: do not send seed phrases, private keys, passwords, recovery codes, or API tokens through Telegram.

Ordinary chat uses renderer-owned, Telegram-safe HTML. Untrusted model HTML is escaped first. Inline source is emitted only as `<code>` and fenced/multiline source only as balanced `<pre><code>`; model text cannot create tags. Markdown bullets become `•`, horizontal rules become spacing, and code operators/literals remain unchanged. Brief questions receive a concise default response; explicit detailed requests may be longer and are chunked at safe, entity-balanced boundaries.

V0.2 uses an Inline Keyboard dashboard rather than a persistent Reply Keyboard. Owner navigation has eight compact first-level buttons, with file/media and system-management submenus. Public navigation has six scoped first-level buttons. A route registry defines every page's parent and root; “返回” opens the parent while “返回首页” is reserved for root. Dashboard/submenu navigation edits the current message when possible. `/start` uses a temporary ReplyKeyboard cleanup message, sends the dashboard, then best-effort deletes the temporary message.

## UX principles

Telegram is the primary simple operator surface, not a dense admin console. The interface should be usable without remembering commands or understanding model/provider internals.

Required principles:

- keep the first-level menu small;
- prefer short text labels over decorative icons;
- avoid emoji-heavy or visually noisy button layouts;
- expose advanced options only after the user enters the relevant workflow;
- prefer one clear question per step;
- provide sensible defaults and an explicit `自动` option where possible;
- show only currently usable/qualified choices by default;
- preserve a simple `返回` path at every step;
- do not require the user to type internal ids, local runtime paths, model names, Git commands, or JSON;
- show a compact confirmation summary before expensive work starts;
- notify only on meaningful state changes, not heartbeat/progress spam;
- external publishing and destructive cleanup remain explicit gated actions.

The existing navigation model remains unchanged: media/video is reached through the existing media entry rather than adding more first-level buttons.

## Owner video task wizard

Selecting the Owner video function starts a guided, stateful wizard. The user should not need to compose a structured command such as `type=video language=auto persona=...` manually.

Recommended interaction:

```text
媒体
  -> 视频
  -> 新建视频
```

Then ask one step at a time.

### Step 1: task name

Prompt:

```text
视频名称？
```

The user types a human-readable name such as `solana demo`. Host code derives a safe task slug; the user does not type the slug.

### Step 2: source/material mode

Show only simple choices:

```text
材料方式
[上传 PPT + 文稿]
[只上传 PPT]
[只上传文稿]
[直接描述任务]
[返回]
```

Behavior:

- `PPT + 文稿`: wait for PPTX and TXT/DOCX/approved script input; final script is owner-provided unless the user explicitly asks Qwen to revise it;
- `只上传 PPT`: use notes/slide content and local Qwen according to narration policy;
- `只上传文稿`: create an audio-first or later scene/video job without requiring PPT;
- `直接描述任务`: ask for a brief, then local Qwen produces a durable draft script/scene plan before media generation.

Telegram-uploaded material is copied into the task's private bounded workspace after validation. Users should not be asked for local filesystem paths when operating through Telegram.

### Step 3: language

Choices should be derived from qualified language routes:

```text
语言
[自动]
[中文]
[English]
[返回]
```

`自动` is the default. Language detection is performed on the final narration script. Advanced mixed-language behavior stays out of the normal wizard and is available only through an advanced/settings path if needed.

### Step 4: voice/persona

Default simple choices:

```text
声音
[自动推荐]
[中文男声 25]
[English Male 25]
[我的声音/人物]
[返回]
```

The UI shows friendly labels, not profile ids. Internally they map to qualified profiles such as `zh-male-25-default` and `en-male-25-default`.

`自动推荐` chooses the qualified default voice for the final script language.

`我的声音/人物` opens a second-level selector containing only qualified Owner PersonaProfiles. If none exist, explain briefly that a custom persona must first be created/qualified. Do not expose training controls in the normal video wizard.

### Step 5: output/publish preference

Keep this simple:

```text
完成后怎么处理？
[先预览，确认后发布]
[只保存在本地]
[返回]
```

Default is `先预览，确认后发布`.

The normal user should not choose Git remote/branch/path. For the current Owner video product workflow, the configured publish target is `lxxlx2/ai_video_product` with task-named archival.

### Step 6: confirmation

Before generation show one compact summary:

```text
视频任务
名称: solana demo
材料: PPT + 文稿
语言: 自动
声音: 自动推荐
完成后: 预览后发布

[开始生成]
[修改]
[取消]
```

Do not show internal model revisions, filesystem roots, hashes, or provider implementation details in this normal confirmation view.

## Video result/review flow

After generation reaches `REVIEW_PENDING`, send a Telegram-friendly preview when file size/API limits permit. If direct preview delivery is too large, provide a bounded local/Tailscale/private artifact link or a smaller preview artifact according to policy.

Result message should remain compact:

```text
视频已生成
名称: solana demo
时长: 00:58
状态: 等待确认

[通过并发布]
[重新生成]
[修改文稿]
[取消]
```

`通过并发布` is an explicit approval gate. Only after this callback is authorized and bound to the exact output hash may the deterministic publisher copy approved product artifacts into the configured `ai_video_product/<task-name>/` structure and push them to GitHub.

The model itself has no Git push authority.

After successful publish is verified against the expected commit/output hash, the bot reports the published result and then applies the configured local-retention policy.

Default product-video cleanup policy:

1. keep small durable metadata/manifests needed for audit/recovery;
2. remove published local duplicate MP4 files and expendable render/audio/segment intermediates from the private job workspace;
3. never delete source material, persona assets, training data, or the only copy of an unpublished artifact automatically;
4. cleanup happens only after remote publish verification succeeds;
5. failed publish leaves all required local artifacts intact and reports retryable state;
6. the Owner may choose `保留本地` before cleanup if desired.

The bot should report the cleanup result simply, for example:

```text
已发布到 ai_video_product
本地成品缓存已清理
任务记录已保留
```

## State and interruption behavior

The video wizard is durable. If Telegram reconnects or the bot restarts, the user can reopen `视频 -> 我的任务` and continue from the last valid step rather than re-entering the whole task.

At most one unanswered wizard question should be active for a task. Unexpected text/file input is not silently interpreted as a privileged action; the bot explains the expected input and keeps the wizard state.

Capability questions are deterministic product responses, not free-form model descriptions. They disclose the active model and local backend, while respecting Owner/Public scope. Native code entities prevent `@decorator` from becoming a username link and preserve `*args`, `**kwargs`, dunder names, `<`, `>`, and `&`.

The Owner “私人任务” page may expose an “自动工作流” submenu after Workflow Supervisor deployment. It is deliberately absent from the eight-button home page. The submenu can show counts/details and control Owner-owned jobs or create the fixed safe demo; it cannot turn arbitrary Telegram text into a coding-agent task. Public users have no visible entry and are denied by server-side authorization.

Production Capability Consolidation V0.1 keeps the eight-button home page and expands only its existing submenus. Owner media navigation includes visual understanding, audio, image/video generation, and MediaJob progress; system navigation includes safe web research. Natural-language media/web intent is deterministic and registry-bound. Qualified Qwen3.8 Owner image understanding is routed through its private spool and localhost sidecar; Public image inference remains disabled. Every unqualified provider still says “registered, not qualified/configured” and is not executed. RAW selection, voice cloning, browser automation, and generative media remain Owner-only; Public capability scope is unchanged.

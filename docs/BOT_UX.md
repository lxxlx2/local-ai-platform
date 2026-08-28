# Bot UX

Owner homepage: AI chat, approvals, private tasks/projects, media/file entry points, memory, model/system status, feature/report settings, and a public-view preview. Public homepage: AI chat, media/file placeholders, the user’s own tasks/memory/usage, and help. Buttons are Chinese-first and callbacks are authorized server-side.

Users may always send ordinary Chinese text directly. It becomes a chat request with bounded history and no system tools. The help and memory surfaces explicitly state: do not send seed phrases, private keys, passwords, recovery codes, or API tokens through Telegram.

Ordinary chat uses renderer-owned, Telegram-safe HTML. Untrusted model HTML is escaped first. Inline source is emitted only as `<code>` and fenced/multiline source only as balanced `<pre><code>`; model text cannot create tags. Markdown bullets become `•`, horizontal rules become spacing, and code operators/literals remain unchanged. Brief questions receive a concise default response; explicit detailed requests may be longer and are chunked at safe, entity-balanced boundaries.

V0.2 uses an Inline Keyboard dashboard rather than a persistent Reply Keyboard. Owner navigation has eight compact first-level buttons, with file/media and system-management submenus. Public navigation has six scoped first-level buttons. A route registry defines every page's parent and root; `返回` opens the parent while `返回首页` is reserved for root. Dashboard/submenu navigation edits the current message when possible. `/start` uses a temporary ReplyKeyboard cleanup message, sends the dashboard, then best-effort deletes the temporary message.

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

Selecting the Owner video function starts a guided, durable wizard. The user should not need to compose a structured command such as `type=video language=auto persona=...` manually.

Recommended entry:

```text
媒体
  -> 视频
  -> 新建视频
```

### Step 1: task name

Prompt:

```text
视频名称？
```

The Owner types a human-readable name such as `solana demo`. Host code derives a safe stable task slug; the Owner never types the slug.

### Step 2: source/material origin

After the name is accepted, ask where the task requirements/materials come from:

```text
材料从哪里来？
[上传材料]
[发送链接]
[上传材料 + 链接]
[直接描述任务]
[返回]
```

This replaces a rigid PPT/TXT-only assumption while keeping the interaction simple.

#### 上传材料

The Owner may send supported files such as PPTX, TXT, DOCX, PDF, images, audio or video according to current qualified capabilities. Telegram uploads are copied into the exact private task workspace after type/size/path validation. The bot summarizes what was received and offers only simple actions:

```text
已收到：
1. presentation.pptx
2. script.txt

[继续上传]
[材料齐了]
[取消]
```

The user is not asked for local filesystem paths through Telegram.

#### 发送链接

Prompt:

```text
请发送任务或要求所在的链接。
可以连续发送多个链接。

[链接齐了]
[取消]
```

Owner-supplied URLs are handed to the bounded Search/Browser requirement-intake layer. Fetched pages and linked documents are `UNTRUSTED DATA`; instructions inside them never gain execution, download, filesystem, Git, credential or authorization power.

The requirement-intake layer should persist source URL, retrieval timestamp, source/content hash and evidence provenance, then extract only task-relevant facts such as:

- objective/prompt;
- required number of videos;
- length limits;
- mandatory questions/content;
- language/format requirements;
- submission requirements;
- deadlines;
- evaluation criteria;
- official reference material;
- explicit prohibitions/constraints.

When useful, the bounded research layer may follow relevant public official links to clarify requirements. It must not invent unavailable page contents. Login, CAPTCHA, inaccessible pages or network failure produce a short recoverable prompt such as:

```text
这个页面目前无法完整读取。
[上传页面/PDF]
[稍后重试]
[取消]
```

#### 上传材料 + 链接

Accept both validated uploads and Owner-supplied links into one requirement-intake job. Provenance must distinguish uploaded source material, retrieved external evidence and model-generated artifacts.

#### 直接描述任务

Ask for one short free-form brief. Local Qwen may convert it into a durable production brief/script/scene plan according to MediaJob policy.

### Step 3: requirement/material readiness

After uploads/links/brief are complete, deterministic intake plus Local Qwen may generate durable intermediate artifacts:

```text
requirements.json / requirements.md
production_brief.md
script.txt
scene_plan.json
prompt_pack.json
source_evidence.json
```

The user does not need to see these by default. They remain available through `查看方案` or task details.

If the task requires a real personal fact that is not supported by Owner-provided material or approved project context, Qwen must not fabricate it. The workflow enters `MISSING_OWNER_FACT` and asks only the minimum necessary question, for example:

```text
这个视频需要你的真实活动经历，但当前材料里没有足够信息。
请简单告诉我：你组织过什么活动？

[取消]
```

General rule: infer what can be inferred, research what can be safely researched, generate what can be generated, and ask the Owner only for missing real-world personal facts or explicit decisions.

### Step 4: execution style

Keep the decision small:

```text
执行方式
[自动完成]
[先看文稿]
[返回]
```

Default is `自动完成`.

`自动完成` allows requirement analysis -> script/scene preparation -> local generation/composition -> final preview without an intermediate script approval gate.

`先看文稿` stops at `SCRIPT_READY`, shows the durable draft script, and requires Owner continue/revise before expensive synthesis.

### Step 5: language

Choices are derived from qualified language routes:

```text
语言
[自动]
[中文]
[English]
[返回]
```

`自动` is the default and is determined from the final narration script. Advanced mixed-language behavior stays out of the normal wizard.

### Step 6: voice/persona

Default simple choices:

```text
声音
[自动推荐]
[中文男声 25]
[English Male 25]
[我的声音/人物]
[返回]
```

The UI shows friendly labels, never profile ids. Internally they map to qualified profiles such as `zh-male-25-default` and `en-male-25-default`.

`我的声音/人物` opens a second-level selector containing only qualified Owner PersonaProfiles. Training controls are not shown in the ordinary video wizard.

### Step 7: output/publish preference

```text
完成后怎么处理？
[先预览，确认后发布]
[只保存在本地]
[返回]
```

Default is `先预览，确认后发布`.

The normal Owner does not choose Git remote/branch/path. The configured product target for approved video deliverables is `lxxlx2/ai_video_product` using the canonical task layout.

### Step 8: confirmation

Before generation, show one compact summary. Example for URL-driven automatic work:

```text
准备生成视频
名称: solana demo
来源: 1 个链接
识别要求: 2 个视频，每个不超过 60 秒
语言: English
声音: English Male 25
材料: 系统自动准备
模式: 自动完成
完成后: 先预览

[开始生成]
[查看方案]
[补充材料]
[取消]
```

`查看方案` may reveal requirements, script, scene plan and source/evidence summary. Normal confirmation does not expose filesystem roots, hashes, model revisions or provider implementation details.

## Video result/review flow

After generation reaches `REVIEW_PENDING`, send a Telegram-friendly preview when file/API limits permit. If direct preview is too large, provide a bounded private preview artifact/link or smaller preview according to policy.

Result message stays compact:

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

`通过并发布` is an explicit approval gate bound to the exact output hash. Regeneration invalidates the prior approval. Only an authorized exact-output approval may trigger deterministic Git/LFS publishing.

The model itself has no Git push authority.

After remote publish verification succeeds, the bot reports the published result and applies configured retention/cleanup policy.

Default product-video cleanup policy:

1. keep small durable metadata/manifests needed for audit/recovery;
2. remove verified published local duplicate MP4 files and expendable render/audio/segment intermediates;
3. never delete source material, persona assets, training data, or the only copy of an unpublished artifact automatically;
4. cleanup happens only after remote publish verification succeeds;
5. failed publish leaves all required local artifacts intact and reports retryable state;
6. the Owner may choose `保留本地` before cleanup if desired.

Example result:

```text
已发布到 ai_video_product
本地成品缓存已清理
任务记录已保留
```

## State and interruption behavior

The video wizard is durable. If Telegram reconnects or the bot restarts, the Owner can reopen `视频 -> 我的任务` and continue from the last valid step.

At most one unanswered wizard question is active for a task. Unexpected text/file input is not silently interpreted as a privileged action; the bot explains the expected input and preserves wizard state.

Capability questions are deterministic product responses, not free-form model descriptions. They disclose the active model and local backend while respecting Owner/Public scope.

The Owner `私人任务` page may expose an `自动工作流` submenu after Workflow Supervisor deployment. It remains absent from the eight-button home page. Public users have no visible Owner workflow entry and are denied by server-side authorization.

Production Capability Consolidation keeps the compact first-level home page and expands only existing submenus. Owner media navigation includes visual understanding, audio, image/video generation and MediaJob progress; system navigation includes safe web research. Natural-language media/web intent is deterministic and registry-bound. Unqualified providers still report registered/not-qualified and are not executed. RAW selection, voice cloning, browser automation and generative media remain Owner-only unless a later approved architecture changes that scope.

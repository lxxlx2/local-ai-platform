# Bot UX

Owner homepage: AI chat, approvals, private tasks/projects, media/file entry points, memory, model/system status, feature/report settings, and a public-view preview. Public homepage: AI chat, media/file placeholders, the user’s own tasks/memory/usage, and help. Buttons are Chinese-first and callbacks are authorized server-side.

Users may always send ordinary Chinese text directly. It becomes a chat request with bounded history and no system tools. The help and memory surfaces explicitly state: do not send seed phrases, private keys, passwords, recovery codes, or API tokens through Telegram.

Ordinary chat uses renderer-owned, Telegram-safe HTML. Untrusted model HTML is escaped first. Inline source is emitted only as `<code>` and fenced/multiline source only as balanced `<pre><code>`; model text cannot create tags. Markdown bullets become `•`, horizontal rules become spacing, and code operators/literals remain unchanged. Brief questions receive a concise default response; explicit detailed requests may be longer and are chunked at safe, entity-balanced boundaries.

V0.2 uses an Inline Keyboard dashboard rather than a persistent Reply Keyboard. Owner navigation has eight compact first-level buttons, with file/media and system-management submenus. Public navigation has six scoped first-level buttons. A route registry defines every page's parent and root; “返回” opens the parent while “返回首页” is reserved for root. Dashboard/submenu navigation edits the current message when possible. `/start` uses a temporary ReplyKeyboard cleanup message, sends the dashboard, then best-effort deletes the temporary message.

Capability questions are deterministic product responses, not free-form model descriptions. They disclose the active model and local backend, while respecting Owner/Public scope. Native code entities prevent `@decorator` from becoming a username link and preserve `*args`, `**kwargs`, dunder names, `<`, `>`, and `&`.

Owner Settings includes a Learning & Training dashboard for candidate counts, datasets, evals, adapters, and privacy. Owner AI answers expose lightweight GOOD/BAD/SKIP feedback controls; markup is attached only to the final Telegram chunk. Public users cannot access learning routes or create candidates. Feedback records candidates only—it never starts training or switches the current model.

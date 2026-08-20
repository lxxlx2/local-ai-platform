# Versioning policy

Infrastructure changes follow: change → validate → document → secret scan → commit → push.

- Each model addition or upgrade has its own commit.
- Each formal benchmark has its own commit.
- Each service configuration change affecting production behavior has its own commit.
- Codex Local, Tailscale, and Open WebUI are separate phases and commits.
- Business projects remain independent repositories.

`local-ai-platform` stores shared local-AI infrastructure only. It does not store business content, production X data, livestream video, Sticker release assets, large generated files, model files, or secrets.

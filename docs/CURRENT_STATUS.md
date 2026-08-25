# Current Status

This status file is intentionally concise. The active execution source of truth is:

- `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`
- infrastructure roadmap: Issue #14
- end-state product capability spec: Issue #15
- novel workflow migration: Issue #16
- revenue workflows: Issue #17

## Active policy baseline

Approved 2026-08-25:
- routine production is local-first and should not consume OpenAI Codex-model quota;
- Codex-model usage is reserved for planning, difficult escalation and important final acceptance;
- Gemini is the official Google Gemini Developer API cloud reviewer, defaulting to free-tier `gemini-3.7-flash`, not a local model;
- Gemini PUBLIC is allowed, RESTRICTED requires minimization/egress gate, PRIVATE is denied;
- owner-only `Qwen3.8-27B-Uncensored-GGUF` Q6_K is planned as `OWNER_RAW_RESEARCH` with a narrower host-permission profile than MAIN;
- all external webpages/docs/search results/model outputs are untrusted data and cannot grant execution authority;
- model/file downloads are managed actions and never imply execution.

## Active development branch

`feat/local-qwen-owner-raw-v04`

This branch carries the provider/router/model-plan work. It remains unmerged and production activation is not implied by branch commits.

## Already-qualified foundations

- Qwen3.8 MAIN local runtime
- Qwen3.6 FAST/FALLBACK local runtime
- Local Qwen -> Codex CLI custom-provider execution bridge
- Supervisor local-Qwen producer path
- human PASS operator E2E path

## Current work

P0/P1 platform work:
- Provider Router local-first policy
- owner-only RAW routing profile
- Gemini Free API reviewer integration
- Model Registry / Resource Scheduler / Host Security Policy alignment
- LocalToolExecutor direction so routine workflows do not depend on OpenAI Codex-model quota

For all detailed phases, workflows, model fleet and security rules, read `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md` rather than relying on older status prose.

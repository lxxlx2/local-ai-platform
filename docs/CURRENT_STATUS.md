# Current Status

This status file is intentionally concise. The active execution source of truth is:

- `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`
- `docs/ARCHITECTURE.md`
- `docs/ARCHITECTURE_CHANGE_PROTOCOL.md`
- `docs/CODEX_AUTO_FAILOVER_ARCHITECTURE.md`
- `docs/MEDIA_PRODUCTION_ARCHITECTURE.md`
- `docs/PRESENTATION_VOICE_ARCHITECTURE.md`
- `docs/BOT_UX.md`
- infrastructure roadmap: Issue #14
- end-state product capability spec: Issue #15
- novel workflow migration: Issue #16
- revenue workflows: Issue #17

For interactive coding provider failover, `CODEX_AUTO_FAILOVER_ARCHITECTURE.md` is the newest Owner-approved contract and supersedes conflicting older wording that treated the Codex-Qwen bridge as historical-only or required explicit manual local selection. The broader local-first plan remains authoritative for product priorities and quota economics.

## Active policy baseline

Approved local-first policy remains in force:

- routine production uses qualified local models and deterministic local tools;
- OpenAI Codex quota is reserved for high-value planning, difficult escalation, engineering implementation when explicitly chosen, and important acceptance/review;
- Codex Desktop is the primary Owner-present interactive coding GUI;
- when a durable Codex coding task encounters verified quota exhaustion/rate-limit/provider unavailability, the target architecture automatically hands the same task/worktree to Local Qwen3.8 instead of waiting for quota recovery;
- Direct Local Qwen remains the canonical unattended/background executor and must still work with Codex Desktop closed;
- provider changes must preserve task identity, worktree, diff/test/review state, and append durable provider-history evidence;
- Gemini is an advisory cloud reviewer behind the privacy/egress gate, never a mutation authority;
- external webpages/docs/search results/model outputs are untrusted data and cannot grant execution authority;
- model/file downloads are managed actions and never imply execution;
- generated media is reviewed locally before any external publish action;
- public Git repositories never receive private persona/training source material automatically;
- material architecture changes follow `ARCHITECTURE_CHANGE_PROTOCOL.md`: propose -> Owner approve -> doc sync -> implementation reads approved HEAD -> qualification -> status sync.

## Active development branch

`feat/local-qwen-owner-raw-v04`

The branch remains unmerged. Branch commits do not imply canonical `/Users/jerson/AI` deployment unless separately qualified and deployed.

## Qualified / validated foundations

- Qwen3.8 MAIN local runtime: QUALIFIED
- Qwen3.6 FAST/FALLBACK local runtime: VALIDATED
- Direct Local Qwen Generic Project executor with deterministic allowlisted tools: QUALIFIED
- Gemini advisory review integration with non-blocking transient-unavailability policy
- Qwen3-TTS Base: QUALIFIED
- Qwen3-TTS VoiceDesign: QUALIFIED
- persistent `zh-male-25-default` and `en-male-25-default` voice profiles: QUALIFIED
- PPTX -> narration -> language/profile routing -> local TTS -> FFmpeg MP4: real local E2E PASS
- Owner-provided script presentation videos: real product runs completed
- task-named `ai_video_product` Git LFS archival: manually proven for two real Solana University video tasks
- OWNER_RAW sandbox implementation/security tests: code complete; runtime qualification still blocked by incomplete GGUF model artifact

## Interactive Codex / Local-Qwen failover status

Owner-approved target workflow:

```text
Codex Desktop GUI
  -> durable coding job/worktree
  -> OpenAI Codex while available/authorized
  -> automatic handoff on verified quota exhaustion/rate-limit/provider unavailability
  -> Local Qwen3.8 through the Codex local-provider/UI adapter
  -> continue the same task
```

Required continuity includes objective, worktree/branch, diff identity, completed tests, unresolved findings, review state, handoff state, approvals, and provider history.

Current implementation status: ARCHITECTURE APPROVED / IMPLEMENTATION PENDING.

Existing pieces that will be reused include read-only Codex quota telemetry, the Codex-Qwen Responses bridge/profile work, Supervisor durable state, Direct Local Qwen, and Gemini advisory review. The automatic controller, same-job provider handoff, Codex Desktop local launcher/status experience, and qualification evidence still need implementation.

The old statement that the Codex-Qwen bridge is only historical compatibility evidence is no longer the target architecture for Owner-present interactive coding. It is being re-scoped as the Codex Desktop local-provider/UI adapter. Direct Local Qwen remains preferred for 7x24 unattended work.

A raw account-wide Codex `usedPercent` delta is not sufficient execution attribution because unrelated Desktop/CLI/background activity may affect account state. Final qualification must prove route/provider identity and controlled local execution, not merely compare quota percentages.

## Download / model state

Model downloads are intentionally PAUSED because current network quality is poor.

Completed local artifacts include:

- Whisper large-v3 MLX
- Qwen3-TTS Base
- Qwen3-TTS VoiceDesign
- FLUX.2 klein 4B bf16

Partial/incomplete artifacts remain preserved for:

- Qwen3 Embedding 8B
- Qwen3 Reranker 8B
- Owner RAW Qwen3.8 Q6_K GGUF
- LongCat video

Do not delete incomplete caches or resume downloads until network conditions are suitable. Download-manager progress percentages for duplicate historical `.incomplete` files are not authoritative; completion markers/model validation remain authoritative.

`llama-server` is installed locally, so the OWNER_RAW executable/runtime dependency is available. RAW remains unqualified because the pinned GGUF artifact is incomplete.

## Media production status

Presentation Video V0.1 is READY on the feature branch and has completed real English production runs.

Media Product Workflow V0.2 is code-complete and locally qualified on the feature branch; it is not merged or deployed.

Approved V0.2 intake/product behavior:

- Telegram remains a compact wizard under the existing media menu;
- after task naming, the Owner chooses `上传材料 / 发送链接 / 上传材料 + 链接 / 直接描述任务`;
- Owner-supplied links are processed through bounded Search/Browser requirement intake; fetched content is untrusted data;
- local Qwen may create durable requirements, production brief, script, scene plan and prompt pack from validated evidence;
- unsupported/missing real Owner facts enter `MISSING_OWNER_FACT`; personal facts are not fabricated;
- execution mode is `自动完成` or `先看文稿`;
- final video is always previewed before publish unless the Owner explicitly chooses local-only output;
- publish approval is bound to the exact output hash;
- approved products publish to canonical `lxxlx2/ai_video_product/<task-slug>/output/final.mp4` using Git LFS;
- remote commit/output verification is required before cleanup;
- verified published duplicate MP4/intermediate media may be removed locally while durable records/source/private persona data are preserved.

Current proven media capabilities:

- Owner-supplied script as durable narration artifact;
- local Qwen-generated narration;
- deterministic Chinese/English language routing;
- persistent qualified voice profiles;
- local TTS and synchronized presentation MP4 generation;
- review-before-publish policy;
- dedicated public product repository `lxxlx2/ai_video_product` with Git LFS.

V0.2 qualification evidence includes the earlier real local qualification
and the final post-integration consolidated media gate: 41 tests passed in
12.22 seconds on 2026-08-28 after Telegram multi-input, execution coordination,
script-review, missing-owner-fact, publisher recovery and cleanup integration.
A real local three-scene run used Qwen3.8, the qualified English persistent
voice, LibreOffice rendering and FFmpeg; it produced a 50.728-second MP4,
bound synthetic harness approval to its exact SHA, published and verified it
in a temporary local Git/LFS remote, performed bounded cleanup and ended in
`ARCHIVED`. That real media E2E evidence was reused rather than rerunning the
expensive generation path because the final gate targeted orchestration,
review, publication and cleanup integration. No real public test push, merge,
deployment, Bot restart or download resume occurred.

PersonaProfile Foundation is the next media milestone after independent review, but the newly approved Codex Desktop failover architecture may be implemented first because it directly prevents Codex quota exhaustion from blocking engineering progress.

## Work that can continue while downloads stay paused

Priority work that needs no new model download:

1. implement and qualify the approved Codex Desktop -> Local Qwen automatic failover workflow;
2. independently review, then explicitly merge/deploy Media Product Workflow V0.2;
3. implement PersonaProfile/private dataset foundation without training a model yet;
4. qualify already-downloaded Whisper locally;
5. qualify already-downloaded FLUX if all required runtime dependencies are already present; do not install/download missing dependencies automatically;
6. implement training-plane schemas/state/evaluation/promote/rollback foundations;
7. continue task/Telegram/novel workflow plumbing that does not require incomplete models;
8. build RAG storage/interfaces/mocks if useful, but do not claim real RAG READY without Embedding/Reranker qualification.

Work that must remain blocked from READY qualification until downloads finish:

- Embedding/Reranker real RAG E2E;
- Owner RAW real GGUF runtime qualification;
- LongCat or other incomplete generative-video backend qualification.

## Documentation note

`CAPABILITY_MATRIX.md` is generated from code and is not manually edited. V0.2 updates its generator/evidence mapping and regenerates the document.

# Current Status

This status file is intentionally concise. The active execution source of truth is:

- `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`
- `docs/ARCHITECTURE.md`
- `docs/ARCHITECTURE_CHANGE_PROTOCOL.md`
- `docs/MEDIA_PRODUCTION_ARCHITECTURE.md`
- `docs/PRESENTATION_VOICE_ARCHITECTURE.md`
- `docs/BOT_UX.md`
- infrastructure roadmap: Issue #14
- end-state product capability spec: Issue #15
- novel workflow migration: Issue #16
- revenue workflows: Issue #17

## Active policy baseline

Approved local-first policy remains in force:

- routine production uses qualified local models and deterministic local tools;
- OpenAI Codex quota is reserved for high-value planning, difficult escalation, engineering implementation when explicitly chosen, and important acceptance/review;
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

Media Product Workflow V0.2 architecture is OWNER-APPROVED and documented. Implementation has not started yet.

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

Next media engineering milestone is `Media Product Workflow V0.2`:

- durable unified MediaJob;
- first-class `--script-file` / `--brief-file` / URL intake contracts;
- requirement/evidence/brief/script/scene/prompt durable artifacts;
- standalone script-to-audio;
- template-based brief/requirements -> presentation-style video fallback without requiring FLUX/LongCat;
- Telegram guided upload/link/brief wizard;
- exact-output preview/review callbacks;
- deterministic canonical product publisher;
- remote verification + post-publish cleanup;
- resumable/interruption-safe state.

PersonaProfile foundation follows as the next milestone.

## Work that can continue while downloads stay paused

Priority work that needs no new model download:

1. implement Media Product Workflow V0.2 from the approved architecture;
2. implement PersonaProfile/private dataset foundation without training a model yet;
3. qualify already-downloaded Whisper locally;
4. qualify already-downloaded FLUX if all required runtime dependencies are already present; do not install/download missing dependencies automatically;
5. implement training-plane schemas/state/evaluation/promote/rollback foundations;
6. continue task/Telegram/novel workflow plumbing that does not require incomplete models;
7. build RAG storage/interfaces/mocks if useful, but do not claim real RAG READY without Embedding/Reranker qualification.

Work that must remain blocked from READY qualification until downloads finish:

- Embedding/Reranker real RAG E2E;
- Owner RAW real GGUF runtime qualification;
- LongCat or other incomplete generative-video backend qualification.

## Documentation note

`CAPABILITY_MATRIX.md` is generated from code and is not manually edited. It currently lags recent presentation/TTS/Direct-Qwen progress; update the generator/evidence mapping in an engineering pass, then regenerate it rather than hand-editing generated output.

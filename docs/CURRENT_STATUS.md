# Current Status

This status file is intentionally concise. The active execution source of truth is:

- `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`
- `docs/ARCHITECTURE.md`
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
- public Git repositories never receive private persona/training source material automatically.

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

Current media architecture supports or has proven:

- Owner-supplied script as durable narration artifact;
- local Qwen-generated narration;
- deterministic Chinese/English language routing;
- persistent qualified voice profiles;
- local TTS and synchronized presentation MP4 generation;
- review-before-publish policy;
- dedicated public product repository `lxxlx2/ai_video_product` with Git LFS.

Next media work is to remove manual glue:

- first-class `--script-file` / `--brief-file` ingestion;
- general `MediaJob` state/orchestrator;
- simple Telegram video wizard;
- Telegram preview and exact-output approval;
- deterministic publisher to `ai_video_product/<task-name>/` only after approval;
- verified post-publish cleanup of duplicate local MP4/intermediate media while preserving durable metadata and unpublished/source/private assets;
- standalone script-to-audio;
- reusable named PersonaProfile foundation and private persona dataset/training plane.

See `MEDIA_PRODUCTION_ARCHITECTURE.md` and `BOT_UX.md`.

## Work that can continue while downloads stay paused

Priority work that needs no new model download:

1. synchronize architecture/status/source-of-truth documents;
2. implement Media M1: script/brief ingestion, MediaJob, publisher and post-publish cleanup;
3. implement the simple Telegram video task wizard and review/publish callbacks;
4. implement PersonaProfile/private dataset foundation without training a model yet;
5. qualify already-downloaded Whisper locally;
6. qualify already-downloaded FLUX if all required runtime dependencies are already present; do not install/download missing dependencies automatically;
7. implement training-plane schemas/state/evaluation/promote/rollback foundations;
8. continue task/Telegram/novel workflow plumbing that does not require incomplete models.

Work that must remain blocked from READY qualification until downloads finish:

- Embedding/Reranker real RAG E2E;
- Owner RAW real GGUF runtime qualification;
- LongCat or other incomplete generative-video backend qualification.

## Documentation note

`CAPABILITY_MATRIX.md` is generated from code and is not manually edited. It currently lags recent presentation/TTS/Direct-Qwen progress; update the generator/evidence mapping in the next engineering pass, then regenerate the file rather than hand-editing generated output.

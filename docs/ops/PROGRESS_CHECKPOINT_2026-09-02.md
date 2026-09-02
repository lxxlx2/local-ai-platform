# Operational Progress Checkpoint — 2026-09-02

Status type: collaboration handoff snapshot. This document is intentionally isolated on an ops branch and MUST NOT be used as the repository SHA for the active G0-B bootstrap epoch.

## Frozen Review Mesh candidates

- G0-A PR #36 branch: `feat/autonomous-review-mesh-g0a-v01`
- G0-A frozen head: `f2d5b7b0152b29da56e33e79583addf13c1ba634`
- G0-B PR #38 branch: `codex/autonomous-review-mesh-g0b-v01`
- G0-B frozen candidate: `b620d93951e468fb2b1f74765aafaa8288401457`
- G0-B parent/base: `f2d5b7b0152b29da56e33e79583addf13c1ba634`
- G0-B tree: `6d9df406d05310f2cadbb4cee53f42fb9a1e6a2f`
- G0-B candidate diff SHA-256: `537cba8506b199ae51e63c840488f1dff7e47476cb35c0f5002845ea2e9c25a9`
- Do not mutate either frozen candidate during the current bootstrap ceremony.

## G0-B bootstrap ceremony

Epoch: `g0b-bootstrap-b620d939-v1`

Current durable state: `BOOTSTRAP_HARNESS_INSPECTED`

Current trusted checkpoint:

- event count: `3`
- journal digest: `6bcf653b355ef7e9f609bfb753cb4fc2d53c6d888b8831087eb8b8e9db141993`
- journal head digest: `bfc44c79033e2ba8296ff6d4e3746abd1dc921fa659bfc6d177d7aaa65b6b05f`
- material pins digest: `e205aeb5a476a1042cefb9d03159a756cb4247328aa13ad18df8e0b7cf4b1082`
- Owner disclosure attestation digest: `852f3cb44e4b112d960d273342abfd053a9e7830e495e77c2cd68dbfe201f49b`

Completed ceremony transitions:

1. `BOOTSTRAP_UNINITIALIZED -> BOOTSTRAP_OWNER_AUTHORIZED`
2. `BOOTSTRAP_OWNER_AUTHORIZED -> BOOTSTRAP_MATERIAL_PINNED`
3. `BOOTSTRAP_MATERIAL_PINNED -> BOOTSTRAP_HARNESS_INSPECTED`

External harness inspection evidence:

- Gemini exact requested model `gemini-3.6-flash`; metadata version `3.6-flash-07-2026`; returned `modelVersion=gemini-3.6-flash`; final bounded retry PASS; zero findings; inspection digest `c519e0a83d54df55c8a561fa346a561feba615093ccd975d2c51330164bda61e`.
- Mistral exact requested/returned model `mistral-medium-3-5`; observed revision `2604`; PASS; zero findings; inspection digest `5d5f0e5a5c6e682552123b796d215b1ea825afd9201b707dfff46e6cdeb06eec`.
- Distinct provider principals: `google-gemini`, `mistral`.
- Distinct foundation lineage classes: `google-gemini3`, `mistral-medium3`.
- Owner confirms both provider paths are within the authorized free/no-PAYG boundary.

Pending ceremony sequence:

1. two full qualification executions against the pinned 15-fixture suite, 30 trials per reviewer, 60 total provider trials
2. deterministic scoring and `BOOTSTRAP_EXECUTIONS_COMPLETE`
3. canonical lineage/qualification seed proposal
4. second exact Owner authorization of bootstrap package and registry snapshot digests
5. atomic ledger genesis and `BOOTSTRAP_COMPLETE`

No merge, deploy, runtime activation, service restart, destructive cleanup, privilege expansion, unapproved paid usage or other protected action is authorized by the bootstrap ceremony.

## Model download result

The configured parallel model download queue finished with manager state `COMPLETED`.

- completed: 8 / 8
- failed: 0
- pending: 0
- active: 0
- quarantine: 0

Every configured model passed the repository's authoritative completion check after cleanup:

- `stt-whisper-large-v3`
- `tts-qwen3-base-bf16`
- `tts-qwen3-voice-design-bf16`
- `image-flux2-klein-4b-bf16`
- `embed-qwen3-8b`
- `rerank-qwen3-8b`
- `video-longcat-q8`
- `raw-qwen38-27b-8bit`

Current approximate model directory sizes after stale-cache cleanup:

- Whisper Large V3: 2.9 GiB
- Qwen3 TTS Base: 4.2 GiB
- Qwen3 TTS VoiceDesign: 4.2 GiB
- Qwen3 Embedding 8B: 14 GiB
- Qwen3 Reranker 8B: 15 GiB
- existing `mlx-community` model area including Qwen3.6: 19 GiB
- FLUX.2 klein 4B: 22 GiB
- existing `qwen38-27b-8bit`: 28 GiB
- new `qwen38-27b-raw-8bit`: 28 GiB
- LongCat Video q8: 31 GiB

Downloaded does not imply qualified or deployed.

## Cleanup completed

Cleanup was performed only against exact audited paths and download caches. Current approximate reclaimed space is about 25.6 GiB total.

Previously reclaimed:

- stale/abandoned model download cache and partial attempts: about 18.7 GiB
- duplicate/manual JMeter runtime and installer material: about 0.9 GiB
- pip/uv/Homebrew download caches: about 2.1 GiB

Additional confirmed cleanup:

- `/Users/jerson/Documents/教学接单`: 3.8 GiB removed completely, including remaining JMeter teaching archives/build output, old editing material and its 2.2 GiB Git object store
- `/Users/jerson/Documents/代下单项目`: about 51 MiB removed
- `/Users/jerson/codex-agentrouter-test`: removed
- post-delete JMeter residue scan under Documents/Desktop/Downloads: no matches
- post-delete old AI-video-material scan under Documents/Desktop/Downloads: no matches

The following remain intentionally protected:

- all 8 official configured model payloads
- `/Users/jerson/AI/models/longcat-video-q8`
- `/Users/jerson/AI/models/qwen38-27b-raw-8bit`
- Python virtual environments
- Java/JDK
- Unity/Android environment
- Review Mesh Owner-private evidence under `/Users/jerson/AI/runtime/review-evidence`
- frozen G0-B worktree `/Users/jerson/local-ai-review-mesh-g0b-v01`
- Git worktrees and current provider credentials
- novel project material such as `归灯纪`; keyword matches like `剧情设计` are not old AI-video residue

Codex sidebar project/conversation entries may remain visible after local files are removed; those application records are separate from filesystem residue.

## Future deletion gate

Do NOT delete `/Users/jerson/AI/models/qwen38-27b-8bit` yet.

It becomes a deletion candidate only after all of these are true:

1. new `/Users/jerson/AI/models/qwen38-27b-raw-8bit` passes its intended role-specific runtime qualification
2. routing and fallback configuration are inspected and prove no live dependency on `qwen38-27b-8bit`
3. no service plist, runtime profile, model registry entry, smoke test, qualification command or recovery path still points to the old directory
4. Owner explicitly authorizes destructive deletion after the read-only dependency audit

Expected reclaim after that future gate: about 28 GiB.

Do not delete `longcat-video-q8` merely because the historical standalone `ai视频` project is no longer needed. It is still a configured platform payload until the platform video capability itself is explicitly retired.

## Project-level execution priority

Immediate priority remains G0 Autonomous Review Mesh bootstrap. PR #31 remains Draft/frozen and should be re-reviewed through the Mesh after bootstrap completion rather than resumed now.

The next collaborator must first read the exact frozen G0-B branch plus the Owner-private current checkpoint, verify durable state `BOOTSTRAP_HARNESS_INSPECTED` and journal digest `6bcf653b355ef7e9f609bfb753cb4fc2d53c6d888b8831087eb8b8e9db141993`, then continue with the two external-lineage qualification executions. Do not reconstruct bootstrap state from chat history and do not write progress commits onto the frozen G0-A/G0-B branches.

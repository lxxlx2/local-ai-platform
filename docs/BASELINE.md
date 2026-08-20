# V1 baseline

## Gateway V0.2 development baseline

The currently running Bot remains V0.1 until an owner-performed restart. V0.2 code adds public/private routing, isolated local development repositories, a secret firewall, bounded context/memory interfaces, file policy, queue/quotas, and remote-provider adapters. Remote PostgreSQL, remote object storage, and a production embedding provider are not configured.

## Chat-output self-acceptance

V0.2 uses a shared safe-plain-text Telegram renderer and a 3,600-character chunker for Owner and Public chat. Default output budget is 1,024 tokens; explicit detailed requests may use 4,096. Responses API completion metadata is retained in application control flow. An incomplete answer is not silently stored or sent as a complete answer. The ignored runtime acceptance report stores only metrics, not answers or credentials.

## Hardware and system

- MacBook Pro, `Mac16,5`; Apple M4 Max (16 CPU cores: 12 performance / 4 efficiency), 40 GPU cores, 48GB unified memory, approximately 1TB internal SSD.
- macOS Tahoe 26.6.2.

## Inference

- oMLX 0.6.3rc1, upstream commit `146d27241e9b01bab08e4768fddda749f6f085fa`; Python 3.13.15.
- Model: `mlx-community/Qwen3.6-35B-A3B-4bit`, revision `38740b8`, Apache-2.0.
- Service: localhost only at `127.0.0.1:8000`; no public endpoint.
- Policy: 28GB guard, single concurrency, paged KV cache disabled, 64 cache blocks, and `chat_template_kwargs: {"enable_thinking": false}`.

## Functional API

`/health`, `/v1/models`, `/v1/chat/completions`, `/v1/responses`, strict JSON, and streaming: PASS. Structured Tool Calling requires independent revalidation before Codex Local production use.

## Context results

| Context | Result | Input | TTFT | Prompt / generation | Retrieval |
| --- | --- | ---: | ---: | --- | --- |
| 8K | PASS | 7,294 | 6.163s | 1,183.55 / 99.44 tok/s | 5/5 |
| 32K | PASS | 30,075 | 24.452s | 1,232.67 / 87.37 tok/s | 10/10; cross 2/2 |
| 64K | Special Long Context | 61,884 | 96.387s | 642.69 / 71.94 tok/s | 16/16; cross 3/4 |

The final 64K cross-context question reached the 1,500-token output cap. Swap delta was 0MiB and thermal state was NORMAL.

## Stability

30-minute mixed workload: 30 requests, 30 successful, 0 API errors, context isolation PASS, JSON PASS, Structured Tool Calling NOT VALIDATED. Swap was 1,046.25MiB at start and 1,038.25MiB at end; thermal NORMAL.

Production context guidance: Chat/X/Sticker/Livestream realtime 8K; Codex pending tool validation, Novel writing/review, and Research 32K; special long context 64K.

## Control-plane position

Telegram Bot is the planned primary Chinese-button control plane. Qwen3.6 is the FAST/default local model for bounded single-request tasks, not a Coding Agent or Codex replacement: real multi-step structured-agent loops failed to progress beyond repeated list/test calls. Deterministic task, approval, permissions, and audit layers retain execution authority.

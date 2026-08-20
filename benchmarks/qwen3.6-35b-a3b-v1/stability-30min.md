# 30-minute production stability — 2026-08-20

Status: **30MIN_STABILITY_PARTIAL**. The loaded Qwen3.6-35B-A3B-4bit oMLX service ran for 1,800.083 seconds without restart, crash, OOM, thermal warning, resource-limit trigger, or API failure. Thirty single-concurrency requests completed: 15 at 8K, 8 at 16K, and 7 at 32K. Context isolation passed for all requests.

Telemetry: system memory used was 24,937.08 / 34,853.47 / 24,711.89MiB (start/peak/end); oMLX RSS 5,151.48 / 5,165.75 / 5,165.75MiB; compressed memory 444.83 / 448.88 / 445.59MiB; and swap 1,046.25 / 1,046.25 / 1,038.25MiB (delta -8.00MiB). Memory-pressure free percentage bottomed at 44%; thermal was NORMAL throughout.

Mean results: 8K TTFT 4.974s and generation 105.50 tok/s; 16K 13.099s and 92.46 tok/s; 32K 41.461s and 89.10 tok/s. Same-context first-ten-minute versus last-ten-minute changes were: 8K TTFT +5.37%, generation -4.58%, prompt -4.84%; 16K TTFT +45.88%, generation -8.62%, prompt -26.73%; 32K TTFT -1.00%, generation -5.95%, prompt +0.56%. These are small-sample workload observations, not a leak signal.

JSON: both strict JSON requests were valid. Tool calling: both forced tool-call exercises returned normal API responses but no verifiable structured tool-call event, so tool calling is **NOT_VALIDATED** in this stability run. Several content tasks reached their intentionally modest 550-token request cap; this is an output-budget observation, not an API failure.

Dynamic production context recommendation: CHAT/X/STICKER/LIVESTREAM_REALTIME 8K; CODEX/NOVEL_WRITING/NOVEL_REVIEW/RESEARCH 32K; 64K only SPECIAL_LONG_CONTEXT_MODE with explicit latency and response-budget planning. Do not start Codex Local until tool calling is independently revalidated and the user authorizes the next phase.

# Qwen3.6-35B-A3B V1 benchmark

Pending model installation. Required stages: idle baseline, loaded idle, short generation, meaningful 8K context, 32K context, conditional 64K context, and 30-minute mixed-workload stability. Capture TTFT, prompt/generation throughput, elapsed time, RAM, swap, pressure, CPU, thermal, crash/leak and degradation observations.

## 8K context — 2026-08-20

Status: **8K_PASS** (REAL_WORLD_WARM_STATE). A local, non-sensitive deployment reference corpus with five distributed fact markers was used. The model received 7,294 input tokens and generated a complete 548-token Chinese structured summary in 11.674 seconds. It returned all five fact markers exactly (`FACT_RECALL=5/5`), with no API error, visible reasoning leakage, garbling, repetition, or truncation.

Performance: TTFT 6.163s; prompt processing estimate 1,183.55 tok/s; generation 99.44 tok/s. The response ended naturally before the desired 1,000–1,500-token range, so output-length adherence is an observed limitation rather than a crash.

Memory: latest pre-test idle swap was 1,134.25MiB; post-test swap was 1,102.25MiB (delta -32.00MiB). Post-test memory-pressure available percentage was 90%, compressed memory 679MiB, CPU 87.42% idle, and thermal reported no warning. Peak in-request telemetry was unavailable because the sampler did not parse percentage-form pressure values; the test was not repeated. No additional swapout occurred during the test window.

Recommendation: 32K may be tested only with explicit next-step authorization. Retain `enable_thinking=false`, single concurrency, the 28GB guard, and cache-disabled configuration.

## 32K context — 2026-08-20

Status: **32K_PASS** (REAL_WORLD_WARM_STATE). The test used public Chinese Python tutorial material and code-oriented technical documentation, with ten distinct, non-sensitive fact markers placed across the context at approximately 5% through 95%. The model received 30,075 input tokens and produced a complete 1,330-token Chinese response. It returned every fact exactly (`FACT_RECALL=10/10`) and correctly answered both cross-context relationships (`CROSS_CONTEXT_REASONING=2/2`). There was no API error, crash, garbling, visible reasoning leakage, repetition, or output truncation.

Performance: TTFT 24.452s; prompt processing 1,232.67 tok/s; generation 87.37 tok/s; total elapsed time 39.672s. Relative to the 8K baseline, prefill speed changed **+4.15%**, generation speed **-12.14%**, and TTFT was **3.97x**. The differing prefill result is recorded as an observation rather than an assumption of a general performance improvement.

Memory: the repaired lightweight sampler collected 39 valid one-second samples without parse errors. Its metric definitions are in `memory-sampler-metrics.md`; process RSS is explicitly not represented as full Apple-Silicon unified-memory allocation. oMLX RSS was 5,125.55MiB at start and peaked at 5,141.84MiB. System memory used peaked at 36,417.45MiB, compressed memory at 451.11MiB, and memory-pressure free percentage bottomed at 40%. Swap was 1,062.25MiB at start, peak, and end (delta 0.00MiB); this is 32.00MiB higher than the 8K test delta, but showed no runaway growth. Peak oMLX CPU was 98.8%; `pmset` reported no thermal or performance warning.

Recommendation: a 64K test may be considered only with explicit authorization. Retain the unchanged model, 28GB guard, single concurrency, cache-disabled configuration, and `enable_thinking=false`; interpret the 40% pressure minimum and 3.97x TTFT increase as the principal 32K headroom observations.

## 64K context — 2026-08-20

Status: **64K_PARTIAL** / **SPECIAL_LONG_CONTEXT_MODE**. A public, non-sensitive Chinese technical corpus spanning Python tutorial, standard-library, code, concurrency, testing, and structured-documentation material was used. Sixteen distinct fact markers were distributed from approximately 5% through 95% of the context. The model received 61,884 input tokens and generated 1,500 tokens. It recovered all facts exactly (`FACT_RECALL=16/16`: early 8/8, middle 2/2, late 6/6), including the deliberately distributed middle facts. It completed three of four cross-context answers exactly (`CROSS_CONTEXT_REASONING=3/4`). The fourth began but was cut off when the requested 1,500-token ceiling was reached, so output is not considered complete.

Performance: TTFT 96.387s; prompt processing 642.69 tok/s; generation 71.94 tok/s; total elapsed time 117.234s. Compared with the 32K baseline, TTFT was **3.94x**, prefill speed changed **-47.86%**, and generation speed changed **-17.66%**.

Memory: 111 valid one-second sampler observations, with no sampling errors. System memory used was 24,905.17MiB at start, peaked at 37,458.28MiB, and ended at 35,820.22MiB. oMLX RSS was 5,144.09MiB at start, peaked at 5,158.73MiB, and ended at 5,151.50MiB; as documented in the sampler definition, this is not a complete Apple-Silicon unified-memory allocation. Compressed memory was 451.34/451.45/451.30MiB (start/peak/end). Swap was 1,046.25MiB at start, peak, and end (delta 0.00MiB). Memory-pressure free percentage was 91% at start, bottomed at 38%, and ended at 41%. Peak oMLX CPU was 85.5%; `pmset` reported no thermal or performance warning. No resource-limit trigger, API error, server crash, OOM, or swap runaway occurred.

Recommendation: keep **32K as the normal production ceiling**. Treat 64K as special long-context mode only after a caller can budget roughly 96 seconds to first token and accepts the response-length planning limitation observed here. Before any future 64K production use, reserve output budget more deliberately (or request a shorter structured answer), but do not change the tested configuration for comparison purposes.

# Functional baseline — REAL_WORLD_WARM_STATE

Captured: 2026-08-20 17:29 +0700.

- Engine: oMLX 0.6.3rc1.
- Model: `mlx-community/Qwen3.6-35B-A3B-4bit`, revision `38740b8`.
- Model path: `~/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit`.
- Foreground launch: `omlx serve --model-dir ~/AI/models --host 127.0.0.1 --port 8000 --max-concurrent-requests 1 --memory-guard-gb 28 --no-cache --initial-cache-blocks 64`.
- Binding: `127.0.0.1:8000` only.
- Memory policy: custom 28GB guard; one concurrent request; paged KV cache disabled; 64 initial cache blocks.
- Chat template request option: `chat_template_kwargs: {"enable_thinking": false}`.
- API baseline: health, models, chat completions, responses, strict JSON, tool calling and streaming passed.
- Idle state at capture: 42GB physical used, 5.3GB unused; 690MB compressor; 1.14GB swap used; memory-pressure available percentage 90%; CPU 87.5% idle; no thermal warning; 723GiB SSD free.

This is a warm, real-world desktop baseline, not a clean-boot performance result.

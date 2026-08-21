# Training lifecycle

V0.1 implements the preparation and safety boundary, not production training.

The MLX provider can safely probe Apple Silicon, physical memory, free disk, local Qwen3.6 model metadata/shards/tokenizer, a dedicated training venv, and `mlx`/`mlx_lm` module availability. If the training venv is absent, it may read the existing oMLX interpreter only to detect modules; it never installs into or mutates that production venv.

Pilot configuration is deterministic and conservative: batch size 1, LoRA rank at most 16, at most 3 iterations, fixed seed, bounded modules/layers, dataset and adapter paths confined to `runtime/learning/`, and `production_training_enabled=false`. The base model path is read-only by contract. `train()` returns `DISABLED` and cannot launch a job.

A runnable pilot requires a separate `runtime/training-venv`, an installed CLI whose exact arguments have been validated, an approved immutable dataset, isolated adapter output, healthy resources, and explicit authorization. None of those conditions permits automatic oMLX stop/restart or model switching.

The current Mac probe found adequate hardware and MLX libraries through a read-only oMLX-venv probe, but no dedicated training venv. Therefore micro LoRA is intentionally skipped and runnable training remains NOT READY.

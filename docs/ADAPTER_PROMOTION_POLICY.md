# Adapter promotion and rollback policy

Adapters are registered only when their artifact is a real non-symlink file under `runtime/learning/adapters/` and its SHA-256 matches. Metadata includes base model/revision, dataset-manifest hash, training-config hash, namespace, artifact path/hash, eval summary, state, and rollback target.

States are `TRAINING`, `CANDIDATE`, `EVAL_FAILED`, `APPROVED`, `ACTIVE`, `ROLLED_BACK`, and `ARCHIVED`. Only a candidate or previously eval-failed adapter can be promoted, and only through an exact bound PASS eval. There is at most one ACTIVE adapter per namespace. Promotion demotes the previous active version to a rollback target; rollback restores that target.

Registry promotion does not reconfigure oMLX, switch the current model, or modify base weights. Runtime activation remains a future, separately authorized deployment step. The base Qwen3.6 model is always retained.

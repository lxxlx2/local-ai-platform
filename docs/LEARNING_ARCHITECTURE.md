# Private Learning Engine V0.1

The learning engine is an Owner-private, offline control plane for turning explicit feedback into versioned training/evaluation assets. It does not change the currently loaded model and does not share authority with the chat model.

```text
Owner feedback/manual import
  -> Secret Firewall
  -> privacy redaction
  -> bounded content-addressed store + metadata DB
  -> approved candidate / preference pair
  -> deterministic namespace dataset
  -> immutable manifest + Golden Holdout
  -> base-vs-adapter evaluation
  -> explicit adapter registry promotion / rollback
```

Memory and Training are separate systems. Conversation memory supports retrieval; it is never implicitly treated as training data. Public-plane messages are excluded by default and cannot create candidates. Model-generated content is marked synthetic and cannot become training ground truth without Owner approval, deterministic validation, or a verified business outcome.

Namespaces are isolated: `personal-general`, `x-content`, `novel-editor`, `livestream-content`, `stickers-content`, and `coding-assistant`. Dataset records carry hashes and provenance metadata; raw content remains under `runtime/learning/`, which is Git-ignored.

The SQLite schema contains candidates, preference pairs, immutable datasets/examples/manifests, eval runs/results, adapters, business outcomes, and metadata-only events. Content is stored separately through a bounded content-addressed interface. The S3-compatible interface exists only as a disabled skeleton in V0.1.

Training orchestration is represented by typed job specifications so a future Supervisor can schedule dataset, training, evaluation, and promotion jobs without importing runtime implementation details. V0.1 exposes no automatic training, model switch, or oMLX restart path.

# Local Producer V0.1

Purpose: keep coding work moving when external Codex quota is exhausted, using the already-qualified local Qwen3.8 model as a bounded patch proposer.

## Security model

Qwen3.8 is treated as an untrusted code proposer, not an autonomous shell agent.

It may receive only bounded, secret-scanned repository context and a task prompt. It returns one strict JSON object containing a unified diff. Deterministic Python/Git policy validates the diff. The model receives no shell, Git credential, service-management, arbitrary filesystem, commit, push, merge, deployment, secret, or network tool.

V0.1 refuses:

- `main` branch mutation
- dirty target worktrees
- path traversal or non-canonical patch paths
- runtime/models/cache/logs/secrets/.git access
- binary patches
- rename/copy/delete/mode-only patches
- ignored-file writes
- mismatched `diff --git`, `---`, or `+++` paths
- more than 8 changed files
- patches over 256 KiB
- task prompts over 20 KiB
- assembled planning prompts over 48 KiB
- model responses that are not strict JSON

The fixed flow is:

`task + bounded safe context -> Qwen3.8 -> strict JSON patch -> path policy -> git apply --check -> optional git apply`

No commit or push occurs.

## Recommended isolated pilot

Keep the target feature branch in `/Users/jerson/AI` and check out the Local Producer code in a separate small Git worktree. This avoids cherry-picking Local Producer implementation into the feature branch being repaired.

Example:

```bash
cd /Users/jerson/AI
git fetch origin
git worktree add /Users/jerson/AI-local-producer origin/feat/local-producer-v01
```

The Qwen3.8 localhost sidecar must already be healthy on `127.0.0.1:8001`. Do not start a second heavy model alongside another resident heavy model. Until the separate heavy-process R4 review is closed, start/stop decisions remain a manual operational gate.

Dry-run proposal against `/Users/jerson/AI`:

```bash
/Users/jerson/AI/runtime/control-plane-venv/bin/python \
  /Users/jerson/AI-local-producer/control-plane/scripts/local-producer.py \
  --repo-root /Users/jerson/AI \
  --task-file /absolute/path/to/task.txt
```

This validates and prints a proposed patch without changing the target worktree.

Apply only after deterministic patch validation:

```bash
/Users/jerson/AI/runtime/control-plane-venv/bin/python \
  /Users/jerson/AI-local-producer/control-plane/scripts/local-producer.py \
  --repo-root /Users/jerson/AI \
  --task-file /absolute/path/to/task.txt \
  --apply
```

Context can be explicitly bounded with repeated `--read` arguments. If omitted, V0.1 discovers a small set from repo paths and task keywords and extracts bounded relevant excerpts from large files.

After application, the existing Supervisor validation/security/review stages remain authoritative. Local Producer V0.1 does not claim tests passed and does not self-certify review.

## Supervisor adapter

`local_producer_runners()` is an opt-in runner set. The normal Supervisor daemon still uses its existing default runner configuration, so merely adding this code does not turn on local mutation.

The opt-in pipeline is:

1. durable Producer work unit
2. Local Qwen3.8 strict patch proposal
3. deterministic patch validation/application
4. pytest validation stage
5. self-acceptance
6. independent Review
7. bounded Revision via the same Local Producer when required
8. security regression
9. read-only Git Gate

Long-term provider priority can become:

1. external Codex when available and authorized
2. Local Qwen3.8 Patch Producer
3. Local Qwen3.6 only after a separate simple-coding qualification
4. WAIT_FOR_HUMAN

Provider fallback must preserve the durable work-unit, validation, independent review, revision, security, and Git Gate contracts.

# Local Producer V0.1

Purpose: keep coding work moving when external Codex quota is exhausted, using the already-qualified local Qwen3.8 model as a bounded patch proposer.

## Security model

Qwen3.8 is treated as an untrusted code proposer, not an autonomous shell agent.

It may receive only bounded, secret-scanned repository context and a task prompt. It returns one strict JSON object containing a unified diff. Deterministic Python/Git policy then validates the diff. The model receives no shell, Git credential, service-management, arbitrary filesystem, commit, push, merge, deployment, secret, or network tool.

V0.1 refuses:

- `main` branch mutation
- dirty worktrees
- path traversal
- runtime/models/cache/logs/secrets/.git access
- binary patches
- rename/copy/delete patches
- ignored-file writes
- more than 8 changed files
- patches over 256 KiB
- task prompts over 128 KiB
- model responses that are not strict JSON

The fixed validation flow is:

`task + safe context -> Qwen3.8 -> strict JSON patch -> path policy -> git apply --check -> optional git apply`

No commit or push occurs.

## Pilot usage

The Qwen3.8 localhost sidecar must already be healthy on `127.0.0.1:8001`. Do not start a second heavy model alongside another resident heavy model.

From `/Users/jerson/AI` on a clean feature branch:

```bash
/Users/jerson/AI/runtime/control-plane-venv/bin/python \
  control-plane/scripts/local-producer.py \
  --task-file /absolute/path/to/task.txt
```

That validates and prints a proposed patch without changing the worktree.

To apply only after deterministic patch validation:

```bash
/Users/jerson/AI/runtime/control-plane-venv/bin/python \
  control-plane/scripts/local-producer.py \
  --task-file /absolute/path/to/task.txt \
  --apply
```

Context can be explicitly bounded with repeated `--read` arguments. If omitted, V0.1 discovers a small set from repo paths and task keywords.

After application, the existing Supervisor validation/security/review stages remain authoritative. Local Producer V0.1 does not claim tests passed and does not self-certify review.

## Planned Supervisor integration

After this pilot is independently reviewed, Producer provider priority can become:

1. external Codex when available and authorized
2. Local Qwen3.8 Patch Producer
3. Local Qwen3.6 only for explicitly qualified simple/FAST coding tasks
4. WAIT_FOR_HUMAN

Provider fallback must preserve the existing durable work-unit, validation, independent review, revision, security, and Git Gate contracts.

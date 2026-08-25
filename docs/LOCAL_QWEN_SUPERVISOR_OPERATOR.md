# Local Qwen Supervisor Operator Flow

Status: FEATURE BRANCH / OPERATOR QUALIFICATION PENDING

Branch: `feat/codex-qwen-supervisor-v02`

This entry point turns the qualified Local Qwen Supervisor path into an owner-operated workflow. It is intentionally local, serial, feature-worktree-only, and human-gated at Review.

## Invariants

- The workspace must be the explicit root of a non-main Git feature worktree.
- The Qwen Responses bridge must already be running and healthy on `127.0.0.1:8010` before a Producer/Revision execution.
- Qwen3.8 remains on the existing sidecar at `127.0.0.1:8001`.
- Local Qwen execution is enabled only inside `submit` or `continue` for that invocation.
- `status`, `review-show`, `review-pass`, and `review-fail` do not activate the coding model.
- The operator does not grant commit, push, merge, deploy, service-control, credential, or network authority.
- The default workflow stops at `REVIEW_RESULT_PENDING` and requires an explicit review submission.
- Durable execution provenance and mutation fences remain authoritative.

## Launcher

Use the feature worktree copy of:

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh ...
```

The launcher uses the existing private control-plane Python environment and the current feature worktree source tree.

## 1. Submit a task

Put the task in a private UTF-8 file. Avoid passing task text directly on the command line because shell history is not an appropriate private prompt store.

```bash
cat > /tmp/local-qwen-task.txt <<'EOF'
Inspect the requested bounded implementation, reproduce the bug, fix implementation code only, and run the relevant tests. Do not commit, push, merge, deploy, access credentials, control services, or use network access.
EOF

zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  submit \
  --title "bounded fix" \
  --prompt-file /tmp/local-qwen-task.txt
```

`submit` creates a durable job/work unit and runs autonomously until one of these boundaries:

- `REVIEW / WAITING / REVIEW_RESULT_PENDING`
- `BLOCKED`
- `FAILED`
- `CANCELED`
- `COMPLETED`

A normal successful Producer path should stop at Review and return the safe job identifiers. Raw task content is not emitted by status output.

## 2. Check status

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  status --job <job-id>
```

The output contains safe state only: job id, title, status, stage, review round, resume state, bounded error state, workspace, and review identifiers when available.

## 3. Materialize the immutable review patch

Prefer a private output file:

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  review-show --job <job-id> \
  --output /Users/jerson/AI/runtime/supervisor-local-qwen/review.patch
```

The patch is reconstructed from the exact durable candidate identity. If the worktree changed after the review unit was created, reconstruction fails closed as stale.

## 4A. Approve the exact review candidate

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  review-pass --job <job-id>
```

This only submits the durable PASS. It does not continue execution automatically.

Then:

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  continue --job <job-id>
```

A PASS continues through deterministic Security and Git Gate. Git Gate remains read-only and performs no commit/merge/push.

## 4B. Reject with findings

Create a JSON findings file:

```json
{
  "findings": [
    {
      "scope": "FILE",
      "severity": "HIGH",
      "file": "control-plane/src/example.py",
      "evidence": "The boundary condition is still incorrect.",
      "recommended_fix": "Correct the condition and rerun the focused tests."
    }
  ]
}
```

Submit it:

```bash
zsh control-plane/scripts/local-qwen-supervisor.sh \
  --workspace /absolute/path/to/feature-worktree \
  review-fail --job <job-id> --findings-file /path/to/findings.json
```

Then run `continue`. The Supervisor consumes the exact durable review result, enters `REVISION`, creates a revision work unit bound to the durable findings, and lets Local Qwen drive Codex again. After revision validation it stops at the next Review boundary.

## Durable state

The default operator database is owner-private under:

```text
/Users/jerson/AI/runtime/supervisor-local-qwen/operator/<workspace-branch-hash>/supervisor.db
```

Task prompts and review payloads remain in the repository's private content store adjacent to the database. Operator status output never dumps the raw private task prompt.

## Current scope

This V2 operator is intentionally bound to the `local-ai-platform` repository layout and its approved `control-plane` / `docs` roots. General arbitrary-repository coding is a later expansion and requires a separate repository-policy design rather than weakening the current path boundary.

Production activation, daemonization, Telegram task submission, automatic cloud/local failover, automatic commit/push/merge/deploy, and general-repository access remain out of scope for this operator gate.

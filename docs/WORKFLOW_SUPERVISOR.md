# Workflow Supervisor V0.1

Status: **EXPERIMENTAL / REVIEW_PENDING** on `feat/workflow-supervisor-v01`. It is not deployed and must not be merged to `main` before a fresh independent review.

## Why it exists

`HOST_TURN_AUTO_RESUME=UNRELIABLE`. Prompt instructions cannot guarantee that a Codex host turn survives detached review or other asynchronous lifecycles. The Workflow Supervisor moves continuation, recovery, retry limits, and review-result consumption into a local durable program.

## Deterministic lifecycle

```text
INTAKE → PRODUCER → VALIDATION → SELF_ACCEPTANCE → REVIEW
                                                   ├─ FAIL → REVISION → VALIDATION
                                                   └─ PASS → SECURITY → GIT_GATE → DONE
```

The language model never chooses the next state. Each stage has a bounded attempt count, every review has a bounded round count, and a global transition ceiling prevents loops. V0.1 processes one job at a time (`MAX_ACTIVE_JOBS=1`) under a leased SQLite singleton lock. Every durable workflow job is mutation-capable and counts toward `MAX_MUTATING_JOBS_IN_SYSTEM=1`; `mutation_capable=false` is rejected rather than treated as an isolation bypass. Read-only status, health, and capability probes are direct service operations, not workflow jobs. This is the V0.1 minimal-isolation policy; dedicated per-job worktrees are not claimed.

## Durable private storage

Runtime state lives in `/Users/jerson/AI/runtime/supervisor/supervisor.db` and is excluded from Git. The database contains jobs, stage runs, external-execution bindings, global mutation fences, review work units/results, audit events, artifact metadata, and locks. WAL mode, busy timeout, idempotency keys, duplicate-event keys, bounded summaries, 5,000 events per job, 500 retained terminal jobs, metadata-only artifacts, and rotating 1 MB logs limit local growth. Prompts and sensitive metadata are persisted only as hashes or redacted values.

This database belongs exclusively to the Owner private plane. Public identities are never allowed to list, view, create, or control Supervisor jobs.

## Crash recovery

On startup the consumer distinguishes a failed/interrupted run from a durable `PASS` that completed before its lifecycle transition was recorded. Read-only/deterministic completed runs can finalize that missing transition without re-running the stage. A completed `PRODUCER` or `REVISION` run can do so only when a Supervisor-owned execution record binds the exact job, work unit, stage, stage run, provider, and execution ID and has status `COMPLETED_CONFIRMED`. Runner-supplied metrics are telemetry only and never authorize recovery.

Owner cancellation is a durable cross-process request, not a call against the control process's runner object. The control surface records `CANCEL_REQUESTED`, requester, timestamp, and the exact target execution ID. The daemon-owned `LeaseKeepingRunner` polls that binding and only its live inner provider receives the exact cancellation. Duplicate requests are idempotent and survive control-process restart. Unsupported, failed, incorrectly bound, or unpersisted cancellation creates a durable global mutation fence and blocks the job for reconciliation. A request that wins before execution start prevents provider start and completes as canceled. A provider `PASS` received after cancellation can never become a clean canceled result; it is fenced as uncertain. In addition, every execution left in `STARTED`, `CANCELLATION_PENDING`, or `UNKNOWN` is itself an implicit global mutation fence; startup materializes an idempotent `EXTERNAL_EXECUTION_UNCERTAIN` fence for operator visibility. That fence survives daemon restart and blocks every new `PRODUCER` or `REVISION` stage until explicit manual reconciliation resolves both the fence and the unresolved execution record; restart never clears it. Completion and cancellation use serialized conditional updates, so a confirmed execution can never simultaneously be canceled. Recovery and fence events are idempotent.

After stopping the Supervisor consumer, an Owner can clear a confirmed external-execution fence with the explicit `reconcile-fence <job_id> <fence_name> --owner-id ... --note ...` control command. The repository requires the job to be exactly `BLOCKED/BLOCKED_REQUIRES_RECONCILIATION`, verifies the Owner, job, active fence, and manual-reconciliation binding, and refuses reconciliation while a live singleton lease exists (`SUPERVISOR_CONSUMER_STILL_ACTIVE`). The no-live-lock check, execution reconciliation, fence clear, and terminal job transition share one immediate SQLite transaction, eliminating an online guard-clear window. The note is Secret-Firewall checked; only its SHA-256 digest is stored. Status keeps the blocked job visible. Health reports `BLOCKED_RECONCILIATION_REQUIRED` for a durable fence and `RECOVERY_REQUIRED` for an unresolved execution without a matching live consumer instead of claiming healthy operation; a matching `RUNNING` job/execution with a live lease remains healthy.

A `PASS` execution is confirmed only after a bounded post-execution `CandidateIdentity` snapshot succeeds. The execution stores the candidate-state, tree, and diff hashes. Completed-not-transitioned recovery recomputes those values and refuses recovery if the worktree changed, the snapshot fails, cancellation was requested, or any unresolved mutation guard exists.

If a crash lands after the job transition but before a review result is marked consumed, startup reconciliation consumes it only when the exact review round, result hash, work unit state, stage-run outcome, job round, and post-review stage agree. Stale or conflicting results remain unconsumed.

This is a minimal completed-not-transitioned recovery path. It does not yet replace the Round 2 compatibility layer with one fully atomic stage-finalization transaction. Git Gate remains read-only and never commits, pushes, or merges.

## Candidate and review invariants

- Job creation captures a typed immutable baseline commit directly from Git. Generic metadata cannot supply or change it, and review creation rejects a missing, invalid, non-commit, or non-ancestor baseline.
- Each review work unit binds to an immutable `CandidateIdentity`: reference type, commit/tree identity, trusted base commit, deterministic candidate-diff hash, capture timestamp, the exact changed/deleted path manifest, and explicit old→new rename/copy provenance. Both sides of a rename/copy are review-scoped, so findings against the trusted old path remain attributable. The timestamp participates in durable work-unit integrity while content equality intentionally ignores capture time.
- Terminal review results are accepted only for the current `REVIEW` lifecycle and exact review round. A future, stale, or transplanted result cannot advance another candidate or round.
- Review findings have an integrity-bound `FILE` or `WORKFLOW` scope. `FILE` findings still require an exact immutable candidate path. `WORKFLOW` findings require an empty file field and can report task-level failures such as no producer effect or missing required artifacts without opening an arbitrary-path bypass. Legacy persisted findings default to `FILE`.
- Each review round binds an Owner-private `TaskObjective` artifact derived from the immutable original Producer work unit (with a bounded legacy intake fallback). It carries the goal, acceptance criteria, constraints, expected artifacts, and source work-unit identity. The raw objective is stored only in the private runtime content store; its content and manifest hashes are included in the reviewer work-unit spec hash. Revision prompts cannot replace it, restart reconstruction verifies it, and tampering fails closed.
- Every reviewer work unit stores a bounded, text-only, Secret-Firewall-scanned patch artifact in the Owner-private runtime content store. Its base commit, candidate identity digest, patch digest, changed/deleted/renamed/copied paths, and current safe-file manifest are integrity-bound to the work unit. A clean candidate uses the canonical `LOCAL_AI_SUPERVISOR_NO_CANDIDATE_CHANGES` patch rather than throwing. Modified, deleted, renamed, copied, and safe untracked content can be reconstructed after restart; stale candidates, secrets, binaries, and oversized patches fail closed. Review preparation errors are converted to a bounded `REVIEW_PREPARATION_BLOCKED` state and cannot terminate the daemon loop. Reviewers receive no unrestricted Git shell.
- Producer and reviewer immutable read manifests use one `RepoAccessPolicy`. Allowed read roots are limited to `control-plane/` and `docs/`; blanket repository access and runtime, secret, cache, model, database, log, credential, symlink, traversal, and path-escape access are denied. Review and Revision manifests cover every non-deleted candidate file, including safe untracked files named by the immutable candidate identity. Every supplied manifest entry is independently re-read and Secret-Firewall scanned. Any extra entry must be present in the bound `CandidateIdentity`; a secret, binary, oversized, symlink, ignored, denied, unrelated, or unbound file fails closed and is never silently omitted. File reads must use the persisted content hash and size, so a permitted parent directory grants no ambient traversal.
- Producer writes use a distinct `RepoWritePolicy`. It permits tracked files and non-ignored new source, test, and documentation files under `control-plane/src/`, `control-plane/tests/`, and `docs/`, while denying ignored new targets, runtime, secrets, credentials, traversal, symlinks, and unsupported file types. A tracked file remains owned even if a later ignore rule matches it. Completion repeats the ignored-file ownership probe; a bypass is fenced as `UNTRACKED_IGNORED_MUTATION_UNACCOUNTED` and cannot be confirmed. An immutable read manifest is not incorrectly reused as a declaration that no safe new file may be created.
- Job creation requires the shared worktree to be completely clean, including tracked and untracked files, and stores the initial candidate-state hash. It separately enumerates ignored pre-existing files inside bounded producer write roots; any ignored file whose type the producer could write makes the worktree unownable without persisting its name or content. Mutating-stage entry repeats that fail-closed ownership check. The Supervisor never stashes, resets, restores, cleans, deletes, or otherwise takes ownership of pre-existing user changes. The first Producer stage rechecks cleanliness and verifies that HEAD/worktree state still belongs to that job before mutation begins. Git snapshots and other non-SQL probes run before the SQLite write transaction; any transactional exception rolls back.
- `job_id` idempotency is bound to a canonical request hash covering Owner, title, scope, risk, creator, attempt/review ceilings, sanitized metadata, and the mutation invariant. Exact replay returns the existing job; any immutable variation raises `IDEMPOTENCY_CONFLICT`. Legacy rows are deterministically backfilled from durable fields.
- Findings must resolve to an allowed path in the bound candidate manifest. A deleted path is valid only when the immutable identity explicitly records it as deleted.
- Job metadata can change only through `update_job_metadata(mapping)`. It preserves existing keys, sanitizes sensitive nested fields, enforces JSON-compatible values and a canonical size bound, and prevents raw `metadata_json` replacement.

## Runners and security

- `LocalValidationRunner` accepts only argv-form `/Users/jerson/AI/runtime/control-plane-venv/bin/python -m pytest`; it uses `shell=False`, a bounded timeout, sanitized environment, and bounded output.
- `SecurityRunner` reuses the existing Secret Firewall, checks both tracked-runtime policy and changed/untracked candidate files, and runs the existing Public/Private isolation regression files through the same allowlisted pytest executor. A failure cannot advance to Git Gate.
- `GitGateRunner` is read-only, rejects `main`, and records that independent review remains pending.
- `CodexTaskSpec` fixes `repo_root` at `/Users/jerson/AI`, validates allowed paths, binds a safe tracked-file manifest, hashes prompts, and applies the Secret Firewall.
- `RealCodexRunner` is an interface boundary only. The local version/help probe reports the official non-interactive and App Server surface as available, but safe existing-auth task execution and enforcement of the producer write policy by an actual agent process are not proven. Therefore `REAL_CODEX_RUNNER=PARTIAL`, auth reuse is `PARTIAL`, and real nested Codex execution remains blocked pending independent review and an explicit activation decision.

The capability probe runs only `codex --version` and `codex --help`. It does not inspect auth files, print credentials, change `~/.codex`, request an API key, or start a task.

## Operations

The scripts use an exact PID file and verify that the process command contains `local_ai_control.supervisor.app daemon`. They never use `pkill`, `killall`, or broad process matching.

```text
/Users/jerson/AI/control-plane/scripts/start-supervisor.sh
/Users/jerson/AI/control-plane/scripts/status-supervisor.sh
/Users/jerson/AI/control-plane/scripts/stop-supervisor.sh
```

Status reports RUNNING/STOPPED, exact PID, active/queued counts, current stage, DB reachability, lock state, last completion, and last error. V0.1 intentionally does not install launchd.

## Safe demo

`SUPERVISOR_DEMO` uses a mock producer, real local pytest validation, deterministic self-acceptance, Security Runner, and read-only Git Gate. On a real clean worktree the mock producer creates no candidate changes, so review receives the canonical empty patch and deterministically passes. A bounded fixture candidate can still exercise the synthetic fail-once → mock revision → validation → review-pass loop. Review preparation failures become a durable blocked state instead of escaping the consumer. The demo neither modifies a business repository nor commits/pushes.

## Telegram surface

The Owner private-task submenu includes an “自动工作流” page. It can show status, create only the safe demo, and pause/resume/cancel/retry existing Owner jobs. It does not accept natural-language-to-Codex execution. Public identities are denied server-side. These changes are not active until a later reviewed deployment and Bot restart.

## Known limitations

- No real Codex work unit is launched in V0.1.
- Existing ChatGPT/Codex auth reuse is not fully confirmed.
- No general side-effect reconciliation or commit/push runner is enabled.
- The process is a simple daemon, not a launchd service.
- Telegram integration is code-complete but not deployed in this phase.
- Independent review is pending.

Next required action: fresh read-only independent review after Codex quota refresh, then an explicit merge/deploy decision.

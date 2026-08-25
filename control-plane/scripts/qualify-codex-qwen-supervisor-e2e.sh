#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
FEATURE_ROOT=${1:-${CONTROL_PLANE_ROOT:h}}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
RUNTIME_ROOT="$LOCAL_AI_ROOT/runtime/supervisor-local-qwen"
STAMP=$(date +%Y%m%d-%H%M%S)
QUAL_ROOT="$RUNTIME_ROOT/e2e-$STAMP-$$"
SCRATCH="$QUAL_ROOT/scratch-repo"
DB="$QUAL_ROOT/supervisor.db"
BRANCH="feat/qwen-supervisor-e2e-$STAMP-$$"

fail() {
  echo "E2E_QUALIFICATION_FAIL $1" >&2
  exit 1
}

[[ -x "$PYTHON" ]] || fail "control-plane Python missing"
for command in git curl lsof shasum; do
  command -v "$command" >/dev/null 2>&1 || fail "$command missing"
done

[[ -z "$(git -C "$FEATURE_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "source feature worktree is not clean"
SOURCE_BRANCH=$(git -C "$FEATURE_ROOT" branch --show-current)
[[ "$SOURCE_BRANCH" == "feat/codex-qwen-supervisor-v02" ]] || \
  fail "source branch must be feat/codex-qwen-supervisor-v02"
SOURCE_HEAD=$(git -C "$FEATURE_ROOT" rev-parse HEAD)

BRIDGE_HEALTH=$(curl --max-time 5 -sf http://127.0.0.1:8010/health) || fail "V1 bridge unavailable"
printf '%s' "$BRIDGE_HEALTH" | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); assert p.get("status")=="healthy" and p.get("backend")=="mlx-community/Qwen3.8-27B-8bit" and p.get("tool")=="exec_command"' || \
  fail "V1 bridge identity mismatch"
curl --max-time 5 -sf http://127.0.0.1:8001/health >/dev/null || fail "Qwen3.8 unavailable"

QWEN_PID_BEFORE=$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | sort -u)
BRIDGE_PID_BEFORE=$(lsof -nP -iTCP:8010 -sTCP:LISTEN -t 2>/dev/null | sort -u)
[[ -n "$QWEN_PID_BEFORE" && "$QWEN_PID_BEFORE" != *$'\n'* ]] || fail "Qwen listener ownership ambiguous"
[[ -n "$BRIDGE_PID_BEFORE" && "$BRIDGE_PID_BEFORE" != *$'\n'* ]] || fail "bridge listener ownership ambiguous"

BOT_PID_FILE="$LOCAL_AI_ROOT/runtime/control-plane/telegram-bot.pid"
BOT_PID_BEFORE=""
if [[ -f "$BOT_PID_FILE" ]]; then
  BOT_PID_BEFORE=$(<"$BOT_PID_FILE")
  kill -0 "$BOT_PID_BEFORE" 2>/dev/null || fail "stored Telegram Bot PID is not live"
fi

mkdir -p "$QUAL_ROOT"
chmod 700 "$QUAL_ROOT"

echo "[1/8] Create isolated scratch feature repository"
git clone --no-local --quiet "$FEATURE_ROOT" "$SCRATCH" || fail "local clone failed"
git -C "$SCRATCH" switch -c "$BRANCH" >/dev/null || fail "scratch feature branch failed"
git -C "$SCRATCH" config user.email "qualification@example.invalid"
git -C "$SCRATCH" config user.name "Local Qwen Qualification"

cat > "$SCRATCH/control-plane/src/local_ai_control/qualification_math.py" <<'PY'
def bounded_add(value, delta, low, high):
    """Add delta and clamp the result into the inclusive range."""
    return min(low, max(high, value + delta))
PY

cat > "$SCRATCH/control-plane/tests/test_local_qwen_e2e_fixture.py" <<'PY'
from local_ai_control.qualification_math import bounded_add


def test_inside_range():
    assert bounded_add(4, 2, 0, 10) == 6


def test_low_clamp():
    assert bounded_add(1, -5, 0, 10) == 0


def test_high_clamp():
    assert bounded_add(8, 7, 0, 10) == 10
PY

git -C "$SCRATCH" add \
  control-plane/src/local_ai_control/qualification_math.py \
  control-plane/tests/test_local_qwen_e2e_fixture.py
git -C "$SCRATCH" commit -m "test: seed local Qwen Supervisor qualification bug" >/dev/null
BASELINE_HEAD=$(git -C "$SCRATCH" rev-parse HEAD)
TEST_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/control-plane/tests/test_local_qwen_e2e_fixture.py" | awk '{print $1}')
IMPL_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/control-plane/src/local_ai_control/qualification_math.py" | awk '{print $1}')

set +e
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests/test_local_qwen_e2e_fixture.py \
    > "$QUAL_ROOT/pre-fix-test.log" 2>&1
)
PRE_RC=$?
set -e
[[ $PRE_RC -ne 0 ]] || fail "seeded fixture unexpectedly passed"

echo "[2/8] Create durable Supervisor job and run real Producer"
SCRATCH="$SCRATCH" DB="$DB" PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" - <<'PY'
import json
import os
import subprocess
from pathlib import Path

from local_ai_control.services.supervisor_contracts import JobStatus, ReviewResult, WorkflowStage
from local_ai_control.services.supervisor_local_qwen import (
    LocalWorktreeSupervisorRepository,
    LocalWorktreeWorkflowSupervisor,
    create_local_qwen_job,
    local_qwen_runners,
)

root = Path(os.environ["SCRATCH"]).resolve()
db = Path(os.environ["DB"]).resolve()
repo = LocalWorktreeSupervisorRepository(root, db)
repo.migrate()
prompt = (
    "Qualification task. Inspect control-plane/src/local_ai_control/qualification_math.py and "
    "control-plane/tests/test_local_qwen_e2e_fixture.py. Run the targeted fixture tests. "
    "Fix the implementation bug in qualification_math.py only. Do not edit the test file or any other file. "
    "Do not commit, push, merge, deploy, access credentials, control services, or use network access. "
    "Finish only after the targeted tests pass."
)
job, unit = create_local_qwen_job(
    repo,
    title="LOCAL_QWEN_SUPERVISOR_E2E",
    owner_id="qualification-owner",
    task_prompt=prompt,
    timeout_seconds=240,
    job_id="local-qwen-e2e",
)
supervisor = LocalWorktreeWorkflowSupervisor(
    repo,
    local_qwen_runners(root, enabled=True),
    timeout_seconds=300,
)
if not supervisor.acquire_singleton():
    raise SystemExit("unable to acquire isolated Supervisor lease")
try:
    for _ in range(6):
        current = supervisor.run_job_once(job.job_id)
        print(f"stage={current.current_stage.value} status={current.status.value} resume={current.resume_state}")
        if current.current_stage is WorkflowStage.VALIDATION and current.status is JobStatus.QUEUED:
            break
        if current.status in {JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELED}:
            raise SystemExit(f"Producer path stopped: {current.status.value} {current.last_error}")
    else:
        raise SystemExit("Producer did not advance to VALIDATION")

    execution = repo.db.execute(
        "SELECT * FROM supervisor_executions WHERE job_id=? AND stage=? ORDER BY started_at DESC LIMIT 1",
        (job.job_id, WorkflowStage.PRODUCER.value),
    ).fetchone()
    if not execution:
        raise SystemExit("Producer execution record missing")
    if execution["provider"] != "LocalQwenCodexRunner":
        raise SystemExit(f"unexpected producer provider: {execution['provider']}")
    if execution["completion_status"] != "COMPLETED_CONFIRMED":
        raise SystemExit(f"producer completion not confirmed: {execution['completion_status']}")
    if execution["cancellation_status"] != "NOT_REQUESTED":
        raise SystemExit("producer cancellation state is unexpected")
    if repo.has_active_mutation_fence():
        raise SystemExit("mutation fence present after confirmed Producer")

    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    expected = [" M control-plane/src/local_ai_control/qualification_math.py"]
    if status != expected:
        raise SystemExit(f"unexpected Producer candidate paths: {status!r}")

    print("PRODUCER_EXECUTION_CONFIRMED")
    print(f"execution_id={execution['execution_id']}")

    # Continue through deterministic validation and self-acceptance until human-review boundary.
    for _ in range(6):
        current = supervisor.run_job_once(job.job_id)
        print(f"stage={current.current_stage.value} status={current.status.value} resume={current.resume_state}")
        if (current.current_stage is WorkflowStage.REVIEW
                and current.status is JobStatus.WAITING
                and current.resume_state == "REVIEW_RESULT_PENDING"):
            break
        if current.status in {JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELED}:
            raise SystemExit(f"pre-review path stopped: {current.status.value} {current.last_error}")
    else:
        raise SystemExit("workflow did not reach durable review boundary")

    review_round = current.review_round + 1
    review_unit = repo.review_work_unit_for_round(job.job_id, job.owner_id, review_round)
    patch = repo.reconstruct_reviewer_patch(job.job_id, job.owner_id, review_round)
    if "qualification_math.py" not in patch or "test_local_qwen_e2e_fixture.py" in patch:
        raise SystemExit("review patch does not match the bounded implementation-only candidate")
    print("DURABLE_REVIEW_BOUNDARY_CONFIRMED")
    print(f"review_work_unit_id={review_unit.review_work_unit_id}")

    # Qualification-only deterministic reviewer: the shell wrapper independently verifies hashes/tests too.
    repo.submit_review_result(
        job.job_id,
        job.owner_id,
        review_round,
        review_unit.review_work_unit_id,
        ReviewResult("PASS"),
    )

    for _ in range(8):
        current = supervisor.run_job_once(job.job_id)
        print(f"stage={current.current_stage.value} status={current.status.value} resume={current.resume_state}")
        if current.status is JobStatus.COMPLETED and current.current_stage is WorkflowStage.DONE:
            break
        if current.status in {JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELED}:
            raise SystemExit(f"post-review path stopped: {current.status.value} {current.last_error}")
    else:
        raise SystemExit("workflow did not complete after review PASS")

    print("SUPERVISOR_WORKFLOW_COMPLETED")
finally:
    supervisor.release_singleton()
    repo.close()
PY

echo "[3/8] Verify protected fixture and implementation mutation"
TEST_HASH_AFTER=$(shasum -a 256 "$SCRATCH/control-plane/tests/test_local_qwen_e2e_fixture.py" | awk '{print $1}')
IMPL_HASH_AFTER=$(shasum -a 256 "$SCRATCH/control-plane/src/local_ai_control/qualification_math.py" | awk '{print $1}')
[[ "$TEST_HASH_AFTER" == "$TEST_HASH_BEFORE" ]] || fail "protected fixture test changed"
[[ "$IMPL_HASH_AFTER" != "$IMPL_HASH_BEFORE" ]] || fail "implementation did not change"
[[ "$(git -C "$SCRATCH" rev-parse HEAD)" == "$BASELINE_HEAD" ]] || fail "Producer created a Git commit"
STATUS=$(git -C "$SCRATCH" status --porcelain=v1 --untracked-files=all)
[[ "$STATUS" == " M control-plane/src/local_ai_control/qualification_math.py" ]] || \
  fail "final candidate contains unexpected paths: $STATUS"

echo "[4/8] Verify targeted fixture now passes"
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests/test_local_qwen_e2e_fixture.py \
    | tee "$QUAL_ROOT/post-fix-targeted.log"
) || fail "targeted post-fix test failed"

echo "[5/8] Verify full control-plane suite on produced candidate"
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests \
    | tee "$QUAL_ROOT/post-fix-full-suite.log"
) || fail "full control-plane suite failed on produced candidate"

echo "[6/8] Verify durable Supervisor terminal state"
DB="$DB" "$PYTHON" - <<'PY'
import os
import sqlite3

db = sqlite3.connect(os.environ["DB"])
db.row_factory = sqlite3.Row
job = db.execute("SELECT * FROM supervisor_jobs WHERE job_id='local-qwen-e2e'").fetchone()
if not job or job["status"] != "COMPLETED" or job["current_stage"] != "DONE":
    raise SystemExit("durable job terminal state mismatch")
execution = db.execute(
    "SELECT * FROM supervisor_executions WHERE job_id='local-qwen-e2e' AND stage='PRODUCER'"
).fetchone()
if not execution or execution["completion_status"] != "COMPLETED_CONFIRMED":
    raise SystemExit("durable Producer execution confirmation missing")
if db.execute("SELECT 1 FROM supervisor_execution_fences WHERE status='ACTIVE'").fetchone():
    raise SystemExit("unexpected active mutation fence")
review = db.execute(
    "SELECT * FROM supervisor_review_results WHERE job_id='local-qwen-e2e' AND review_round=1"
).fetchone()
if not review or review["status"] not in {"SUBMITTED", "CONSUMED"}:
    raise SystemExit("durable review result missing")
print("DURABLE_STATE_PASS")
print(f"producer_execution_id={execution['execution_id']}")
print(f"review_status={review['status']}")
db.close()
PY

echo "[7/8] Verify runtime processes were not replaced"
QWEN_PID_AFTER=$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | sort -u)
BRIDGE_PID_AFTER=$(lsof -nP -iTCP:8010 -sTCP:LISTEN -t 2>/dev/null | sort -u)
[[ "$QWEN_PID_AFTER" == "$QWEN_PID_BEFORE" ]] || fail "Qwen3.8 PID changed"
[[ "$BRIDGE_PID_AFTER" == "$BRIDGE_PID_BEFORE" ]] || fail "V1 bridge PID changed"
if [[ -n "$BOT_PID_BEFORE" ]]; then
  BOT_PID_AFTER=$(<"$BOT_PID_FILE")
  [[ "$BOT_PID_AFTER" == "$BOT_PID_BEFORE" ]] || fail "Telegram Bot PID changed"
  kill -0 "$BOT_PID_AFTER" 2>/dev/null || fail "Telegram Bot stopped"
fi

echo "[8/8] Verify source feature worktree remained untouched"
[[ "$(git -C "$FEATURE_ROOT" rev-parse HEAD)" == "$SOURCE_HEAD" ]] || fail "source feature HEAD changed"
[[ -z "$(git -C "$FEATURE_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "source feature worktree changed"

cat <<EOF
SUPERVISOR_QWEN_CODEX_E2E_PASS
source_branch=$SOURCE_BRANCH
source_head=$SOURCE_HEAD
scratch_branch=$BRANCH
scratch_repo=$SCRATCH
supervisor_db=$DB
qwen_pid=$QWEN_PID_AFTER
bridge_pid=$BRIDGE_PID_AFTER
bot_pid=${BOT_PID_BEFORE:-not_checked}
producer_execution=COMPLETED_CONFIRMED
protected_test_unchanged=PASS
implementation_changed=PASS
no_commit=PASS
targeted_tests=PASS
full_suite=PASS
durable_review=PASS
workflow_terminal=COMPLETED
source_worktree_unchanged=PASS
artifacts=$QUAL_ROOT
EOF

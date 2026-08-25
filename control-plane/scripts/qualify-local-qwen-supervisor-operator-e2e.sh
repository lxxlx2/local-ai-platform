#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
SOURCE_ROOT=${1:-${CONTROL_PLANE_ROOT:h}}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
RUNTIME_ROOT="$LOCAL_AI_ROOT/runtime/supervisor-local-qwen/operator-e2e"
STAMP=$(date +%Y%m%d-%H%M%S)
QUAL_ROOT="$RUNTIME_ROOT/$STAMP-$$"
SCRATCH="$QUAL_ROOT/scratch-repo"
DB="$QUAL_ROOT/operator.db"
PROMPT_FILE="$QUAL_ROOT/task.txt"
PATCH_FILE="$QUAL_ROOT/review.patch"
BRANCH="feat/operator-e2e-$STAMP-$$"
OWNER="operator-e2e-owner"
JOB="operator-e2e"
PRIVATE_MARKER="operator-private-objective-$STAMP-$$"
OPERATOR="$SCRATCH/control-plane/scripts/local-qwen-supervisor.sh"

fail() {
  echo "OPERATOR_E2E_FAIL $1" >&2
  exit 1
}

json_assert() {
  local file=$1
  local expression=$2
  "$PYTHON" - "$file" "$expression" <<'PY'
import json
import sys
path, expression = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
if not eval(expression, {"__builtins__": {}}, {"p": payload}):
    raise SystemExit(f"JSON assertion failed: {expression}; payload={payload}")
PY
}

[[ -x "$PYTHON" ]] || fail "control-plane Python missing"
for command in git curl lsof shasum grep; do
  command -v "$command" >/dev/null 2>&1 || fail "$command missing"
done

[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || fail "source feature worktree is not clean"
SOURCE_BRANCH=$(git -C "$SOURCE_ROOT" branch --show-current)
[[ "$SOURCE_BRANCH" == "feat/codex-qwen-supervisor-v02" ]] || fail "source branch mismatch"
SOURCE_HEAD=$(git -C "$SOURCE_ROOT" rev-parse HEAD)

BRIDGE_HEALTH=$(curl --max-time 5 -sf http://127.0.0.1:8010/health) || fail "V1 bridge unavailable"
printf '%s' "$BRIDGE_HEALTH" | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); assert p.get("status")=="healthy" and p.get("backend")=="mlx-community/Qwen3.8-27B-8bit" and p.get("tool")=="exec_command"' || fail "V1 bridge identity mismatch"
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

echo "[1/10] Create isolated operator workspace"
git clone --no-local --quiet "$SOURCE_ROOT" "$SCRATCH" || fail "local clone failed"
git -C "$SCRATCH" switch -c "$BRANCH" >/dev/null || fail "scratch feature branch failed"
git -C "$SCRATCH" config user.email "operator-e2e@example.invalid"
git -C "$SCRATCH" config user.name "Operator E2E"

cat > "$SCRATCH/control-plane/src/local_ai_control/operator_fixture.py" <<'PY'
def normalize_score(value, low=0, high=100):
    """Clamp a score into the inclusive range."""
    return min(low, max(high, value))
PY

cat > "$SCRATCH/control-plane/tests/test_operator_e2e_fixture.py" <<'PY'
from local_ai_control.operator_fixture import normalize_score


def test_inside_range():
    assert normalize_score(50) == 50


def test_low_clamp():
    assert normalize_score(-7) == 0


def test_high_clamp():
    assert normalize_score(140) == 100
PY

git -C "$SCRATCH" add control-plane/src/local_ai_control/operator_fixture.py control-plane/tests/test_operator_e2e_fixture.py
git -C "$SCRATCH" commit -m "test: seed operator E2E bug" >/dev/null
BASELINE_HEAD=$(git -C "$SCRATCH" rev-parse HEAD)
TEST_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/control-plane/tests/test_operator_e2e_fixture.py" | awk '{print $1}')
IMPL_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/control-plane/src/local_ai_control/operator_fixture.py" | awk '{print $1}')

set +e
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests/test_operator_e2e_fixture.py > "$QUAL_ROOT/pre-fix.log" 2>&1
)
PRE_RC=$?
set -e
[[ $PRE_RC -ne 0 ]] || fail "seeded fixture unexpectedly passed"

cat > "$PROMPT_FILE" <<EOF
$PRIVATE_MARKER
Inspect control-plane/src/local_ai_control/operator_fixture.py and control-plane/tests/test_operator_e2e_fixture.py. Run the targeted fixture tests. Fix the implementation bug in operator_fixture.py only. Do not edit tests or any other file. Do not commit, push, merge, deploy, access credentials, control services, or use network access. Finish only when the targeted tests pass.
EOF
chmod 600 "$PROMPT_FILE"

[[ -f "$OPERATOR" ]] || fail "operator launcher missing from scratch workspace"

echo "[2/10] submit: run autonomous Producer until durable Review boundary"
zsh "$OPERATOR" --workspace "$SCRATCH" --db "$DB" --owner "$OWNER" submit --job-id "$JOB" --title "OPERATOR_E2E" --prompt-file "$PROMPT_FILE" --timeout 240 > "$QUAL_ROOT/submit.json" || fail "operator submit failed"
json_assert "$QUAL_ROOT/submit.json" 'p.get("job_id")=="operator-e2e" and p.get("status")=="WAITING" and p.get("stage")=="REVIEW" and p.get("resume_state")=="REVIEW_RESULT_PENDING"'

grep -Fq "$PRIVATE_MARKER" "$QUAL_ROOT/submit.json" && fail "submit response leaked private prompt"

echo "[3/10] status: safe durable summary only"
zsh "$OPERATOR" --workspace "$SCRATCH" --db "$DB" --owner "$OWNER" status --job "$JOB" > "$QUAL_ROOT/status.json" || fail "operator status failed"
json_assert "$QUAL_ROOT/status.json" 'p.get("job_id")=="operator-e2e" and p.get("status")=="WAITING" and p.get("stage")=="REVIEW" and bool(p.get("review_work_unit_id")) and bool(p.get("patch_sha256"))'
grep -Fq "$PRIVATE_MARKER" "$QUAL_ROOT/status.json" && fail "status leaked private prompt"

REVIEW_RESULT_COUNT=$(DB="$DB" "$PYTHON" - <<'PY'
import os, sqlite3
db=sqlite3.connect(os.environ["DB"])
print(db.execute("SELECT COUNT(*) FROM supervisor_review_results WHERE job_id='operator-e2e'").fetchone()[0])
db.close()
PY
)
[[ "$REVIEW_RESULT_COUNT" == "0" ]] || fail "submit auto-approved review"

echo "[4/10] review-show: materialize bounded immutable candidate patch"
zsh "$OPERATOR" --workspace "$SCRATCH" --db "$DB" --owner "$OWNER" review-show --job "$JOB" --output "$PATCH_FILE" > "$QUAL_ROOT/review-show.json" || fail "review-show failed"
json_assert "$QUAL_ROOT/review-show.json" 'p.get("job_id")=="operator-e2e" and p.get("review_round")==1 and bool(p.get("review_work_unit_id")) and bool(p.get("patch_sha256")) and bool(p.get("candidate_identity_sha256"))'
[[ -s "$PATCH_FILE" ]] || fail "review patch missing"
grep -Fq "operator_fixture.py" "$PATCH_FILE" || fail "review patch does not include implementation"
grep -Fq "test_operator_e2e_fixture.py" "$PATCH_FILE" && fail "review patch unexpectedly includes protected test"
grep -Fq "$PRIVATE_MARKER" "$PATCH_FILE" && fail "review patch leaked private prompt"

TEST_HASH_AFTER_PRODUCER=$(shasum -a 256 "$SCRATCH/control-plane/tests/test_operator_e2e_fixture.py" | awk '{print $1}')
IMPL_HASH_AFTER_PRODUCER=$(shasum -a 256 "$SCRATCH/control-plane/src/local_ai_control/operator_fixture.py" | awk '{print $1}')
[[ "$TEST_HASH_AFTER_PRODUCER" == "$TEST_HASH_BEFORE" ]] || fail "protected test changed during Producer"
[[ "$IMPL_HASH_AFTER_PRODUCER" != "$IMPL_HASH_BEFORE" ]] || fail "implementation did not change during Producer"
[[ "$(git -C "$SCRATCH" rev-parse HEAD)" == "$BASELINE_HEAD" ]] || fail "Producer created a Git commit"
[[ "$(git -C "$SCRATCH" status --porcelain=v1 --untracked-files=all)" == " M control-plane/src/local_ai_control/operator_fixture.py" ]] || fail "unexpected Producer candidate paths"

echo "[5/10] review-pass: bind human approval to exact durable review unit"
zsh "$OPERATOR" --workspace "$SCRATCH" --db "$DB" --owner "$OWNER" review-pass --job "$JOB" > "$QUAL_ROOT/review-pass.json" || fail "review-pass failed"
json_assert "$QUAL_ROOT/review-pass.json" 'p.get("job_id")=="operator-e2e" and p.get("review_round")==1 and p.get("review_status") in ("SUBMITTED","CONSUMED") and bool(p.get("result_hash"))'

echo "[6/10] continue: consume review and run Security/Git Gate to DONE"
zsh "$OPERATOR" --workspace "$SCRATCH" --db "$DB" --owner "$OWNER" continue --job "$JOB" > "$QUAL_ROOT/continue.json" || fail "operator continue failed"
json_assert "$QUAL_ROOT/continue.json" 'p.get("job_id")=="operator-e2e" and p.get("status")=="COMPLETED" and p.get("stage")=="DONE"'

echo "[7/10] Verify produced candidate and targeted tests"
[[ "$(git -C "$SCRATCH" rev-parse HEAD)" == "$BASELINE_HEAD" ]] || fail "operator workflow created a Git commit"
[[ "$(shasum -a 256 "$SCRATCH/control-plane/tests/test_operator_e2e_fixture.py" | awk '{print $1}')" == "$TEST_HASH_BEFORE" ]] || fail "protected test changed"
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests/test_operator_e2e_fixture.py | tee "$QUAL_ROOT/targeted.log"
) || fail "targeted fixture failed after operator workflow"

echo "[8/10] Verify full candidate suite"
(
  cd "$SCRATCH/control-plane"
  PYTHONPATH="$SCRATCH/control-plane/src" "$PYTHON" -m pytest -q tests | tee "$QUAL_ROOT/full-suite.log"
) || fail "full control-plane suite failed on operator candidate"

echo "[9/10] Verify durable ledger and review consumption"
DB="$DB" "$PYTHON" - <<'PY'
import os, sqlite3
path=os.environ["DB"]
db=sqlite3.connect(path)
db.row_factory=sqlite3.Row
job=db.execute("SELECT * FROM supervisor_jobs WHERE job_id='operator-e2e'").fetchone()
if not job or job['status']!='COMPLETED' or job['current_stage']!='DONE':
    raise SystemExit('durable terminal job mismatch')
execution=db.execute("SELECT * FROM supervisor_executions WHERE job_id='operator-e2e' AND stage='PRODUCER'").fetchone()
if not execution or execution['provider']!='LocalQwenCodexRunner' or execution['completion_status']!='COMPLETED_CONFIRMED' or execution['cancellation_status']!='NOT_REQUESTED':
    raise SystemExit('durable Producer execution mismatch')
review=db.execute("SELECT * FROM supervisor_review_results WHERE job_id='operator-e2e' AND review_round=1").fetchone()
if not review or review['status']!='CONSUMED':
    raise SystemExit('durable review was not consumed')
if db.execute("SELECT 1 FROM supervisor_execution_fences WHERE status='ACTIVE'").fetchone():
    raise SystemExit('unexpected active mutation fence')
print('DURABLE_OPERATOR_STATE_PASS')
print(f"producer_execution_id={execution['execution_id']}")
print(f"review_status={review['status']}")
db.close()
PY

echo "[10/10] Verify production processes and source worktree unchanged"
QWEN_PID_AFTER=$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | sort -u)
BRIDGE_PID_AFTER=$(lsof -nP -iTCP:8010 -sTCP:LISTEN -t 2>/dev/null | sort -u)
[[ "$QWEN_PID_AFTER" == "$QWEN_PID_BEFORE" ]] || fail "Qwen3.8 PID changed"
[[ "$BRIDGE_PID_AFTER" == "$BRIDGE_PID_BEFORE" ]] || fail "bridge PID changed"
if [[ -n "$BOT_PID_BEFORE" ]]; then
  BOT_PID_AFTER=$(<"$BOT_PID_FILE")
  [[ "$BOT_PID_AFTER" == "$BOT_PID_BEFORE" ]] || fail "Telegram Bot PID changed"
  kill -0 "$BOT_PID_AFTER" 2>/dev/null || fail "Telegram Bot stopped"
fi
[[ "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" == "$SOURCE_HEAD" ]] || fail "source feature HEAD changed"
[[ -z "$(git -C "$SOURCE_ROOT" status --porcelain=v1 --untracked-files=all)" ]] || fail "source feature worktree changed"

cat <<EOF
LOCAL_QWEN_OPERATOR_E2E_PASS
source_branch=$SOURCE_BRANCH
source_head=$SOURCE_HEAD
scratch_branch=$BRANCH
scratch_repo=$SCRATCH
operator_db=$DB
qwen_pid=$QWEN_PID_AFTER
bridge_pid=$BRIDGE_PID_AFTER
bot_pid=${BOT_PID_BEFORE:-not_checked}
submit_stopped_at_review=PASS
private_prompt_not_exposed=PASS
review_show=PASS
human_review_pass_binding=PASS
producer_execution=COMPLETED_CONFIRMED
protected_test_unchanged=PASS
implementation_changed=PASS
no_commit=PASS
targeted_tests=PASS
full_suite=PASS
review_status=CONSUMED
workflow_terminal=COMPLETED
source_worktree_unchanged=PASS
artifacts=$QUAL_ROOT
EOF

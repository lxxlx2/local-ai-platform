#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
STAMP=$(date +%Y%m%d-%H%M%S)-$$
ROOT=$LOCAL_AI_ROOT/runtime/generic-projects/qualification-$STAMP
EXTERNAL_REPO=$ROOT/external-repo
OPERATOR_RUNTIME=$ROOT/operator
PROMPT=$ROOT/task.txt
SUBMIT_JSON=$ROOT/submit.json
SHOW_JSON=$ROOT/review-show.json
PATCH=$ROOT/candidate.patch
FINAL_JSON=$ROOT/final.json
PROJECT_ID=e2e-$STAMP
TASK_ID=fix-score
LAUNCHER=$SCRIPT_DIR/local-qwen-project.sh

mkdir -p "$ROOT" "$EXTERNAL_REPO"
chmod 700 "$ROOT"

printf '[1/8] Focused Generic Project + direct-Qwen + quota guard checks\n'
PYTHONPATH="$CONTROL_PLANE_ROOT/src" "$PYTHON" -m pytest -q \
  "$CONTROL_PLANE_ROOT/tests/test_generic_project_adapter.py" \
  "$CONTROL_PLANE_ROOT/tests/test_codex_quota_guard.py" \
  "$CONTROL_PLANE_ROOT/tests/test_direct_local_qwen_agent.py" \
  "$CONTROL_PLANE_ROOT/tests/test_generic_project_review_policy.py" \
  "$CONTROL_PLANE_ROOT/tests/test_supervisor_gemini_review.py"

printf '[2/8] Create real second Git project outside local-ai-platform\n'
git -C "$EXTERNAL_REPO" init -b main >/dev/null
git -C "$EXTERNAL_REPO" config user.email local-ai-e2e@example.invalid
git -C "$EXTERNAL_REPO" config user.name 'Local AI E2E'
cat >"$EXTERNAL_REPO/app.py" <<'PY'
def normalize_score(value):
    return max(0.0, min(1.0, value / 10.0))
PY
cat >"$EXTERNAL_REPO/test_app.py" <<'PY'
from app import normalize_score


def test_normalize_score_percentage():
    assert normalize_score(50) == 0.5
    assert normalize_score(100) == 1.0
    assert normalize_score(0) == 0.0
PY
cat >"$EXTERNAL_REPO/pyproject.toml" <<'TOML'
[project]
name = "generic-project-e2e"
version = "0.0.1"
TOML
git -C "$EXTERNAL_REPO" add .
git -C "$EXTERNAL_REPO" commit -m seed >/dev/null
SOURCE_HEAD=$(git -C "$EXTERNAL_REPO" rev-parse HEAD)

cat >"$PROMPT" <<'TXT'
Fix the normalize_score implementation so the existing tests pass. Inspect only this project, make the smallest correct source change, run the fixed pytest test command, and do not change the tests. Do not commit, push, merge, install packages, use network access, control services, or access credentials.
TXT
chmod 600 "$PROMPT"

printf '[3/8] Register external project\n'
/bin/zsh "$LAUNCHER" --runtime "$OPERATOR_RUNTIME" register --repo "$EXTERNAL_REPO" --project-id "$PROJECT_ID" >/dev/null

printf '[4/8] Run direct Local Qwen task to durable Gemini Review boundary\n'
/bin/zsh "$LAUNCHER" --runtime "$OPERATOR_RUNTIME" task \
  --project "$PROJECT_ID" \
  --task-id "$TASK_ID" \
  --prompt-file "$PROMPT" \
  --test-profile pytest \
  --privacy RESTRICTED \
  --risk LOW \
  --timeout 240 >"$SUBMIT_JSON"

"$PYTHON" - "$SUBMIT_JSON" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p["status"] == "WAITING", p
assert p["stage"] == "REVIEW", p
assert p["resume_state"] == "REVIEW_RESULT_PENDING", p
assert p.get("patch_sha256"), p
assert p.get("review_work_unit_id"), p
assert "gemini_advisory" in p, p
print("REVIEW_BOUNDARY_PASS")
print("gemini_advisory_status=" + str(p["gemini_advisory"].get("status")))
PY

WORKTREE=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["worktree"])' "$SUBMIT_JSON")

printf '[5/8] Materialize immutable patch and verify candidate\n'
/bin/zsh "$LAUNCHER" --runtime "$OPERATOR_RUNTIME" review-show \
  --project "$PROJECT_ID" --task-id "$TASK_ID" --output "$PATCH" >"$SHOW_JSON"
cat "$PATCH"

grep -q 'app.py' "$PATCH"
if grep -q '^+.*return max(0.0, min(1.0, value / 10.0))' "$PATCH"; then
  echo "implementation bug remains" >&2
  exit 1
fi
if ! git -C "$WORKTREE" diff --quiet -- test_app.py; then
  echo "protected test changed" >&2
  exit 1
fi
[[ $(git -C "$WORKTREE" rev-parse HEAD) == "$SOURCE_HEAD" ]]
[[ -n $(git -C "$WORKTREE" status --porcelain) ]]

printf '[6/8] Run deterministic fixture test\n'
(cd "$WORKTREE" && PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -q)

printf '\nReview the small deterministic qualification patch above.\n'
printf 'Approve this qualification candidate and continue through Security/Git Gate? [y/N] '
read -r REPLY
case "$REPLY" in
  y|Y|yes|YES) ;;
  *) echo 'QUALIFICATION_STOPPED_BY_OWNER'; exit 2 ;;
esac

printf '[7/8] Bind owner approval and continue to terminal workflow\n'
/bin/zsh "$LAUNCHER" --runtime "$OPERATOR_RUNTIME" review-pass \
  --project "$PROJECT_ID" --task-id "$TASK_ID" >/dev/null
/bin/zsh "$LAUNCHER" --runtime "$OPERATOR_RUNTIME" continue \
  --project "$PROJECT_ID" --task-id "$TASK_ID" >"$FINAL_JSON"

"$PYTHON" - "$FINAL_JSON" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
assert p["status"] == "COMPLETED", p
assert p["stage"] == "DONE", p
print("TERMINAL_WORKFLOW_PASS")
PY

printf '[8/8] Verify source repo untouched and candidate uncommitted\n'
[[ $(git -C "$EXTERNAL_REPO" rev-parse HEAD) == "$SOURCE_HEAD" ]]
[[ -z $(git -C "$EXTERNAL_REPO" status --porcelain) ]]
[[ $(git -C "$WORKTREE" rev-parse HEAD) == "$SOURCE_HEAD" ]]
[[ -n $(git -C "$WORKTREE" status --porcelain) ]]

printf '\nGENERIC_PROJECT_E2E_PASS\n'
printf 'source_repo=%s\n' "$EXTERNAL_REPO"
printf 'worktree=%s\n' "$WORKTREE"
printf 'operator_runtime=%s\n' "$OPERATOR_RUNTIME"
printf 'source_head=%s\n' "$SOURCE_HEAD"
printf 'source_unchanged=PASS\n'
printf 'candidate_uncommitted=PASS\n'
printf 'protected_test_unchanged=PASS\n'
printf 'owner_review_binding=PASS\n'
printf 'codex_quota_guard=ENFORCED\n'
printf 'codex_cli_invoked=NO\n'
printf 'local_executor=DIRECT_QWEN_TOOLS\n'
printf 'artifacts=%s\n' "$ROOT"

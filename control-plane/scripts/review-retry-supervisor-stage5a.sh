#!/bin/zsh
set -eu
set -o pipefail

ROOT=/Users/jerson/AI
BRANCH=fix/supervisor-start-identity-v01
FIX_COMMIT=d0e20cc932db54995c36ff235f472fd107ac4156
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
CONTROL="$ROOT/control-plane"
IDENTITY="$ROOT/runtime/supervisor/supervisor.identity.json"
STAMP=$(date +%Y%m%d-%H%M%S)
REPORT="$ROOT/runtime/supervisor/stage5a-identity-review-$STAMP.log"
STARTED=0

fail() {
  echo "REVIEW_STAGE5A: FAIL"
  echo "ERROR: $*"
  exit 1
}

cleanup() {
  if [[ $STARTED -eq 1 ]]; then
    echo "Cleanup: stopping only the exact identity-verified Supervisor started by this harness."
    "$CONTROL/scripts/stop-supervisor.sh" || true
  fi
}
trap cleanup EXIT INT TERM

mkdir -p "$ROOT/runtime/supervisor"
umask 077
exec > >(tee "$REPORT") 2>&1

print "===== SUPERVISOR START-IDENTITY INDEPENDENT ACCEPTANCE ====="
print "report=$REPORT"

cd "$ROOT"
[[ -x "$PYTHON" ]] || fail "control-plane venv python missing: $PYTHON"
[[ -z "$(git status --porcelain)" ]] || fail "worktree must be clean before review"

git fetch origin
if [[ "$(git branch --show-current)" != "$BRANCH" ]]; then
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH"
  else
    git switch --track -c "$BRANCH" "origin/$BRANCH"
  fi
fi
git merge --ff-only "origin/$BRANCH"

git merge-base --is-ancestor "$FIX_COMMIT" HEAD || fail "reviewed fix commit is not an ancestor of current branch HEAD"
print "branch=$(git branch --show-current)"
print "head=$(git rev-parse HEAD)"
print "fix_commit=$FIX_COMMIT"
print "origin_main=$(git rev-parse origin/main)"
[[ -z "$(git status --porcelain)" ]] || fail "worktree changed during branch preparation"

print "\n===== STATIC / TEST REVIEW ====="
cd "$CONTROL"
"$PYTHON" -m pytest -q tests/test_supervisor_process_identity_compat.py
"$PYTHON" -m pytest -q
zsh -n scripts/start-supervisor.sh scripts/status-supervisor.sh scripts/stop-supervisor.sh

if grep -En '\b(pgrep|pkill|killall)\b' \
  src/local_ai_control/supervisor/process_identity.py \
  scripts/start-supervisor.sh scripts/status-supervisor.sh scripts/stop-supervisor.sh; then
  fail "broad process discovery/signaling token found"
fi
print "INDEPENDENT_TEST_REVIEW: PASS"

print "\n===== PRE-RETRY RUNTIME SNAPSHOT ====="
"$CONTROL/scripts/status-bot.sh" || fail "bot status probe failed"
"$CONTROL/scripts/status-model-downloads.sh" || fail "download-manager status probe failed"

# Refuse to proceed through an unknown live identity. A stale/dead identity is
# handled by the reviewed launcher itself; INVALID/MISMATCH remains fail-closed.
if [[ -f "$IDENTITY" ]]; then
  set +e
  ID_STATUS=$(PYTHONPATH="$CONTROL/src" "$PYTHON" -m local_ai_control.supervisor.process_identity check --file "$IDENTITY" 2>/dev/null)
  ID_RC=$?
  set -e
  if [[ $ID_RC -eq 0 ]]; then
    fail "Supervisor is already running; stop it through the exact identity-aware stop script before retry"
  elif [[ $ID_RC -eq 4 ]]; then
    fail "existing Supervisor identity is INVALID/MISMATCH; manual reconciliation required: $ID_STATUS"
  fi
  print "preexisting_identity_status=$ID_STATUS"
fi

print "\n===== CONTROLLED STAGE 5A START-IDENTITY RETRY ====="
"$CONTROL/scripts/start-supervisor.sh"
STARTED=1
sleep 1

PYTHONPATH="$CONTROL/src" "$PYTHON" -m local_ai_control.supervisor.process_identity check --file "$IDENTITY"
PID=$(PYTHONPATH="$CONTROL/src" "$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY")
[[ -n "$PID" ]] || fail "identity file did not yield an exact Supervisor PID"
print "verified_supervisor_pid=$PID"

"$CONTROL/scripts/status-supervisor.sh"
sleep 2

"$CONTROL/scripts/stop-supervisor.sh"
STARTED=0
[[ ! -f "$IDENTITY" ]] || fail "identity file remained after exact stop"
print "STAGE5A_START_IDENTITY_RETRY: PASS"

print "\n===== POST-RETRY SAFETY CHECKS ====="
"$CONTROL/scripts/status-bot.sh" || fail "bot status probe failed after retry"
"$CONTROL/scripts/status-model-downloads.sh" || fail "download-manager status probe failed after retry"
cd "$ROOT"
[[ -z "$(git status --porcelain)" ]] || fail "review/retry changed tracked or untracked repository files"

print "\nINDEPENDENT_REVIEW_EXECUTION: PASS"
print "STAGE5A_START_IDENTITY_RETRY: PASS"
print "MAIN_MERGED: NO"
print "DEPLOYED: NO"
print "REAL_CODEX_ENABLED_BY_THIS_SCRIPT: NO"
print "DOWNLOADS_STARTED_BY_THIS_SCRIPT: NO"
print "WORKTREE_CLEAN: YES"
print "REPORT: $REPORT"
print "REVIEW_STAGE5A: PASS"

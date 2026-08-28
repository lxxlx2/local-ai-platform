#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
PROJECT_ROOT=${CONTROL_PLANE_ROOT:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
export PYTHONPATH="$CONTROL_PLANE_ROOT/src"

FEATURE_ROOT=${1:-$PROJECT_ROOT}
RUNTIME_ROOT="$LOCAL_AI_ROOT/runtime/codex-qwen"
PRIVATE_TMP="$RUNTIME_ROOT/tmp"
mkdir -p "$PRIVATE_TMP"
chmod 700 "$RUNTIME_ROOT" "$PRIVATE_TMP" 2>/dev/null || true
export TMPDIR="$PRIVATE_TMP"

fail() {
  echo "QUALIFICATION_FAIL $1" >&2
  exit 1
}

[[ -x "$PYTHON" ]] || fail "control-plane Python missing: $PYTHON"
command -v codex >/dev/null 2>&1 || fail "codex executable missing"
command -v git >/dev/null 2>&1 || fail "git executable missing"
command -v curl >/dev/null 2>&1 || fail "curl executable missing"
command -v lsof >/dev/null 2>&1 || fail "lsof executable missing"

VERSION=$(codex --version 2>/dev/null || true)
[[ "$VERSION" == *"0.148.0"* ]] || fail "Codex version unqualified: $VERSION"

BRIDGE_HEALTH=$(curl -sf http://127.0.0.1:8010/health) || fail "bridge health unavailable"
printf '%s' "$BRIDGE_HEALTH" | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); assert p.get("status")=="healthy" and p.get("tool")=="exec_command" and p.get("backend")=="mlx-community/Qwen3.8-27B-8bit"' || fail "bridge is not the V1 local producer"
curl -sf http://127.0.0.1:8001/health >/dev/null || fail "Qwen3.8 MAIN health unavailable"

QWEN_PID_BEFORE=$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | sort -u)
[[ -n "$QWEN_PID_BEFORE" && "$QWEN_PID_BEFORE" != *$'\n'* ]] || fail "Qwen3.8 listener ownership ambiguous"

BOT_PID_FILE="$LOCAL_AI_ROOT/runtime/control-plane/telegram-bot.pid"
BOT_PID_BEFORE=""
if [[ -f "$BOT_PID_FILE" ]]; then
  BOT_PID_BEFORE=$(<"$BOT_PID_FILE")
  kill -0 "$BOT_PID_BEFORE" 2>/dev/null || fail "stored Telegram Bot PID is not live"
fi

"$PYTHON" -m local_ai_control.services.codex_qwen_workspace "$FEATURE_ROOT" >/dev/null || fail "feature worktree policy denied"

echo "[1/7] Focused Local Producer tests"
(
  cd "$CONTROL_PLANE_ROOT"
  "$PYTHON" -m pytest -q tests/test_codex_qwen_bridge.py tests/test_codex_qwen_workspace.py
) || fail "focused tests"

echo "[2/7] Full control-plane tests"
(
  cd "$CONTROL_PLANE_ROOT"
  "$PYTHON" -m pytest -q tests
) || fail "full control-plane tests"

echo "[3/7] Git whitespace validation"
git -C "$FEATURE_ROOT" diff --check || fail "git diff --check"

STAMP=$(date +%Y%m%d-%H%M%S)
QUAL_ROOT="$RUNTIME_ROOT/qualification-$STAMP-$$"
SCRATCH="$QUAL_ROOT/scratch-repo"
mkdir -p "$SCRATCH"
chmod 700 "$QUAL_ROOT" "$SCRATCH"

cat > "$SCRATCH/math_box.py" <<'PY'
def clamp(value, low, high):
    return min(low, max(high, value))
PY

cat > "$SCRATCH/test_math_box.py" <<'PY'
from math_box import clamp


def test_clamp_inside_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_low():
    assert clamp(-2, 0, 10) == 0


def test_clamp_high():
    assert clamp(12, 0, 10) == 10
PY

git -C "$SCRATCH" init -b feat/qualification >/dev/null
TEST_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/test_math_box.py" | awk '{print $1}')
IMPL_HASH_BEFORE=$(shasum -a 256 "$SCRATCH/math_box.py" | awk '{print $1}')

set +e
(
  cd "$SCRATCH"
  "$PYTHON" -m pytest -q test_math_box.py > "$QUAL_ROOT/pre-fix-test.log" 2>&1
)
PRE_RC=$?
set -e
[[ $PRE_RC -ne 0 ]] || fail "seeded scratch test unexpectedly passed"

echo "[4/7] Real Codex CLI -> bridge -> Qwen3.8 -> exec_command loop"
PROMPT='This is a qualification scratch repository. Inspect the files and run the tests. Fix the implementation bug in math_box.py only. Do not edit test_math_box.py. Do not commit or push. Do not use network access. Finish only after the tests pass, and report what changed.'
zsh "$SCRIPT_DIR/run-codex-qwen-local.sh" "$SCRATCH" exec --json --ephemeral "$PROMPT" > "$QUAL_ROOT/codex.jsonl" || fail "Codex local producer run"

TEST_HASH_AFTER=$(shasum -a 256 "$SCRATCH/test_math_box.py" | awk '{print $1}')
IMPL_HASH_AFTER=$(shasum -a 256 "$SCRATCH/math_box.py" | awk '{print $1}')
[[ "$TEST_HASH_BEFORE" == "$TEST_HASH_AFTER" ]] || fail "protected test file changed"
[[ "$IMPL_HASH_BEFORE" != "$IMPL_HASH_AFTER" ]] || fail "implementation did not change"
if git -C "$SCRATCH" rev-parse --verify HEAD >/dev/null 2>&1; then
  fail "local producer created a commit"
fi

echo "[5/7] Post-fix test"
(
  cd "$SCRATCH"
  "$PYTHON" -m pytest -q test_math_box.py | tee "$QUAL_ROOT/post-fix-test.log"
) || fail "post-fix test"

echo "[6/7] Production runtime invariants"
QWEN_PID_AFTER=$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | sort -u)
[[ "$QWEN_PID_AFTER" == "$QWEN_PID_BEFORE" ]] || fail "Qwen3.8 MAIN PID changed"
curl -sf http://127.0.0.1:8001/health >/dev/null || fail "Qwen3.8 MAIN unhealthy after qualification"
if [[ -n "$BOT_PID_BEFORE" ]]; then
  BOT_PID_AFTER=$(<"$BOT_PID_FILE")
  [[ "$BOT_PID_AFTER" == "$BOT_PID_BEFORE" ]] || fail "Telegram Bot PID changed"
  kill -0 "$BOT_PID_AFTER" 2>/dev/null || fail "Telegram Bot stopped"
fi

echo "[7/7] Obvious credential literal scan on Local Producer implementation"
SCAN_FILES=(
  "$CONTROL_PLANE_ROOT/src/local_ai_control/services/codex_qwen_bridge.py"
  "$CONTROL_PLANE_ROOT/src/local_ai_control/services/codex_qwen_workspace.py"
  "$CONTROL_PLANE_ROOT/tests/test_codex_qwen_bridge.py"
  "$CONTROL_PLANE_ROOT/tests/test_codex_qwen_workspace.py"
  "$SCRIPT_DIR/run-codex-qwen-local.sh"
)
if grep -En '(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|seed phrase|mnemonic[[:space:]]*=|password[[:space:]]*=[[:space:]]*[^"'"'"'[:space:]]+)' "${SCAN_FILES[@]}"; then
  fail "credential-like literal detected"
fi

cat <<EOF
QUALIFICATION_PASS
codex_version=$VERSION
feature_root=$FEATURE_ROOT
qwen_pid=$QWEN_PID_AFTER
bot_pid=${BOT_PID_BEFORE:-not_checked}
artifacts=$QUAL_ROOT
scratch_repo=$SCRATCH
protected_test_unchanged=PASS
implementation_changed=PASS
post_fix_tests=PASS
no_commit=PASS
EOF

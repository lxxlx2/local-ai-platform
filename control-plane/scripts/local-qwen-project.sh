#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
SOURCE_ROOT=${CONTROL_PLANE_ROOT:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
BRIDGE_HEALTH=http://127.0.0.1:8010/health
RUNTIME=$LOCAL_AI_ROOT/runtime/generic-projects
BRIDGE_LOG=$RUNTIME/bridge-session.log

[[ -x "$PYTHON" ]] || {
  echo "LOCAL_QWEN_PROJECT_PYTHON_MISSING" >&2
  exit 1
}

export PYTHONPATH="$CONTROL_PLANE_ROOT/src"
export PATH="$LOCAL_AI_ROOT/runtime/control-plane-venv/bin:$PATH"

NEEDS_BRIDGE=0
for ARG in "$@"; do
  if [[ "$ARG" == "task" || "$ARG" == "continue" ]]; then
    NEEDS_BRIDGE=1
    break
  fi
done

BRIDGE_PID=""
cleanup_bridge() {
  if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
}

if [[ "$NEEDS_BRIDGE" == "1" ]] && ! curl -sf "$BRIDGE_HEALTH" >/dev/null 2>&1; then
  mkdir -p "$RUNTIME"
  chmod 700 "$RUNTIME"
  /bin/zsh "$SCRIPT_DIR/run-codex-qwen-bridge.sh" >"$BRIDGE_LOG" 2>&1 &
  BRIDGE_PID=$!
  trap cleanup_bridge EXIT INT TERM
  READY=0
  for _ in {1..30}; do
    if curl -sf "$BRIDGE_HEALTH" >/dev/null 2>&1; then
      READY=1
      break
    fi
    sleep 1
  done
  if [[ "$READY" != "1" ]]; then
    echo "LOCAL_QWEN_PROJECT_BRIDGE_START_FAILED" >&2
    exit 1
  fi
fi

set +e
"$PYTHON" -m local_ai_control.services.generic_project_operator_guarded "$@"
STATUS=$?
set -e
cleanup_bridge
trap - EXIT INT TERM
exit "$STATUS"

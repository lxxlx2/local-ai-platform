#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
PORT=${CODEX_QWEN_BRIDGE_PORT:-8010}
export PYTHONPATH="$CONTROL_PLANE_ROOT/src"

[[ -x "$PYTHON" ]] || {
  echo "CONTROL_PLANE_PYTHON_MISSING $PYTHON" >&2
  exit 1
}

curl -sf http://127.0.0.1:8001/health >/dev/null || {
  echo "QWEN38_MAIN_UNHEALTHY" >&2
  exit 1
}

exec "$PYTHON" -m local_ai_control.services.codex_qwen_bridge serve --host 127.0.0.1 --port "$PORT"

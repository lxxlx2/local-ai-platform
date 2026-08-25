#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}

[[ -x "$PYTHON" ]] || {
  echo "LOCAL_QWEN_OPERATOR_PYTHON_MISSING" >&2
  exit 1
}

export PYTHONPATH="$CONTROL_PLANE_ROOT/src"
export PATH="$LOCAL_AI_ROOT/runtime/control-plane-venv/bin:$PATH"

exec "$PYTHON" -m local_ai_control.services.supervisor_local_qwen_operator "$@"

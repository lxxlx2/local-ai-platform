#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}

[[ -x "$PYTHON" ]] || {
  echo "OWNER_RAW_CONTROL_PYTHON_MISSING" >&2
  exit 1
}

export PYTHONPATH="$CONTROL_PLANE_ROOT/src"
export PATH="/usr/bin:/bin"

exec "$PYTHON" "$SCRIPT_DIR/owner-raw-qwen.py" "$@"

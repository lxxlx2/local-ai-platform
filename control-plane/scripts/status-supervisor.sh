#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
PIDFILE="$ROOT/runtime/supervisor/supervisor.pid"
EXPECTED="local_ai_control.supervisor.app daemon"
STATE=STOPPED
PID="-"

if [[ -f "$PIDFILE" ]]; then
  set +e
  PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity verify >/dev/null 2>&1
  VERIFY_RC=$?
  set -e
  if [[ $VERIFY_RC -eq 0 ]]; then
    STATE=RUNNING
    PID=$(<"$PIDFILE")
  elif [[ $VERIFY_RC -eq 3 ]]; then
    STATE=IDENTITY_MISMATCH
    PID=$(<"$PIDFILE")
  fi
fi

echo "STATUS=$STATE"
echo "PID=$PID"
PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.app status

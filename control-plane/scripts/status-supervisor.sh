#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
IDENTITY="$ROOT/runtime/supervisor/supervisor.identity.json"
export PYTHONPATH="$ROOT/control-plane/src"
STATE=STOPPED
PID="-"
if [[ -f "$IDENTITY" ]]; then
  set +e
  STATUS=$("$PYTHON" -m local_ai_control.supervisor.process_identity check --file "$IDENTITY" 2>/dev/null)
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    STATE=RUNNING
    PID=$("$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY")
  elif [[ $RC -eq 4 ]]; then
    STATE=IDENTITY_MISMATCH
  fi
fi
echo "STATUS=$STATE"
echo "PID=$PID"
"$PYTHON" -m local_ai_control.supervisor.app status

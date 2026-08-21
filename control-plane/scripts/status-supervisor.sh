#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
PIDFILE="$ROOT/runtime/supervisor/supervisor.pid"
EXPECTED="local_ai_control.supervisor.app daemon"
STATE=STOPPED
PID="-"
if [[ -f "$PIDFILE" ]]; then
  CANDIDATE=$(<"$PIDFILE")
  COMMAND=$(ps -p "$CANDIDATE" -o command= 2>/dev/null || true)
  if [[ "$COMMAND" == *"$EXPECTED"* ]]; then STATE=RUNNING; PID="$CANDIDATE"; fi
fi
echo "STATUS=$STATE"
echo "PID=$PID"
PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.app status

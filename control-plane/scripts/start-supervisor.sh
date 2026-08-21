#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
PIDFILE="$ROOT/runtime/supervisor/supervisor.pid"
EXPECTED="local_ai_control.supervisor.app daemon"
mkdir -p "$ROOT/runtime/supervisor"
if [[ -f "$PIDFILE" ]]; then
  PID=$(<"$PIDFILE")
  COMMAND=$(ps -p "$PID" -o command= 2>/dev/null || true)
  if [[ -n "$COMMAND" && "$COMMAND" == *"$EXPECTED"* ]]; then
    echo "Supervisor already running (PID $PID)"
    exit 0
  fi
  rm -f "$PIDFILE"
fi
PYTHONPATH="$ROOT/control-plane/src" nohup "$PYTHON" -m local_ai_control.supervisor.app daemon </dev/null >/dev/null 2>&1 &
PID=$!
echo "$PID" >"$PIDFILE"
sleep 1
COMMAND=$(ps -p "$PID" -o command= 2>/dev/null || true)
if [[ "$COMMAND" != *"$EXPECTED"* ]]; then
  rm -f "$PIDFILE"
  echo "Supervisor failed to start"
  exit 1
fi
echo "Supervisor started (PID $PID)"

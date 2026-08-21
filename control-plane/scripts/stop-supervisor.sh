#!/bin/zsh
set -eu
PIDFILE=/Users/jerson/AI/runtime/supervisor/supervisor.pid
EXPECTED="local_ai_control.supervisor.app daemon"
[[ -f "$PIDFILE" ]] || { echo "Supervisor stopped"; exit 0; }
PID=$(<"$PIDFILE")
COMMAND=$(ps -p "$PID" -o command= 2>/dev/null || true)
if [[ -z "$COMMAND" ]]; then
  rm -f "$PIDFILE"
  echo "Supervisor stopped"
  exit 0
fi
if [[ "$COMMAND" != *"$EXPECTED"* ]]; then
  echo "Refusing to stop PID $PID: identity mismatch"
  exit 1
fi
kill -TERM "$PID"
for _ in {1..20}; do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
  echo "Supervisor did not stop within timeout"
  exit 1
fi
rm -f "$PIDFILE"
echo "Supervisor stopped"

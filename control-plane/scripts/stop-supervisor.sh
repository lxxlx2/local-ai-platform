#!/bin/zsh
set -eu
umask 077
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
PIDFILE="$ROOT/runtime/supervisor/supervisor.pid"
EXPECTED="local_ai_control.supervisor.app daemon"

[[ -f "$PIDFILE" ]] || { echo "Supervisor stopped"; exit 0; }
set +e
PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity verify >/dev/null 2>&1
VERIFY_RC=$?
set -e
if [[ $VERIFY_RC -eq 2 ]]; then
  PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity cleanup >/dev/null 2>&1 || true
  echo "Supervisor stopped"
  exit 0
fi
if [[ $VERIFY_RC -ne 0 ]]; then
  PID=$(<"$PIDFILE")
  echo "Refusing to stop PID $PID: exact identity mismatch"
  exit 1
fi
PID=$(<"$PIDFILE")
kill -TERM "$PID"
for _ in {1..20}; do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
  echo "Supervisor did not stop within timeout"
  exit 1
fi
PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity cleanup >/dev/null 2>&1 || true
echo "Supervisor stopped"

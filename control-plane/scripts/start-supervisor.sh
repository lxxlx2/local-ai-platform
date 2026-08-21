#!/bin/zsh
set -eu
umask 077
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
RUNTIME="$ROOT/runtime/supervisor"
PIDFILE="$RUNTIME/supervisor.pid"
IDENTITYFILE="$RUNTIME/supervisor.identity.json"
EXPECTED="local_ai_control.supervisor.app daemon"
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"

if [[ -f "$PIDFILE" || -f "$IDENTITYFILE" ]]; then
  set +e
  PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity verify >/dev/null 2>&1
  VERIFY_RC=$?
  set -e
  if [[ $VERIFY_RC -eq 0 ]]; then
    PID=$(<"$PIDFILE")
    echo "Supervisor already running (PID $PID)"
    exit 0
  fi
  if [[ $VERIFY_RC -eq 2 ]]; then
    PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity cleanup
  else
    echo "Refusing to start: existing PID identity mismatch"
    exit 1
  fi
fi

PYTHONPATH="$ROOT/control-plane/src" nohup "$PYTHON" -m local_ai_control.supervisor.app daemon </dev/null >/dev/null 2>&1 &
PID=$!
sleep 1
set +e
PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity write --pid "$PID"
WRITE_RC=$?
set -e
if [[ $WRITE_RC -ne 0 ]]; then
  kill -TERM "$PID" 2>/dev/null || true
  PYTHONPATH="$ROOT/control-plane/src" "$PYTHON" -m local_ai_control.supervisor.process_identity cleanup >/dev/null 2>&1 || true
  echo "Supervisor failed exact identity verification"
  exit 1
fi
chmod 600 "$PIDFILE" "$IDENTITYFILE"
echo "Supervisor started (PID $PID)"

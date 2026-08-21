#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
RUNTIME="$ROOT/runtime/supervisor"
IDENTITY="$RUNTIME/supervisor.identity.json"
export PYTHONPATH="$ROOT/control-plane/src"
mkdir -p "$RUNTIME"
chmod 700 "$RUNTIME"
if [[ -f "$IDENTITY" ]]; then
  set +e
  STATUS=$("$PYTHON" -m local_ai_control.supervisor.process_identity check --file "$IDENTITY" 2>/dev/null)
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    PID=$("$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY")
    echo "Supervisor already running (PID $PID)"
    exit 0
  fi
  if [[ "$STATUS" == "MISMATCH" || "$STATUS" == "INVALID" ]]; then
    echo "Stale supervisor identity did not match a live supervisor; replacing identity metadata only"
  fi
  rm -f "$IDENTITY"
fi
nohup "$PYTHON" -m local_ai_control.supervisor.app daemon </dev/null >/dev/null 2>&1 &
PID=$!
sleep 1
if ! "$PYTHON" -m local_ai_control.supervisor.process_identity capture --pid "$PID" --file "$IDENTITY"; then
  echo "Supervisor failed exact identity verification after start"
  exit 1
fi
echo "Supervisor started (PID $PID)"

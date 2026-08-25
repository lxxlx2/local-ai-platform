#!/bin/zsh
set -eu
umask 077
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
  if [[ $RC -eq 3 ]]; then
    rm -f "$IDENTITY"
  else
    CANDIDATE_PID=$("$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY" 2>/dev/null || echo unknown)
    echo "ORPHAN_RECONCILIATION_REQUIRED PID=$CANDIDATE_PID STATUS=$STATUS"
    exit 1
  fi
fi
nohup "$PYTHON" -m local_ai_control.supervisor.app daemon </dev/null >/dev/null 2>&1 &
PID=$!
sleep 1
set +e
START_ID=$("$PYTHON" -m local_ai_control.supervisor.process_identity start-identity --pid "$PID" 2>/dev/null)
START_RC=$?
set -e
if [[ $START_RC -eq 3 ]]; then
  rm -f "$IDENTITY"
  echo "Supervisor child exited before identity capture (PID $PID)"
  exit 1
fi
if [[ $START_RC -ne 0 || -z "$START_ID" ]]; then
  echo "ORPHAN_RECONCILIATION_REQUIRED PID=$PID"
  exit 1
fi
if ! "$PYTHON" -m local_ai_control.supervisor.process_identity capture --pid "$PID" --start-identity "$START_ID" --file "$IDENTITY"; then
  set +e
  CLEANUP=$("$PYTHON" -m local_ai_control.supervisor.process_identity cleanup-start --pid "$PID" --start-identity "$START_ID" --file "$IDENTITY" 2>/dev/null)
  CLEANUP_RC=$?
  set -e
  if [[ $CLEANUP_RC -ne 0 ]]; then
    echo "ORPHAN_RECONCILIATION_REQUIRED PID=$PID"
    exit 1
  fi
  echo "Supervisor failed identity capture; child cleanup=$CLEANUP"
  exit 1
fi
echo "Supervisor started (PID $PID)"

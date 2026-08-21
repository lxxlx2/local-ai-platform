#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PYTHON="$ROOT/runtime/control-plane-venv/bin/python"
IDENTITY="$ROOT/runtime/supervisor/supervisor.identity.json"
export PYTHONPATH="$ROOT/control-plane/src"
[[ -f "$IDENTITY" ]] || { echo "Supervisor stopped"; exit 0; }
set +e
STATUS=$("$PYTHON" -m local_ai_control.supervisor.process_identity check --file "$IDENTITY" 2>/dev/null)
RC=$?
set -e
if [[ $RC -eq 3 ]]; then
  rm -f "$IDENTITY"
  echo "Supervisor stopped"
  exit 0
fi
if [[ $RC -ne 0 ]]; then
  PID=$("$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY" 2>/dev/null || true)
  echo "Refusing to stop PID ${PID:-unknown}: exact identity mismatch ($STATUS)"
  exit 1
fi
PID=$("$PYTHON" -m local_ai_control.supervisor.process_identity pid --file "$IDENTITY")
kill -TERM "$PID"
for _ in {1..20}; do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
  echo "Supervisor did not stop within timeout"
  exit 1
fi
rm -f "$IDENTITY"
echo "Supervisor stopped"

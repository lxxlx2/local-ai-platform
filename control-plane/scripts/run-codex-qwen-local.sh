#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
export PYTHONPATH="$CONTROL_PLANE_ROOT/src"
export PATH="$LOCAL_AI_ROOT/runtime/control-plane-venv/bin:$PATH"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <feature-worktree-root> [codex args...]" >&2
  exit 2
fi

WORKSPACE=$1
shift

if ! command -v codex >/dev/null 2>&1; then
  echo "CODEX_NOT_FOUND" >&2
  exit 1
fi

VERSION=$(codex --version 2>/dev/null || true)
if [[ "$VERSION" != *"0.148.0"* ]]; then
  echo "CODEX_VERSION_UNQUALIFIED expected=0.148.0 actual=$VERSION" >&2
  exit 1
fi

if ! curl -sf http://127.0.0.1:8010/health >/dev/null; then
  echo "CODEX_QWEN_BRIDGE_UNHEALTHY" >&2
  exit 1
fi

CODEX_HOME=$("$PYTHON" -m local_ai_control.services.codex_qwen_workspace "$WORKSPACE")
export CODEX_HOME

cd "$WORKSPACE"
exec codex -C "$WORKSPACE" -c 'sandbox_workspace_write.network_access=false' "$@"

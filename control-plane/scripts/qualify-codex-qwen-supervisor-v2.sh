#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
CONTROL_PLANE_ROOT=${SCRIPT_DIR:h}
PROJECT_ROOT=${CONTROL_PLANE_ROOT:h}
LOCAL_AI_ROOT=${LOCAL_AI_ROOT:-/Users/jerson/AI}
PYTHON=${LOCAL_AI_CONTROL_PYTHON:-$LOCAL_AI_ROOT/runtime/control-plane-venv/bin/python}
export PYTHONPATH="$CONTROL_PLANE_ROOT/src"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <feature-worktree-root>" >&2
  exit 2
fi

WORKTREE=$1

cd "$CONTROL_PLANE_ROOT"

echo "[1/4] Local Qwen Supervisor focused tests"
"$PYTHON" -m pytest -q tests/test_codex_qwen_supervisor.py

echo "[2/4] Existing V1 focused tests"
"$PYTHON" -m pytest -q tests/test_codex_qwen_bridge.py tests/test_codex_qwen_workspace.py

echo "[3/4] Full control-plane tests"
"$PYTHON" -m pytest -q tests

echo "[4/4] Worktree and production invariants"
"$PYTHON" - "$WORKTREE" <<'PY'
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

root=Path(sys.argv[1]).resolve(strict=True)
branch=subprocess.run(["git","-C",str(root),"branch","--show-current"],capture_output=True,text=True,check=True).stdout.strip()
if not branch or branch in {"main","master"}:
    raise SystemExit("feature worktree required")
with urllib.request.urlopen("http://127.0.0.1:8010/health",timeout=5) as response:
    health=json.load(response)
if health.get("status")!="healthy" or health.get("backend")!="mlx-community/Qwen3.8-27B-8bit" or health.get("tool")!="exec_command":
    raise SystemExit("V1 bridge identity mismatch")
print(f"feature_branch={branch}")
print("bridge_identity=PASS")
PY

git -C "$PROJECT_ROOT" diff --check

cat <<EOF
V2_QUALIFICATION_PASS
feature_root=$WORKTREE
production_activation=DISABLED
real_codex_default=UNCHANGED
merge_deploy=NOT_PERFORMED
EOF

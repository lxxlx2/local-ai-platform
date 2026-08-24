#!/bin/sh
set -eu
parallel=3
if [ "$#" -gt 0 ]; then
  [ "$#" -eq 2 ] && [ "$1" = "--parallel" ] || { echo "usage: $0 [--parallel 2..5]" >&2; exit 2; }
  parallel="$2"
fi
case "$parallel" in 2|3|4|5) ;; *) echo "parallel must be 2..5" >&2; exit 2;; esac
runtime=/Users/jerson/AI/runtime/model-downloads
umask 077
mkdir -p "$runtime"
chmod 700 "$runtime"
nohup /Users/jerson/AI/runtime/control-plane-venv/bin/python /Users/jerson/AI/control-plane/scripts/model-download-queue.py --run --parallel "$parallel" >>"$runtime/manual-manager.stdout.log" 2>&1 </dev/null &
echo "MODEL_DOWNLOAD_MANAGER_STARTED pid=$! parallel=$parallel"

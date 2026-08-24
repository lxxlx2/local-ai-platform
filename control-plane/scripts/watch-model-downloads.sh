#!/bin/sh
set -eu
interval="${1:-5}"
case "$interval" in 5|10) ;; *) echo "usage: $0 [5|10]" >&2; exit 2;; esac
exec /Users/jerson/AI/runtime/control-plane-venv/bin/python /Users/jerson/AI/control-plane/scripts/watch-model-downloads.py "$interval"

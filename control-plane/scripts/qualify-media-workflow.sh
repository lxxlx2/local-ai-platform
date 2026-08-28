#!/bin/sh
set -eu
exec /Users/jerson/AI/runtime/control-plane-venv/bin/python \
  /Users/jerson/AI/control-plane/scripts/qualify-media-workflow.py "$@"

#!/bin/sh
set -eu
uid="$(id -u)"
label="local-ai.model-download-queue"
plist="$(/Users/jerson/AI/runtime/control-plane-venv/bin/python /Users/jerson/AI/control-plane/scripts/model-download-queue.py --write-launch-plist)"
if launchctl print "gui/${uid}/${label}" >/dev/null 2>&1; then
  launchctl kickstart "gui/${uid}/${label}"
else
  launchctl bootstrap "gui/${uid}" "${plist}"
fi

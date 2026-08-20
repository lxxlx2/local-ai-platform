#!/bin/zsh
set -eu
PIDFILE=/Users/jerson/AI/runtime/control-plane/telegram-bot.pid
[[ -f "$PIDFILE" ]] || { echo "Bot not running"; exit 0; }
PID=$(<"$PIDFILE")
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; echo "Bot stopped"; fi
rm -f "$PIDFILE"

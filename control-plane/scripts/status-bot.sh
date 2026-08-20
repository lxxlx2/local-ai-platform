#!/bin/zsh
ROOT=/Users/jerson/AI
PIDFILE="$ROOT/runtime/control-plane/telegram-bot.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then echo "Bot Running: PID $(<"$PIDFILE")"; else echo "Bot not running"; fi
curl -sf http://127.0.0.1:8000/health >/dev/null && echo "oMLX health: PASS" || echo "oMLX health: FAIL"

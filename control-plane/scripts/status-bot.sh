#!/bin/zsh
ROOT=/Users/jerson/AI
PIDFILE="$ROOT/runtime/control-plane/telegram-bot.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then echo "Bot Running: PID $(<"$PIDFILE")"; else echo "Bot not running"; fi
curl -sf http://127.0.0.1:8001/health >/dev/null && echo "MAIN Qwen3.8 health: PASS" || echo "MAIN Qwen3.8 health: FAIL"
curl -sf http://127.0.0.1:8000/health >/dev/null && echo "FAST Qwen3.6 health: PASS (resident)" || echo "FAST Qwen3.6 health: STOPPED"

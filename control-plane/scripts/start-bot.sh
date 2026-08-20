#!/bin/zsh
set -eu
ROOT=/Users/jerson/AI
PIDFILE="$ROOT/runtime/control-plane/telegram-bot.pid"
LOG="$ROOT/runtime/control-plane/telegram-bot.log"
VENV="$ROOT/runtime/control-plane-venv"
mkdir -p "$ROOT/runtime/control-plane"
if [[ -f "$PIDFILE" ]] && kill -0 "$(<"$PIDFILE")" 2>/dev/null; then echo "Bot already running"; exit 0; fi
rm -f "$PIDFILE"
set -a; source "$ROOT/runtime/secrets/telegram-bot.env"; set +a
PYTHONPATH="$ROOT/control-plane/src" nohup "$VENV/bin/python" -m local_ai_control.bot.app >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "Bot started (PID $!)"

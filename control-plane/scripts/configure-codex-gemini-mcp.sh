#!/bin/zsh
set -eu
umask 077

SCRIPT_DIR=${0:A:h}
MCP_LAUNCHER="$SCRIPT_DIR/run-gemini-review-mcp.sh"
CODEX_BIN=${CODEX_BIN:-$(command -v codex)}

[[ -n "$CODEX_BIN" && -x "$CODEX_BIN" ]] || {
  echo "CODEX_NOT_FOUND" >&2
  exit 1
}
[[ -f "$MCP_LAUNCHER" && ! -L "$MCP_LAUNCHER" ]] || {
  echo "GEMINI_REVIEW_MCP_LAUNCHER_UNSAFE" >&2
  exit 1
}

if "$CODEX_BIN" mcp get localGeminiReviewer >/dev/null 2>&1; then
  "$CODEX_BIN" mcp remove localGeminiReviewer >/dev/null
fi

"$CODEX_BIN" mcp add localGeminiReviewer -- /bin/zsh "$MCP_LAUNCHER"
"$CODEX_BIN" mcp get localGeminiReviewer --json

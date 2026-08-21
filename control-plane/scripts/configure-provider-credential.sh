#!/bin/sh
# macOS-local credential setup. It intentionally receives no value via argv.
set -eu
alias_name=${1:-}
case "$alias_name" in
  ''|*[!a-z0-9-]*) echo 'Usage: configure-provider-credential.sh provider-alias' >&2; exit 2 ;;
esac
echo 'Enter the credential in the macOS Keychain prompt (input is hidden).' >&2
# The platform's secure prompt keeps the credential out of argv, shell history,
# pipes, files, and this script's output.
security add-generic-password -U -a "$USER" -s "local-ai-platform.provider.$alias_name" -w
echo 'Credential configured: YES'

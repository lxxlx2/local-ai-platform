from __future__ import annotations

import getpass
import subprocess


class ProviderCredentialError(RuntimeError):
    pass


def keychain_service_name(alias: str) -> str:
    if not alias or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in alias):
        raise ValueError("invalid provider alias")
    return f"local-ai-platform.provider.{alias}"


def read_keychain_secret(alias: str) -> str:
    """Read a provider credential from macOS Keychain without shell expansion."""
    service = keychain_service_name(alias)
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-w",
            "-a",
            getpass.getuser(),
            "-s",
            service,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        shell=False,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise ProviderCredentialError(f"credential unavailable for provider alias: {alias}")
    secret = result.stdout.rstrip("\r\n")
    if not secret:
        raise ProviderCredentialError(f"empty credential for provider alias: {alias}")
    return secret

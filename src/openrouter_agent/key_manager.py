"""
Secure API Key Manager

Tiered key storage: CLI flag → ENV var → keyring → prompt user.
"""

from __future__ import annotations

import os
from typing import Optional

import keyring
from rich.console import Console

SERVICE_NAME = "openrouter-cli-agent"
KEY_ACCOUNT = "api_key"
ENV_VAR = "OPENROUTER_API_KEY"

console = Console()


class APIKeyManager:
    def __init__(self, explicit_key: Optional[str] = None):
        self._explicit_key = explicit_key

    def get_api_key(self) -> Optional[str]:
        if self._explicit_key:
            return self._explicit_key
        key = os.getenv(ENV_VAR)
        if key:
            return key
        key = self._read_keychain()
        if key:
            return key
        return self._prompt_and_store()

    def set_api_key(self, key: str) -> None:
        keyring.set_password(SERVICE_NAME, KEY_ACCOUNT, key)

    def delete_api_key(self) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, KEY_ACCOUNT)
        except Exception:
            pass

    @staticmethod
    def mask(key: str) -> str:
        if len(key) < 12:
            return "****"
        return f"{key[:8]}…****…{key[-4:]}"

    @staticmethod
    def sanitize_env() -> dict[str, str]:
        env = dict(os.environ)
        env.pop(ENV_VAR, None)
        return env

    @staticmethod
    def _read_keychain() -> Optional[str]:
        try:
            return keyring.get_password(SERVICE_NAME, KEY_ACCOUNT)
        except Exception:
            return None

    def _prompt_and_store(self) -> Optional[str]:
        console.print(
            "\n[bold]Welcome to OpenRouter Agent![/bold]\n"
            "Get a key at [link=https://openrouter.ai/keys]openrouter.ai/keys[/link]\n"
        )
        try:
            key = console.input("[bold]Enter your OpenRouter API key: [/bold]").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not key:
            return None
        try:
            keyring.set_password(SERVICE_NAME, KEY_ACCOUNT, key)
            console.print(f"[green]✓ API key stored in system keychain[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠ Keychain unavailable ({e})[/yellow]")
        return key

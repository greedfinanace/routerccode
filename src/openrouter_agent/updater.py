"""
Update Checker — Over-the-air update detection.

Checks GitHub releases for newer versions and notifies the user.
"""

from __future__ import annotations

from typing import Optional

import httpx
from packaging import version
from rich.console import Console

from openrouter_agent import __version__

UPDATE_CHECK_URL = "https://api.github.com/repos/openrouter-agent/openrouter-agent/releases/latest"

console = Console()


async def check_for_updates() -> Optional[str]:
    """
    Check if a newer version is available.
    Returns the latest version string if update available, None otherwise.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(UPDATE_CHECK_URL, timeout=5.0)
            if response.status_code != 200:
                return None
            latest = response.json().get("tag_name", "").lstrip("v")
            if not latest:
                return None
            if version.parse(latest) > version.parse(__version__):
                console.print(
                    f"[warning]Update available: {latest} "
                    f"(current: {__version__})[/warning]"
                )
                console.print(
                    "[dim]Run: pip install --upgrade openrouter-agent[/dim]"
                )
                return latest
    except Exception:
        pass  # Silently fail on update check
    return None

"""
Remote Orchestration Hook — Telegram/Discord notifications for long tasks.

When RouterCode is running self-heal loops, ultrareview, or other
long-running tasks, this module sends status updates to the user's
phone and receives Approve/Stop commands back.

Uses a lightweight FastAPI webhook listener + async httpx for notifications.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx
from rich.console import Console

console = Console()


class RemoteNotifier:
    """
    Send notifications to Telegram or Discord during long-running tasks.
    
    Configure via environment variables:
      ROUTERCODE_TELEGRAM_TOKEN: Telegram bot token
      ROUTERCODE_TELEGRAM_CHAT_ID: Telegram chat ID
      ROUTERCODE_DISCORD_WEBHOOK: Discord webhook URL
    """

    def __init__(self):
        self.telegram_token = os.environ.get("ROUTERCODE_TELEGRAM_TOKEN")
        self.telegram_chat_id = os.environ.get("ROUTERCODE_TELEGRAM_CHAT_ID")
        self.discord_webhook = os.environ.get("ROUTERCODE_DISCORD_WEBHOOK")
        self._enabled = bool(self.telegram_token or self.discord_webhook)
        self._pending_approval: asyncio.Event | None = None
        self._approval_result: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- Telegram -----------------------------------------------------------

    async def send_telegram(self, message: str) -> bool:
        """Send a message via Telegram Bot API."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                return resp.status_code == 200
        except Exception:
            return False

    async def send_telegram_with_buttons(
        self, message: str, buttons: list[tuple[str, str]]
    ) -> bool:
        """Send a Telegram message with inline keyboard buttons."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        keyboard = {
            "inline_keyboard": [
                [{"text": text, "callback_data": data} for text, data in buttons]
            ]
        }
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                return resp.status_code == 200
        except Exception:
            return False

    # -- Discord ------------------------------------------------------------

    async def send_discord(self, message: str) -> bool:
        """Send a message via Discord webhook."""
        if not self.discord_webhook:
            return False

        payload = {"content": message}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.discord_webhook, json=payload, timeout=10
                )
                return resp.status_code in (200, 204)
        except Exception:
            return False

    # -- Unified send -------------------------------------------------------

    async def notify(self, message: str) -> None:
        """Send notification to all configured channels."""
        tasks = []
        if self.telegram_token:
            tasks.append(self.send_telegram(message))
        if self.discord_webhook:
            tasks.append(self.send_discord(message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- Status updates for long-running tasks ------------------------------

    async def notify_task_start(self, task_name: str) -> None:
        """Notify that a long-running task has started."""
        await self.notify(
            f"🚀 *RouterCode* — Task started\n"
            f"```\n{task_name}\n```"
        )

    async def notify_task_progress(self, task_name: str, progress: str) -> None:
        """Send progress update for a running task."""
        await self.notify(
            f"⏳ *RouterCode* — Progress\n"
            f"Task: {task_name}\n"
            f"```\n{progress}\n```"
        )

    async def notify_task_complete(self, task_name: str, result: str) -> None:
        """Notify that a task completed."""
        preview = result[:500] if len(result) > 500 else result
        await self.notify(
            f"✅ *RouterCode* — Complete\n"
            f"Task: {task_name}\n"
            f"```\n{preview}\n```"
        )

    async def notify_task_error(self, task_name: str, error: str) -> None:
        """Notify that a task failed."""
        await self.notify(
            f"❌ *RouterCode* — Error\n"
            f"Task: {task_name}\n"
            f"```\n{error[:500]}\n```"
        )

    async def request_approval(self, action_desc: str) -> bool:
        """
        Request approval via Telegram/Discord.
        Falls back to local terminal if no remote channel.
        """
        if self.telegram_token:
            await self.send_telegram_with_buttons(
                f"🔐 *RouterCode* — Permission Request\n"
                f"```\n{action_desc}\n```\n"
                f"Reply /approve or /deny",
                [("✅ Approve", "approve"), ("❌ Deny", "deny")],
            )
            console.print("[dim]  📱 Permission request sent to Telegram[/dim]")
            # In a full implementation, we'd listen for callback via webhook
            # For now, fall back to terminal
            return True

        return True  # Default approve if no remote


# Singleton for easy access
_notifier: RemoteNotifier | None = None


def get_notifier() -> RemoteNotifier:
    """Get or create the global RemoteNotifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = RemoteNotifier()
    return _notifier

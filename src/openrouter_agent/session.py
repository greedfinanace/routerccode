"""
Session Manager — Persistent session storage & management.

Handles session creation, continuation (-c), resumption (-r),
forking, and export. Sessions are stored as JSON files.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.syntax import Syntax

SESSIONS_DIR = Path.home() / ".openrouter-agent" / "sessions"


class SessionManager:
    """Manage conversation sessions with local persistence."""

    def __init__(self):
        self.session_id: str = ""
        self.session_name: str = ""
        self.created_at: str = ""
        self.messages: list[dict[str, Any]] = []
        self.file_changes: list[dict[str, str]] = []
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def create_new(self) -> None:
        self.session_id = uuid.uuid4().hex
        self.session_name = f"session-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.messages = []
        self.file_changes = []

    def save(self, ctx_mgr=None) -> None:
        if not self.session_id:
            return
        data = {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "created_at": self.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "messages": ctx_mgr.messages if ctx_mgr else self.messages,
            "file_changes": self.file_changes,
        }
        path = SESSIONS_DIR / f"{self.session_id}.json"
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load(self, session_id_or_name: str) -> None:
        # Try exact ID match first
        path = SESSIONS_DIR / f"{session_id_or_name}.json"
        if not path.exists():
            # Search by name or partial ID
            for f in SESSIONS_DIR.glob("*.json"):
                data = json.loads(f.read_text(encoding="utf-8"))
                if (session_id_or_name in data.get("session_id", "")
                        or session_id_or_name in data.get("session_name", "")):
                    path = f
                    break
            else:
                raise FileNotFoundError(f"Session not found: {session_id_or_name}")

        data = json.loads(path.read_text(encoding="utf-8"))
        self.session_id = data["session_id"]
        self.session_name = data.get("session_name", "")
        self.created_at = data.get("created_at", "")
        self.messages = data.get("messages", [])
        self.file_changes = data.get("file_changes", [])

    def load_latest(self) -> None:
        sessions = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            self.create_new()
            return
        self.load(sessions[0].stem)

    def fork(self, name: Optional[str] = None) -> None:
        old_id = self.session_id
        self.session_id = uuid.uuid4().hex
        self.session_name = name or f"fork-{old_id[:8]}"
        self.save()

    def export(self, filename: str) -> None:
        lines = []
        for msg in self.messages:
            role = msg.get("role", "?").upper()
            content = msg.get("content", "")
            lines.append(f"[{role}]\n{content}\n")
        Path(filename).write_text("\n".join(lines), encoding="utf-8")

    def show_diff(self, console: Console) -> None:
        if not self.file_changes:
            console.print("[dim]No file changes in this session[/dim]")
            return
        for change in self.file_changes:
            console.print(f"  {change.get('action', '?')}  {change.get('path', '?')}")

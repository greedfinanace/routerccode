"""
Memory Manager — Persistent Project Memory ("Shared Brain").

Implements the 2026 persistence paradigm:
  - ROUTERCODE.md: Manual project DNA (build commands, style rules, architecture)
  - MEMORY.md: Agent's automated notes (auto-discovered insights)
  - Auto-Dream: Background summarization when MEMORY.md exceeds 200 lines
  - /memo slash command: Manual insight appending
  - /init bootstrap: Agent interviews codebase to generate ROUTERCODE.md
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()

MAX_MEMORY_LINES = 200
MAX_MEMORY_BYTES = 25_000


class MemoryManager:
    """Manages persistent project memory across sessions."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path.cwd()
        self.routercode_md: str = ""
        self.memory_md: str = ""
        self._routercode_path = self.project_root / "ROUTERCODE.md"
        self._memory_path = self.project_root / "MEMORY.md"

    # -- Loading ------------------------------------------------------------

    def load(self) -> str:
        """Load all memory files and return combined context for the LLM."""
        context_parts = []

        # Load ROUTERCODE.md (manual project DNA)
        self.routercode_md = self._load_file(self._routercode_path)
        if self.routercode_md:
            context_parts.append(
                f"## Project DNA (ROUTERCODE.md)\n{self.routercode_md}"
            )

        # Walk upward for parent ROUTERCODE.md files (monorepo support)
        parent = self.project_root.parent
        while parent != parent.parent:
            parent_rc = parent / "ROUTERCODE.md"
            if parent_rc.exists():
                parent_content = self._load_file(parent_rc)
                if parent_content:
                    context_parts.insert(0, f"## Root Project DNA ({parent_rc.relative_to(parent.parent)})\n{parent_content}")
                break
            parent = parent.parent

        # Load MEMORY.md (agent's automated notes)
        self.memory_md = self._load_file(self._memory_path)
        if self.memory_md:
            context_parts.append(
                f"## Agent Memory (MEMORY.md)\n{self.memory_md}"
            )

        # Load .routercode.local.md (personal, gitignored)
        local_path = self.project_root / ".routercode.local.md"
        local_content = self._load_file(local_path)
        if local_content:
            context_parts.append(
                f"## Local Overrides (.routercode.local.md)\n{local_content}"
            )

        return "\n\n".join(context_parts) if context_parts else ""

    @staticmethod
    def _load_file(path: Path) -> str:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")[:MAX_MEMORY_BYTES]
            except Exception:
                return ""
        return ""

    # -- Writing to MEMORY.md -----------------------------------------------

    def add_memory(self, insight: str, category: str = "general") -> None:
        """Append an insight to MEMORY.md with timestamp and category."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{timestamp}] **{category}**: {insight}\n"

        # Append to file
        with self._memory_path.open("a", encoding="utf-8") as f:
            if not self._memory_path.exists() or self._memory_path.stat().st_size == 0:
                f.write("# RouterCode Agent Memory\n\n")
                f.write("_Auto-generated insights from RouterCode sessions._\n\n")
            f.write(entry)

        # Reload
        self.memory_md = self._load_file(self._memory_path)

    def needs_dream(self) -> bool:
        """Check if MEMORY.md needs Auto-Dream summarization."""
        if not self._memory_path.exists():
            return False
        lines = self._memory_path.read_text(encoding="utf-8").splitlines()
        return len(lines) > MAX_MEMORY_LINES

    # -- Auto-Dream ---------------------------------------------------------

    async def auto_dream(self, client=None) -> None:
        """
        Summarize MEMORY.md when it exceeds 200 lines.
        Uses LLM to consolidate, resolve contradictions, and compress.
        """
        if not self.needs_dream():
            return

        content = self._memory_path.read_text(encoding="utf-8")

        if client:
            try:
                summary = await client.chat([
                    {"role": "system", "content": (
                        "You are summarizing an AI agent's memory notes. "
                        "Consolidate duplicate insights. Resolve contradictions "
                        "(keep the most recent version). Remove completed items. "
                        "Keep the result under 100 lines. Use markdown bullets. "
                        "Preserve categories and key technical decisions."
                    )},
                    {"role": "user", "content": content},
                ])
                # Write consolidated memory
                self._memory_path.write_text(
                    f"# RouterCode Agent Memory\n\n"
                    f"_Last consolidated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
                    f"{summary}\n",
                    encoding="utf-8",
                )
                console.print("[success]✓ Memory consolidated via Auto-Dream[/success]")
            except Exception as e:
                console.print(f"[warning]Auto-Dream failed: {e}[/warning]")
                self._simple_trim()
        else:
            self._simple_trim()

    def _simple_trim(self) -> None:
        """Fallback: keep only the last MAX_MEMORY_LINES/2 lines."""
        lines = self._memory_path.read_text(encoding="utf-8").splitlines()
        keep = lines[:4] + lines[-(MAX_MEMORY_LINES // 2):]  # header + recent
        self._memory_path.write_text("\n".join(keep), encoding="utf-8")

    # -- Bootstrap (/init) --------------------------------------------------

    def generate_routercode_template(self) -> str:
        """Generate a starter ROUTERCODE.md by scanning the project."""
        template = "# ROUTERCODE.md — Project DNA\n\n"
        template += "_This file is read by RouterCode at the start of every session._\n\n"

        # Detect build system
        template += "## Build & Test\n"
        if (self.project_root / "package.json").exists():
            template += "- Install: `npm install`\n"
            template += "- Test: `npm test`\n"
            template += "- Dev: `npm run dev`\n"
        elif (self.project_root / "pyproject.toml").exists():
            template += "- Install: `pip install -e '.[dev]'`\n"
            template += "- Test: `pytest tests/ -v`\n"
        elif (self.project_root / "Cargo.toml").exists():
            template += "- Build: `cargo build`\n"
            template += "- Test: `cargo test`\n"
        elif (self.project_root / "go.mod").exists():
            template += "- Build: `go build ./...`\n"
            template += "- Test: `go test ./...`\n"
        else:
            template += "- TODO: Add build/test commands\n"

        template += "\n## Architecture\n"
        template += "- TODO: Document key modules and their purposes\n"

        template += "\n## Style Rules\n"
        template += "- TODO: Add coding conventions and preferences\n"

        template += "\n## Important Notes\n"
        template += "- TODO: Add architectural decisions and gotchas\n"

        return template

    def init_project(self) -> None:
        """Bootstrap ROUTERCODE.md for a new project."""
        if self._routercode_path.exists():
            console.print("[warning]ROUTERCODE.md already exists. Use /memo to add notes.[/warning]")
            return

        template = self.generate_routercode_template()
        self._routercode_path.write_text(template, encoding="utf-8")
        console.print(f"[success]✓ Created {self._routercode_path}[/success]")
        console.print("[dim]  Edit this file to teach RouterCode your project's DNA.[/dim]")

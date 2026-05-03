"""
Context Manager - Token management & 4-layer compression pipeline.

Implements the multi-layer compaction strategy from the master prompt:
  Layer 1: Deterministic micro-compaction (truncation)
  Layer 2: Budget reduction (output limits)
  Layer 3: Snipping old history
  Layer 4: LLM-generated auto-compaction
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

import tiktoken
from rich.console import Console
from rich.table import Table
from rich.progress_bar import ProgressBar

SYSTEM_PROMPT = """You are an expert coding assistant operating through the OpenRouter Agent CLI.
You have access to tools for reading files, editing files, running commands, and searching codebases.
Always reason step-by-step before acting. Use tools surgically - prefer targeted edits over full rewrites.
When you're done with a task, explain what you did concisely."""

MAX_CONTEXT_TOKENS = 128_000
COMPACT_THRESHOLD = 0.80  # trigger at 80%
MAX_TOOL_OUTPUT_TOKENS = 2000
KEEP_RECENT_TURNS = 20


class ContextManager:
    """Manages conversation context with multi-layer compression."""

    def __init__(self, session=None):
        self.messages: list[dict[str, Any]] = []
        self.tool_definitions: list[dict[str, Any]] = []
        self.agent_md: str = ""
        self.total_tokens_used: int = 0
        self.session = session
        self._encoder = tiktoken.get_encoding("cl100k_base")
        self._lock = threading.Lock()
        self._prebuilt_summary: Optional[str] = None

        # Load agent.md if present
        self._load_agent_md()

    def _load_agent_md(self) -> None:
        """Load .agent.md from working directory if it exists."""
        from pathlib import Path
        agent_file = Path.cwd() / ".agent.md"
        if agent_file.exists():
            self.agent_md = agent_file.read_text(encoding="utf-8")[:4000]

    # -- Token counting -----------------------------------------------------

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def context_token_count(self) -> int:
        total = self.count_tokens(SYSTEM_PROMPT)
        total += self.count_tokens(self.agent_md)
        total += self.count_tokens(json.dumps(self.tool_definitions))
        for msg in self.messages:
            total += self.count_tokens(str(msg.get("content", "")))
        return total

    def is_over_budget(self) -> bool:
        return self.context_token_count() > int(MAX_CONTEXT_TOKENS * COMPACT_THRESHOLD)

    # -- Message management -------------------------------------------------

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str, tool_calls: list[dict[str, Any]] | None = None) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc.get("arguments", json.dumps(tc.get("parameters", {})))
                    }
                }
                for i, tc in enumerate(tool_calls)
            ]
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, tool_name: str, result: str) -> None:
        truncated = self._truncate_output(result)
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": truncated,
        })

    def clear(self) -> None:
        self.messages.clear()
        self._prebuilt_summary = None

    # -- Layer 1: Deterministic micro-compaction ----------------------------

    @staticmethod
    def _truncate_output(output: str, max_chars: int = 8000) -> str:
        if len(output) <= max_chars:
            return output
        half = max_chars // 2
        return (
            f"{output[:half]}\n\n"
            f"[... truncated {len(output):,} chars ...]\n\n"
            f"{output[-half:]}"
        )

    # -- Layer 3: Snip old turns -------------------------------------------

    def _snip_old_turns(self) -> None:
        if len(self.messages) <= KEEP_RECENT_TURNS:
            return
        snipped = len(self.messages) - KEEP_RECENT_TURNS
        self.messages = [
            {"role": "system", "content": f"[... {snipped} earlier turns truncated ...]"},
            *self.messages[-KEEP_RECENT_TURNS:],
        ]

    # -- Layer 4: LLM-generated compaction ---------------------------------

    async def compact(
        self,
        client=None,
        instructions: str | None = None,
    ) -> None:
        """Full context compaction with optional focus instructions."""
        # Use pre-built summary if available
        with self._lock:
            if self._prebuilt_summary:
                self.messages = [
                    {"role": "system", "content": f"Previous session summary:\n{self._prebuilt_summary}"},
                ]
                self._prebuilt_summary = None
                return

        # If we have a client, use LLM to summarize
        if client and len(self.messages) > 4:
            summary_prompt = [
                {"role": "system", "content": (
                    "Summarize the conversation below into a concise session summary. "
                    "Omit system context. Keep under 500 words. "
                    "Prioritize user corrections over errors. "
                    "Prioritize active work over completed work."
                )},
                {"role": "user", "content": json.dumps(self.messages[-30:], default=str)},
            ]
            if instructions:
                summary_prompt[0]["content"] += f"\nFocus: {instructions}"

            try:
                summary = await client.chat(summary_prompt)
                self.messages = [
                    {"role": "system", "content": f"Previous session summary:\n{summary}"},
                ]
                return
            except Exception:
                pass

        # Fallback: simple snipping
        self._snip_old_turns()

    def start_background_compaction(self, client) -> None:
        """Build summary in background while user continues working."""
        thread = threading.Thread(
            target=self._background_compact,
            args=(client,),
            daemon=True,
        )
        thread.start()

    def _background_compact(self, client) -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            summary_prompt = [
                {"role": "system", "content": "Summarize concisely. Under 500 words."},
                {"role": "user", "content": json.dumps(self.messages[-30:], default=str)},
            ]
            summary = loop.run_until_complete(client.chat(summary_prompt))
            with self._lock:
                self._prebuilt_summary = summary
        except Exception:
            pass
        finally:
            loop.close()

    # -- Payload building ---------------------------------------------------

    def build_payload(self) -> dict[str, Any]:
        """Build API payload with optimal caching structure."""
        messages = []

        # Static content first (cached)
        messages.append({"role": "system", "content": SYSTEM_PROMPT})

        if self.agent_md:
            messages.append({"role": "system", "content": f"Project context:\n{self.agent_md}"})

        if self.tool_definitions:
            messages.append({
                "role": "system",
                "content": f"Available tools:\n{json.dumps(self.tool_definitions)}",
            })

        # Conversation history
        messages.extend(self.messages)

        return {
            "messages": messages,
            "tools": self.tool_definitions if self.tool_definitions else None,
        }

    # -- Visualization ------------------------------------------------------

    def visualize(self, console: Console) -> None:
        current = self.context_token_count()
        pct = current / MAX_CONTEXT_TOKENS * 100

        table = Table(title="Context Window Usage")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")
        table.add_row("Current tokens", f"{current:,}")
        table.add_row("Max tokens", f"{MAX_CONTEXT_TOKENS:,}")
        table.add_row("Usage", f"{pct:.1f}%")
        table.add_row("Messages", str(len(self.messages)))
        table.add_row("Compact threshold", f"{COMPACT_THRESHOLD * 100:.0f}%")

        color = "green" if pct < 60 else "yellow" if pct < 80 else "red"
        table.add_row("Status", f"[{color}]{'OK' if pct < 80 else 'COMPACT SOON'}[/{color}]")

        console.print(table)

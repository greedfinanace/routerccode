"""
Intent Security Classifier - Two-stage Auto Mode safety layer.

Implements the intent-based security model from the 2026 paradigm:
  Stage 1: Fast filter (~64 tokens) - block obvious dangers
  Stage 2: Contextual LLM check (~4K tokens) - intent alignment
  
The classifier answers: "Does this action serve the user's request?"
If yes -> execute. If no -> fall back to ask-first mode.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel

console = Console()


# ---------------------------------------------------------------------------
# Stage 1: Fast deterministic filter
# ---------------------------------------------------------------------------

# Commands that are ALWAYS blocked regardless of context
HARD_BLOCK_PATTERNS = [
    r"rm\s+-rf\s+/\s*$",           # rm -rf /
    r"rm\s+-rf\s+/\*",             # rm -rf /*
    r"rm\s+-rf\s+~",               # rm -rf ~
    r"mkfs\.",                       # mkfs.ext4 etc
    r"dd\s+if=.*of=/dev/",         # dd to raw device
    r":\(\)\{.*\}",                 # fork bomb
    r"chmod\s+-R\s+777\s+/",       # chmod 777 everything
    r"curl.*\|\s*(bash|sh)",       # pipe curl to shell (from unknown)
    r"wget.*\|\s*(bash|sh)",       # pipe wget to shell
    r"git\s+push\s+.*--force\s+.*main",    # force push to main
    r"git\s+push\s+.*--force\s+.*master",  # force push to master
    r"DROP\s+DATABASE",            # SQL drop database
    r"DROP\s+TABLE",               # SQL drop table
    r"TRUNCATE\s+TABLE",           # SQL truncate
    r"shutdown\s",                  # system shutdown
    r"reboot",                      # system reboot
    r"format\s+[cC]:",            # Windows format C:
    r"del\s+/[sS]\s+/[qQ]\s+[cC]:", # Windows recursive delete C:
]

# File paths that should never be written to
PROTECTED_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/hosts",
    "C:\\Windows\\System32",
    ".env",  # Never overwrite env files without asking
    ".git/config",
]

# Commands considered "safe" in read-only context
SAFE_READONLY_COMMANDS = [
    "ls", "dir", "cat", "head", "tail", "grep", "find", "wc",
    "git status", "git log", "git diff", "git branch",
    "python --version", "node --version", "npm --version",
    "pip list", "pip show", "type", "more",
]


class IntentSecurityClassifier:
    """Two-stage security classifier for Auto Mode."""

    def __init__(self):
        self._block_patterns = [re.compile(p, re.IGNORECASE) for p in HARD_BLOCK_PATTERNS]

    # -- Stage 1: Fast filter (deterministic, ~0ms) -------------------------

    def fast_filter(self, tool_name: str, params: dict[str, Any]) -> tuple[str, str]:
        """
        Stage 1: Lightweight deterministic check.
        Returns: ("allow" | "block" | "review", reason)
        """
        if tool_name in ("read_file", "list_directory", "search_files"):
            return ("allow", "Read-only operation")

        if tool_name == "run_command":
            command = params.get("command", "")
            return self._check_command(command)

        if tool_name in ("write_file", "apply_diff", "edit_lines"):
            file_path = params.get("file_path", "")
            return self._check_file_write(file_path)

        return ("review", f"Unknown tool: {tool_name}")

    def _check_command(self, command: str) -> tuple[str, str]:
        """Check a shell command against block patterns."""
        # Check hard blocks
        for pattern in self._block_patterns:
            if pattern.search(command):
                return ("block", f"Blocked by security rule: {pattern.pattern}")

        # Check if it's a known safe command
        cmd_lower = command.strip().lower()
        for safe in SAFE_READONLY_COMMANDS:
            if cmd_lower.startswith(safe):
                return ("allow", "Known safe read-only command")

        # Everything else needs contextual review
        return ("review", "Command requires intent verification")

    def _check_file_write(self, file_path: str) -> tuple[str, str]:
        """Check if writing to a file path is safe."""
        for protected in PROTECTED_PATHS:
            if protected.lower() in file_path.lower():
                return ("block", f"Protected path: {protected}")

        # Writing to project files is generally okay, but needs context check
        return ("review", "File write requires intent verification")

    # -- Stage 2: LLM intent check (~4K tokens) ----------------------------

    async def intent_check(
        self,
        tool_name: str,
        params: dict[str, Any],
        user_request: str,
        client=None,
    ) -> tuple[bool, str]:
        """
        Stage 2: Ask the LLM whether this action serves the user's intent.
        Returns: (approved: bool, reasoning: str)
        """
        if not client:
            return (False, "No LLM available for intent check")

        action_desc = self._describe_action(tool_name, params)

        try:
            response = await client.chat([
                {"role": "system", "content": (
                    "You are a security classifier. Given the user's request and "
                    "a proposed tool action, determine if the action directly serves "
                    "the user's stated intent. Respond with ONLY 'APPROVE' or 'DENY' "
                    "on the first line, followed by a brief reason on the second line."
                )},
                {"role": "user", "content": (
                    f"User's request: {user_request}\n\n"
                    f"Proposed action: {action_desc}\n\n"
                    "Does this action directly serve the user's request?"
                )},
            ], model_override="openai/gpt-4o-mini")  # Use cheap model for classification

            lines = response.strip().split("\n", 1)
            decision = lines[0].strip().upper()
            reason = lines[1].strip() if len(lines) > 1 else ""

            if "APPROVE" in decision:
                return (True, reason)
            else:
                return (False, reason)

        except Exception as e:
            return (False, f"Intent check failed: {e}")

    @staticmethod
    def _describe_action(tool_name: str, params: dict) -> str:
        """Create a human-readable description of the action."""
        if tool_name == "run_command":
            return f"Execute shell command: `{params.get('command', '?')}`"
        elif tool_name == "write_file":
            return f"Write {len(params.get('content', '')):,} chars to: {params.get('file_path', '?')}"
        elif tool_name == "apply_diff":
            return f"Edit file {params.get('file_path', '?')}: replace text"
        elif tool_name == "edit_lines":
            return f"Edit lines {params.get('start_line')}-{params.get('end_line')} in {params.get('file_path', '?')}"
        return f"{tool_name}: {params}"

    # -- Combined check (used by the agentic loop) -------------------------

    async def check(
        self,
        tool_name: str,
        params: dict[str, Any],
        user_request: str,
        client=None,
    ) -> tuple[str, str]:
        """
        Full two-stage security check.
        Returns: ("allow" | "block" | "ask", reason)
        """
        # Stage 1
        verdict, reason = self.fast_filter(tool_name, params)

        if verdict == "allow":
            return ("allow", reason)
        if verdict == "block":
            console.print(f"[error][SAFE] BLOCKED: {reason}[/error]")
            return ("block", reason)

        # Stage 2 - only for "review" verdicts
        approved, intent_reason = await self.intent_check(
            tool_name, params, user_request, client
        )

        if approved:
            return ("allow", f"Intent verified: {intent_reason}")
        else:
            return ("ask", f"Intent unclear: {intent_reason}")

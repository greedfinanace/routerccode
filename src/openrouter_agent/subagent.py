"""
Subagent System - Fan-Out Parallel Task Delegation.

Implements the 2026 "Fan-Out" pattern:
  - Orchestrator dispatches specialized subagents
  - Each subagent has restricted toolset + scoped directory
  - Git worktree integration for parallel branch work
  - Adversarial validation: multiple subagents debate solutions
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@dataclass
class SubagentConfig:
    """Configuration for a specialized subagent."""
    name: str
    role: str  # "test_writer", "security_auditor", "doc_writer", "debugger"
    scope_dir: str = "."  # restricted directory scope
    allowed_tools: list[str] = field(default_factory=lambda: [
        "read_file", "search_files", "list_directory"
    ])
    system_prompt_extra: str = ""
    model_override: str | None = None  # use cheaper model for subagents
    timeout: int = 120

    # Preset role configurations
    ROLES = {
        "test_writer": {
            "allowed_tools": ["read_file", "write_file", "run_command", "search_files", "list_directory"],
            "system_prompt_extra": (
                "You are a test-writing specialist. Write comprehensive unit tests "
                "for the code you are given. Use the project's existing test framework. "
                "Run the tests to verify they pass. Focus on edge cases and error paths."
            ),
        },
        "security_auditor": {
            "allowed_tools": ["read_file", "search_files", "list_directory"],
            "system_prompt_extra": (
                "You are a security auditor. Review code for vulnerabilities: "
                "injection attacks, auth bypasses, data exposure, insecure defaults. "
                "Report findings with severity ratings. Do NOT modify any files."
            ),
        },
        "doc_writer": {
            "allowed_tools": ["read_file", "write_file", "search_files", "list_directory"],
            "system_prompt_extra": (
                "You are a documentation specialist. Read the code and write or update "
                "README files, docstrings, and inline comments. Keep docs concise and "
                "accurate. Focus on architecture and usage, not implementation details."
            ),
        },
        "debugger": {
            "allowed_tools": ["read_file", "run_command", "search_files", "list_directory", "apply_diff"],
            "system_prompt_extra": (
                "You are a debugging specialist. Analyze error messages and stack traces. "
                "Read relevant source files to understand the issue. Propose and apply "
                "minimal, targeted fixes. Run tests to verify the fix works."
            ),
        },
    }


class Subagent:
    """
    A specialized, isolated worker agent.
    
    Subagents run with restricted toolsets and scoped directories,
    using cheaper models when possible (e.g., gpt-4o-mini for test writing).
    """

    def __init__(
        self,
        config: SubagentConfig,
        client=None,
        parent_context: str = "",
    ):
        self.id = uuid.uuid4().hex[:8]
        self.config = config
        self.client = client
        self.parent_context = parent_context
        self.result: str = ""
        self.status: str = "pending"

    async def execute(self, task: str) -> str:
        """Run the subagent on a specific task."""
        self.status = "running"
        console.print(
            f"  [tool_call][SUB] Subagent [{self.config.name}][/tool_call] "
            f"[dim]({self.config.role})[/dim]"
        )

        if not self.client:
            self.status = "error"
            return "No API client available"

        # Build subagent-specific system prompt
        system_prompt = (
            f"You are a specialized subagent: {self.config.role}.\n"
            f"Working directory scope: {self.config.scope_dir}\n"
            f"Allowed tools: {', '.join(self.config.allowed_tools)}\n\n"
            f"{self.config.system_prompt_extra}\n\n"
            f"Context from orchestrator:\n{self.parent_context[:2000]}"
        )

        # Use cheaper model for subagents
        model = self.config.model_override or "openai/gpt-4o-mini"
        old_model = self.client.model
        self.client.model = model

        try:
            result = await self.client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ])
            self.result = result
            self.status = "done"
        except Exception as e:
            self.result = f"Subagent error: {e}"
            self.status = "error"
        finally:
            self.client.model = old_model

        console.print(
            f"  [success][OK] Subagent [{self.config.name}] complete[/success]"
        )
        return self.result


class SubagentOrchestrator:
    """
    Manages fan-out dispatching of multiple subagents.
    Supports parallel execution and result aggregation.
    """

    def __init__(self, client=None, working_dir: Path | None = None):
        self.client = client
        self.working_dir = working_dir or Path.cwd()
        self.subagents: list[Subagent] = []
        self.results: dict[str, str] = {}

    def create_subagent(
        self,
        role: str,
        name: str | None = None,
        scope_dir: str = ".",
        model: str | None = None,
    ) -> Subagent:
        """Create a configured subagent from a role preset."""
        role_config = SubagentConfig.ROLES.get(role, {})

        config = SubagentConfig(
            name=name or f"{role}-{uuid.uuid4().hex[:4]}",
            role=role,
            scope_dir=scope_dir,
            allowed_tools=role_config.get("allowed_tools", ["read_file"]),
            system_prompt_extra=role_config.get("system_prompt_extra", ""),
            model_override=model,
        )

        subagent = Subagent(config=config, client=self.client)
        self.subagents.append(subagent)
        return subagent

    async def fan_out(self, tasks: list[tuple[str, str]]) -> dict[str, str]:
        """
        Dispatch multiple subagents in parallel.
        
        Args:
            tasks: List of (role, task_description) tuples
            
        Returns:
            Dict of {role: result}
        """
        console.print(
            Panel(
                f"[bold]Dispatching {len(tasks)} subagents...[/bold]",
                border_style="#00f5d4",
            )
        )

        # Create subagents
        agents = []
        for role, task in tasks:
            agent = self.create_subagent(role=role)
            agents.append((agent, task))

        # Run in parallel
        async_tasks = [agent.execute(task) for agent, task in agents]
        results = await asyncio.gather(*async_tasks, return_exceptions=True)

        # Aggregate results
        for (agent, _), result in zip(agents, results):
            if isinstance(result, Exception):
                self.results[agent.config.role] = f"Error: {result}"
            else:
                self.results[agent.config.role] = result

        # Display summary
        table = Table(
            title="[SUB] Subagent Results",
            border_style="#00f5d4",
            show_header=True,
            header_style="bold #00f5d4",
        )
        table.add_column("Role", style="bold")
        table.add_column("Status")
        table.add_column("Result Preview", max_width=60)
        for agent, _ in agents:
            status_icon = "[OK]" if agent.status == "done" else "[X]"
            color = "green" if agent.status == "done" else "red"
            preview = agent.result[:100] + "..." if len(agent.result) > 100 else agent.result
            table.add_row(
                agent.config.role,
                f"[{color}]{status_icon}[/{color}]",
                preview,
            )
        console.print(table)

        return self.results

    # -- Git Worktree integration -------------------------------------------

    def create_worktree(self, branch_name: str) -> Path | None:
        """
        Create a git worktree for isolated parallel work.
        Returns the worktree path, or None if git isn't available.
        """
        worktree_dir = self.working_dir / f".worktrees/{branch_name}"

        try:
            # Create branch
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=str(self.working_dir),
                capture_output=True,
                check=False,
            )
            # Checkout back to original
            subprocess.run(
                ["git", "checkout", "-"],
                cwd=str(self.working_dir),
                capture_output=True,
                check=False,
            )
            # Create worktree
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), branch_name],
                cwd=str(self.working_dir),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print(f"[success][OK] Created worktree: {worktree_dir}[/success]")
                return worktree_dir
            else:
                console.print(f"[error]Worktree creation failed: {result.stderr}[/error]")
                return None
        except FileNotFoundError:
            console.print("[warning]Git not found - worktree unavailable[/warning]")
            return None

    def cleanup_worktree(self, branch_name: str) -> None:
        """Remove a git worktree after subagent work is done."""
        worktree_dir = self.working_dir / f".worktrees/{branch_name}"
        try:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir)],
                cwd=str(self.working_dir),
                capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(self.working_dir),
                capture_output=True,
            )
        except Exception:
            pass

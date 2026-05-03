"""
Self-Healing Loop - Autonomous test-fix-test debugging.

Runs a test command, captures errors, feeds them back to the LLM
for auto-repair, with a circuit breaker to prevent infinite loops.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from openrouter_agent.tools import ToolExecutor

console = Console()


def parse_traceback(stderr: str) -> str:
    """Extract only relevant error lines from a traceback."""
    lines = stderr.split("\n")
    relevant = [
        line for line in lines
        if "File " in line or "Error" in line or "Exception" in line
    ]
    return "\n".join(relevant[-5:]) if relevant else stderr[-500:]


async def self_healing_loop(
    test_command: str,
    tool_executor: ToolExecutor,
    ctx_mgr=None,
    client=None,
    max_attempts: int = 3,
) -> bool:
    """
    Autonomous test-fix-test cycle.
    Returns True if tests pass, False if circuit breaker triggers.
    """
    previous_errors: list[str] = []

    for attempt in range(max_attempts):
        result = await tool_executor.execute("run_command", {
            "command": test_command,
            "timeout": 60,
        })

        # Check if tests passed
        if "[exit code: 0]" in result:
            console.print("[success][OK] Tests passed![/success]")
            return True

        # Parse error
        error_summary = parse_traceback(result)

        # Circuit breaker: detect identical repeated errors
        if error_summary in previous_errors:
            console.print(
                "[warning]Circuit breaker: same error repeating. "
                "Returning control to user.[/warning]"
            )
            return False
        previous_errors.append(error_summary)

        console.print(
            f"[warning]Attempt {attempt + 1}/{max_attempts}: "
            f"Tests failed, asking LLM to fix...[/warning]"
        )

        # Feed error back to LLM
        if ctx_mgr and client:
            ctx_mgr.add_user_message(
                f"The test command `{test_command}` failed with:\n```\n{error_summary}\n```\n"
                "Please analyze and fix the issue using your tools. Do not explain, just execute the fix."
            )
            
            # Actually invoke the LLM to fix it
            from openrouter_agent.main import agentic_loop
            await agentic_loop(
                query="",  # we already added it to ctx_mgr
                ctx_mgr=ctx_mgr,
                client=client,
                tool_executor=tool_executor,
                permission_mode="auto", # Autonomous healing
                print_mode=False
            )

    console.print("[error]Max attempts reached. Manual intervention needed.[/error]")
    return False

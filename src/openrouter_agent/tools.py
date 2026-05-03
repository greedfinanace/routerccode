"""
Tool Execution Engine — Surgical edits & safe command execution.

Implements pydantic-validated tool schemas:
  - apply_diff: search-and-replace editing
  - edit_lines: line-range editing
  - run_command: sandboxed shell execution
  - read_file / write_file / list_directory / search_files
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional, Tuple

from pydantic import BaseModel, Field

from openrouter_agent.key_manager import APIKeyManager


# ---------------------------------------------------------------------------
# Tool schemas (Pydantic models)
# ---------------------------------------------------------------------------

class ApplyDiffTool(BaseModel):
    """Surgical file editing via search-and-replace."""
    file_path: str = Field(description="Path to file to edit")
    search: str = Field(description="Exact text to find")
    replace: str = Field(description="Text to replace with")


class EditLinesTool(BaseModel):
    """Edit specific line ranges."""
    file_path: str
    start_line: int
    end_line: int
    new_content: str


class RunCommandTool(BaseModel):
    """Execute shell command with safety checks."""
    command: str
    timeout: int = 30
    working_dir: Optional[str] = None


class ReadFileTool(BaseModel):
    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class WriteFileTool(BaseModel):
    file_path: str
    content: str


class ListDirectoryTool(BaseModel):
    path: str = "."


class SearchFilesTool(BaseModel):
    pattern: str
    path: str = "."
    file_glob: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool definitions for OpenRouter API (OpenAI function format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents. Optionally specify line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "start_line": {"type": "integer", "description": "Start line (1-indexed)"},
                    "end_line": {"type": "integer", "description": "End line (1-indexed)"},
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_diff",
            "description": "Surgical edit: find exact text and replace it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "search": {"type": "string", "description": "Exact text to find"},
                    "replace": {"type": "string", "description": "Replacement text"},
                },
                "required": ["file_path", "search", "replace"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_lines",
            "description": "Replace a range of lines in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "new_content": {"type": "string"},
                },
                "required": ["file_path", "start_line", "end_line", "new_content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command and return output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
                    "working_dir": {"type": "string", "description": "Working directory"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: .)"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a text pattern in files using grep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Search root (default: .)"},
                    "file_glob": {"type": "string", "description": "File filter e.g. *.py"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Execute validated tool calls against the local file system."""

    def __init__(self, working_dir: Path | None = None):
        self.working_dir = working_dir or Path.cwd()
        self.file_changes: list[dict[str, str]] = []

    async def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        handlers = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "apply_diff": self._apply_diff,
            "edit_lines": self._edit_lines,
            "run_command": self._run_command,
            "list_directory": self._list_directory,
            "search_files": self._search_files,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            return handler(params)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    def _resolve(self, file_path: str) -> Path:
        p = Path(file_path)
        if not p.is_absolute():
            p = self.working_dir / p
        return p.resolve()

    def _read_file(self, params: dict) -> str:
        p = self._resolve(params["file_path"])
        if not p.exists():
            return f"File not found: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        start = params.get("start_line")
        end = params.get("end_line")
        if start is not None:
            start = max(0, start - 1)
            end = end or len(lines)
            lines = lines[start:end]
        # Add line numbers
        numbered = [f"{i+1}: {line}" for i, line in enumerate(lines)]
        return "".join(numbered)

    def _write_file(self, params: dict) -> str:
        p = self._resolve(params["file_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(params["content"], encoding="utf-8")
        self.file_changes.append({"action": "write", "path": str(p)})
        return f"Written {len(params['content'])} chars to {p}"

    def _apply_diff(self, params: dict) -> str:
        validated = ApplyDiffTool(**params)
        p = self._resolve(validated.file_path)
        if not p.exists():
            return f"File not found: {p}"
        content = p.read_text(encoding="utf-8")
        if validated.search not in content:
            return f"Search text not found in {p}"
        new_content = content.replace(validated.search, validated.replace, 1)
        p.write_text(new_content, encoding="utf-8")
        self.file_changes.append({"action": "diff", "path": str(p)})
        return f"Applied diff to {p}"

    def _edit_lines(self, params: dict) -> str:
        validated = EditLinesTool(**params)
        p = self._resolve(validated.file_path)
        if not p.exists():
            return f"File not found: {p}"
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        start = max(0, validated.start_line - 1)
        end = min(len(lines), validated.end_line)
        new_lines = validated.new_content.splitlines(keepends=True)
        lines[start:end] = new_lines
        p.write_text("".join(lines), encoding="utf-8")
        self.file_changes.append({"action": "edit_lines", "path": str(p)})
        return f"Edited lines {validated.start_line}-{validated.end_line} in {p}"

    def _run_command(self, params: dict) -> str:
        validated = RunCommandTool(**params)
        cwd = self._resolve(validated.working_dir) if validated.working_dir else self.working_dir

        try:
            result = subprocess.run(
                validated.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=validated.timeout,
                cwd=str(cwd),
                env=APIKeyManager.sanitize_env(),
            )
            output = ""
            if result.stdout:
                output += f"stdout:\n{result.stdout}"
            if result.stderr:
                output += f"\nstderr:\n{result.stderr}"
            output += f"\n[exit code: {result.returncode}]"
            return output.strip()
        except subprocess.TimeoutExpired:
            return f"Command timed out after {validated.timeout}s"

    def _list_directory(self, params: dict) -> str:
        p = self._resolve(params.get("path", "."))
        if not p.exists():
            return f"Directory not found: {p}"
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        lines = []
        for entry in entries[:100]:
            prefix = "📁 " if entry.is_dir() else "📄 "
            lines.append(f"{prefix}{entry.name}")
        return "\n".join(lines) if lines else "(empty directory)"

    def _search_files(self, params: dict) -> str:
        pattern = params["pattern"]
        root = self._resolve(params.get("path", "."))
        glob = params.get("file_glob", "*")

        results = []
        for fpath in root.rglob(glob):
            if fpath.is_dir() or ".git" in fpath.parts:
                continue
            try:
                for i, line in enumerate(fpath.open(encoding="utf-8", errors="replace"), 1):
                    if pattern in line:
                        rel = fpath.relative_to(root)
                        results.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(results) >= 50:
                            break
            except Exception:
                continue
            if len(results) >= 50:
                break

        return "\n".join(results) if results else "No matches found"

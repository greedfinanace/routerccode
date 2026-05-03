"""
Codebase Mapping - AST-based repository skeleton generation.

Generates a compressed codebase map by parsing Python ASTs to extract
class and function signatures, producing a token-efficient overview.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

# Directories to always ignore
IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", "*.egg-info",
}


def should_ignore(path: Path) -> bool:
    """Check if a path should be excluded from mapping."""
    for part in path.parts:
        if part in IGNORE_DIRS or part.endswith(".egg-info"):
            return True
    return False


def extract_signatures(source: str) -> list[str]:
    """Extract class and function signatures from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    signatures = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(
                getattr(b, "id", getattr(b, "attr", "?")) for b in node.bases
            )
            sig = f"class {node.name}" + (f"({bases})" if bases else "")
            signatures.append(sig)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(arg.arg for arg in node.args.args)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            signatures.append(f"{prefix} {node.name}({args})")

    return signatures


def generate_codebase_map(
    root_dir: Path,
    max_chars: int = 12_000,
    extensions: tuple[str, ...] = (".py",),
) -> str:
    """
    Generate a compressed repository skeleton.

    Returns a string like:
      src/main.py: def main(), class App
      src/tools.py: class ToolExecutor, def execute()
    """
    tree_lines: list[str] = []

    for path in sorted(root_dir.rglob("*")):
        if path.is_dir() or should_ignore(path):
            continue
        if path.suffix not in extensions:
            continue

        rel = path.relative_to(root_dir)

        if path.suffix == ".py":
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                sigs = extract_signatures(source)
                if sigs:
                    tree_lines.append(f"{rel}: {', '.join(sigs)}")
                else:
                    tree_lines.append(str(rel))
            except Exception:
                tree_lines.append(str(rel))
        else:
            tree_lines.append(str(rel))

    full_map = "\n".join(tree_lines)

    # Truncate if exceeds budget
    if len(full_map) > max_chars:
        full_map = full_map[:max_chars] + "\n[... truncated ...]"

    return full_map

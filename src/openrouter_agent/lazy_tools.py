"""
Lazy Tool Loader — MCP Tool Search & Dynamic Loading.

When tool definitions exceed 10% of the context window,
instead of sending all tool schemas to the LLM, we send only
a ToolSearchTool. The agent must first search for tools it
needs, then we inject those specific schemas.

This prevents "context rot" from tool description bloat.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

console = Console()

# 10% of context window is the threshold for lazy loading
LAZY_LOAD_THRESHOLD_PCT = 0.10


class LazyToolLoader:
    """
    Manages lazy loading of tool definitions.
    
    If total tool tokens > 10% of context, switches to search mode:
    - Only sends tool names + short descriptions
    - Agent calls ToolSearchTool to get full schemas
    - Full schemas injected for just the tools needed
    """

    def __init__(self, all_tools: list[dict[str, Any]], max_context_tokens: int = 128_000):
        self.all_tools = all_tools
        self.max_context_tokens = max_context_tokens
        self._tool_index = self._build_index()
        self._active_tools: set[str] = set()

    def _build_index(self) -> dict[str, dict[str, Any]]:
        """Build a name→tool mapping for quick lookup."""
        index = {}
        for tool in self.all_tools:
            name = tool.get("function", {}).get("name", "")
            index[name] = tool
        return index

    def _estimate_tokens(self, tools: list[dict]) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(json.dumps(tools)) // 4

    def should_lazy_load(self) -> bool:
        """Check if we need lazy loading based on tool count."""
        token_est = self._estimate_tokens(self.all_tools)
        threshold = int(self.max_context_tokens * LAZY_LOAD_THRESHOLD_PCT)
        return token_est > threshold

    def get_tool_search_definition(self) -> dict[str, Any]:
        """
        Return the ToolSearchTool definition — a meta-tool
        that lets the agent discover and load other tools.
        """
        # Build searchable catalog
        catalog_lines = []
        for tool in self.all_tools:
            fn = tool.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "")[:100]
            catalog_lines.append(f"- {name}: {desc}")

        catalog = "\n".join(catalog_lines)

        return {
            "type": "function",
            "function": {
                "name": "tool_search",
                "description": (
                    f"Search for and load tool schemas. There are {len(self.all_tools)} "
                    f"tools available but their full schemas are not loaded to save context. "
                    f"Call this tool with the name(s) of tools you need.\n\n"
                    f"Available tools:\n{catalog}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of tool names to load full schemas for",
                        },
                    },
                    "required": ["tool_names"],
                    "additionalProperties": False,
                },
            },
        }

    def search_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        """
        Load full schemas for the requested tool names.
        Returns the complete tool definitions.
        """
        loaded = []
        for name in tool_names:
            if name in self._tool_index:
                loaded.append(self._tool_index[name])
                self._active_tools.add(name)
                console.print(f"  [tool_call]📦 Loaded tool: {name}[/tool_call]")
            else:
                console.print(f"  [warning]Tool not found: {name}[/warning]")
        return loaded

    def get_active_tools(self) -> list[dict[str, Any]]:
        """
        Get the tool definitions to send with the API request.
        If lazy loading is active, returns only loaded + search tool.
        Otherwise returns all tools.
        """
        if not self.should_lazy_load():
            return self.all_tools

        # Return ToolSearchTool + any actively loaded tools
        active = [self.get_tool_search_definition()]
        for name in self._active_tools:
            if name in self._tool_index:
                active.append(self._tool_index[name])

        return active

    def reset(self) -> None:
        """Clear loaded tools (e.g., between conversation turns)."""
        self._active_tools.clear()

"""
MCP Host - Model Context Protocol host implementation.

Manages lifecycle of MCP server child processes, communicating
via JSON-RPC 2.0 over stdio. Supports dynamic tool discovery
and deferred loading.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional


class MCPServer:
    """Represents a single MCP server connection over stdio."""

    def __init__(self, name: str, process: asyncio.subprocess.Process):
        self.name = name
        self.process = process
        self.request_id = 0
        self.capabilities: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        response = await self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
        })
        self.capabilities = response.get("result", {}).get("capabilities", {})
        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

    async def send_request(self, method: str, params: dict) -> dict:
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(request) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()

        response_line = await self.process.stdout.readline()
        if not response_line:
            return {"error": "No response from MCP server"}
        return json.loads(response_line)

    async def _send_notification(self, method: str, params: dict) -> None:
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()

    async def list_tools(self) -> list[dict[str, Any]]:
        response = await self.send_request("tools/list", {})
        self.tools = response.get("result", {}).get("tools", [])
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        response = await self.send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return response.get("result", response.get("error", "Unknown error"))

    async def shutdown(self) -> None:
        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            self.process.kill()


class MCPHost:
    """Manages lifecycle of multiple MCP server processes."""

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}

    async def start_server(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> MCPServer:
        cmd_args = args or []
        process = await asyncio.create_subprocess_exec(
            command, *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        server = MCPServer(name, process)
        await server.initialize()
        self.servers[name] = server
        return server

    async def call_tool(self, server_name: str, tool_name: str, params: dict) -> Any:
        server = self.servers.get(server_name)
        if not server:
            raise ValueError(f"MCP server '{server_name}' not found")
        return await server.call_tool(tool_name, params)

    async def discover_all_tools(self) -> list[dict[str, Any]]:
        """Query all servers for available tools -> OpenAI function format."""
        all_tools = []
        for server_name, server in self.servers.items():
            tools = await server.list_tools()
            for tool in tools:
                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": f"{server_name}__{tool['name']}",
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {}),
                    },
                }
                all_tools.append(openai_tool)
        return all_tools

    async def shutdown_all(self) -> None:
        for server in self.servers.values():
            await server.shutdown()
        self.servers.clear()

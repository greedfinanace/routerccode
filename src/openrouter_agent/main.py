"""
RouterCode CLI — Main Entry Point

OpenRouter-powered coding agent CLI with:
- ReAct agentic loop with tool execution
- Persistent project memory (ROUTERCODE.md + MEMORY.md)
- Two-stage intent security classifier (Auto Mode)
- Fan-out parallel subagent delegation
- Lazy tool loading (MCP Tool Search)
- Remote notifications (Telegram/Discord)
- OpenRouter model discovery & real cost tracking
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Optional, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich.markdown import Markdown

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML

from openrouter_agent import __version__
from openrouter_agent.api_client import OpenRouterClient
from openrouter_agent.context import ContextManager
from openrouter_agent.key_manager import APIKeyManager
from openrouter_agent.memory_manager import MemoryManager
from openrouter_agent.security import IntentSecurityClassifier
from openrouter_agent.session import SessionManager
from openrouter_agent.subagent import SubagentOrchestrator
from openrouter_agent.lazy_tools import LazyToolLoader
from openrouter_agent.remote import get_notifier
from openrouter_agent.tools import ToolExecutor, TOOL_DEFINITIONS
from openrouter_agent.ui.theme import CATPPUCCIN_MOCHA_THEME, create_banner

app = typer.Typer(
    name="routercode",
    help="RouterCode — OpenRouter-powered coding agent CLI · 200+ models",
    add_completion=True,
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console(theme=CATPPUCCIN_MOCHA_THEME)
security = IntentSecurityClassifier()

import json

class FavoritesManager:
    def __init__(self):
        self.path = Path.home() / ".routercode_favourites.json"
        self._favorites = []
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self._favorites = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._favorites = []

    def save(self):
        self.path.write_text(json.dumps(self._favorites), encoding="utf-8")

    def toggle(self, model: str) -> bool:
        if model in self._favorites:
            self._favorites.remove(model)
            self.save()
            return False
        else:
            self._favorites.append(model)
            self.save()
            return True

    def get_all(self):
        return self._favorites


# ---------------------------------------------------------------------------
# Slash-command router (used inside the interactive REPL)
# ---------------------------------------------------------------------------
SLASH_COMMANDS = {
    "/compact": "Manually trigger context compression",
    "/clear": "Hard reset conversation",
    "/model": "Switch model mid-session",
    "/mode": "Switch permission mode (ask-first, auto, trust, plan)",
    "/models": "Browse available OpenRouter models",
    "/memo": "Add insight to MEMORY.md (persistent agent memory)",
    "/init": "Bootstrap ROUTERCODE.md for this project",
    "/dispatch": "Fan-out subagent (test_writer/debugger/security_auditor/doc_writer)",
    "/heal": "Run self-healing test-fix-test loop",
    "/fork": "Branch conversation",
    "/diff": "View all file changes",
    "/context": "Visualize token usage",
    "/cost": "Show real API spending & cache savings",
    "/favourite": "Toggle current model as favourite, or list favourites",
    "/export": "Save conversation to file",
    "/help": "Show available commands",
    "/quit": "Exit the session",
}

class RouterCodeCompleter(Completer):
    """Custom auto-completer for slash commands and OpenRouter models."""
    def __init__(self, commands: dict[str, str], client: OpenRouterClient):
        self.commands = commands
        self.client = client

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        
        if text.startswith("/"):
            parts = text.split(" ", 1)
            cmd = parts[0].lower()
            
            # Autocomplete the command itself or allow direct model switching
            if len(parts) == 1:
                # 1. Standard commands
                for k, v in self.commands.items():
                    if k.startswith(cmd):
                        yield Completion(k, start_position=-len(cmd), display_meta=v)
                
                # 2. Direct model switching (if typing past just "/")
                prefix = cmd[1:]
                if self.client._models_cache and prefix:
                    count = 0
                    for m in self.client._models_cache:
                        if prefix.lower() in m.id.lower() or prefix.lower() in m.name.lower():
                            yield Completion(
                                f"/{m.id}",
                                start_position=-len(cmd),
                                display=f"/{m.id}",
                                display_meta=f"Switch model (${m.pricing_prompt:.2f}/M)",
                            )
                            count += 1
                            if count >= 15:
                                break
            
            # Legacy /model arguments using cached OpenRouter models
            elif cmd == "/model" and len(parts) == 2:
                prefix = parts[1]
                if self.client._models_cache:
                    count = 0
                    for m in self.client._models_cache:
                        if prefix.lower() in m.id.lower() or prefix.lower() in m.name.lower():
                            yield Completion(
                                m.id,
                                start_position=-len(prefix),
                                display=m.id,
                                display_meta=f"${m.pricing_prompt:.2f}/M tokens",
                            )
                            count += 1
                            if count >= 15:
                                break

async def handle_slash_command(
    cmd: str,
    ctx_mgr: ContextManager,
    session_mgr: SessionManager,
    client: OpenRouterClient,
    memory_mgr: MemoryManager | None = None,
    tool_executor: ToolExecutor | None = None,
) -> Any:
    """
    Process slash commands typed during the interactive REPL.
    Returns True to continue, False to exit, or a string for special actions.
    """
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit"):
        console.print("[success]Session saved. Goodbye![/success]")
        return False

    elif command == "/help":
        table = Table(
            title="⚡ Slash Commands",
            border_style="#00f5d4",
            show_header=True,
            header_style="bold #00f5d4",
        )
        table.add_column("Command", style="bold")
        table.add_column("Description")
        for k, v in SLASH_COMMANDS.items():
            table.add_row(k, v)
        console.print(table)

    elif command == "/compact":
        with console.status("[info]⏳ Compacting context…[/info]", spinner="dots"):
            instructions = args if args else None
            await ctx_mgr.compact(instructions=instructions, client=client)
            console.print("[success]✓ Context compacted[/success]")

    elif command == "/clear":
        ctx_mgr.clear()
        console.print("[warning]⚠ Context cleared — fresh start[/warning]")

    elif command == "/mode":
        valid_modes = ["ask-first", "auto", "trust", "plan"]
        mode_arg = args.strip().lower()
        if not mode_arg:
            console.print("[info]Usage: /mode <mode>[/info]")
            console.print(f"[dim]Available modes: {', '.join(valid_modes)}[/dim]")
        elif mode_arg not in valid_modes:
            console.print(f"[error]Invalid mode. Choose from: {', '.join(valid_modes)}[/error]")
        else:
            # We return the new mode via a special string or just let the caller handle it.
            # But wait, handle_slash_command returns bool. We can return the string if it's a mode change,
            # or just mutate a global or pass a callback.
            # Actually, we can return the string from handle_slash_command.
            return f"MODE_SWITCH:{mode_arg}"

    elif command == "/favourite":
        fav_mgr = FavoritesManager()
        if not args:
            favs = fav_mgr.get_all()
            if not favs:
                console.print("[info]No favourite models yet. Use /favourite <model> or /favourite to add the current model.[/info]")
            else:
                table = Table(title="⭐ Favourite Models", border_style="accent")
                table.add_column("Model ID", style="model_id")
                for f in favs:
                    table.add_row(f)
                console.print(table)
        else:
            target_model = args.strip()
            added = fav_mgr.toggle(target_model)
            if added:
                console.print(f"[success]⭐ Added {target_model} to favourites[/success]")
            else:
                console.print(f"[info]Removed {target_model} from favourites[/info]")

    elif command == "/model":
        if not args:
            info = await client.get_model_info()
            console.print(f"[info]Current model:[/info] [bold]{client.model}[/bold]")
            if info:
                console.print(f"[dim]  Context: {info.context_length:,} tokens[/dim]")
                console.print(f"[dim]  Pricing: ${info.pricing_prompt:.2f}/${info.pricing_completion:.2f} per 1M tokens[/dim]")
        else:
            old_model = client.model
            client.model = args.strip()
            console.print(f"[success]✓ Switched: {old_model} → {client.model}[/success]")

    elif command == "/models":
        query = args.strip()
        with console.status(f"[info]⏳ Fetching models from OpenRouter…[/info]", spinner="dots"):
            try:
                models = await client.list_models(query)
                table = Table(
                    title=f"🌐 OpenRouter Models{f' (filter: {query})' if query else ''}",
                    border_style="#00f5d4",
                    show_header=True,
                    header_style="bold #00f5d4",
                )
                table.add_column("Model ID", style="bold", max_width=45)
                table.add_column("Context", justify="right")
                table.add_column("$/M prompt", justify="right", style="green")
                table.add_column("$/M completion", justify="right", style="yellow")
                for m in models[:25]:  # show top 25
                    ctx_str = f"{m.context_length // 1000}K" if m.context_length else "?"
                    table.add_row(
                        m.id,
                        ctx_str,
                        f"${m.pricing_prompt:.2f}",
                        f"${m.pricing_completion:.2f}",
                    )
                console.print(table)
                if len(models) > 25:
                    console.print(f"[dim]  … and {len(models) - 25} more. Use /models <query> to filter.[/dim]")
            except Exception as e:
                console.print(f"[error]Failed to fetch models: {e}[/error]")

    elif command == "/context":
        ctx_mgr.visualize(console)

    elif command == "/cost":
        cost = client.estimate_cost()
        table = Table(
            title="💰 Session Cost (OpenRouter)",
            border_style="#00f5d4",
            show_header=False,
        )
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")
        table.add_row("Model", cost.get("model", client.model))
        table.add_row("Total cost", f"${cost['total_cost_usd']:.4f}")
        if "cache_savings_usd" in cost:
            table.add_row("Cache savings", f"[green]-${cost['cache_savings_usd']:.4f}[/green]")
        table.add_row("Prompt tokens", f"{cost['prompt_tokens']:,}")
        table.add_row("Completion tokens", f"{cost['completion_tokens']:,}")
        if cost.get("cache_read_tokens"):
            table.add_row("Cache reads", f"{cost['cache_read_tokens']:,}")
        table.add_row("API requests", str(cost.get("requests", 0)))
        table.add_row("Session time", f"{cost.get('elapsed_minutes', 0):.1f} min")
        table.add_row("Source", cost.get("source", "estimated"))
        console.print(table)

    elif command == "/diff":
        session_mgr.show_diff(console)

    elif command == "/fork":
        name = args.strip() or None
        session_mgr.fork(name)
        console.print("[success]✓ Conversation forked[/success]")

    elif command == "/export":
        filename = args.strip() or "session_export.txt"
        session_mgr.export(filename)
        console.print(f"[success]✓ Exported to {filename}[/success]")

    elif command == "/memo":
        if not args:
            console.print("[warning]Usage: /memo <insight to remember>[/warning]")
        elif memory_mgr:
            memory_mgr.add_memory(args)
            console.print(f"[success]✓ Saved to MEMORY.md[/success]")
            # Also tell the LLM
            ctx_mgr.add_user_message(f"[System: User saved a memory note: {args}]")
        else:
            console.print("[error]Memory manager not initialized[/error]")

    elif command == "/init":
        if memory_mgr:
            memory_mgr.init_project()
        else:
            console.print("[error]Memory manager not initialized[/error]")

    elif command == "/dispatch":
        if not args:
            console.print("[warning]Usage: /dispatch <role> <task>[/warning]")
            console.print("[dim]  Roles: test_writer, debugger, security_auditor, doc_writer[/dim]")
        else:
            dispatch_parts = args.split(maxsplit=1)
            role = dispatch_parts[0]
            task = dispatch_parts[1] if len(dispatch_parts) > 1 else "Analyze the current project"
            orchestrator = SubagentOrchestrator(client=client)
            results = await orchestrator.fan_out([(role, task)])
            for role_name, result in results.items():
                ctx_mgr.add_assistant_message(f"[Subagent {role_name}]:\n{result}")

    elif command == "/heal":
        test_cmd = args.strip() or "pytest tests/ -v"
        from openrouter_agent.self_heal import self_healing_loop
        await self_healing_loop(test_cmd, tool_executor, ctx_mgr, client)

    else:
        # Fallback: check if the command is actually a direct model switch (e.g., /tencent/hy3-preview:free)
        potential_model = command[1:] # strip leading slash
        
        # If cache is available, strictly validate
        found_model = False
        if client._models_cache:
            for m in client._models_cache:
                if m.id.lower() == potential_model.lower():
                    old_model = client.model
                    client.model = m.id
                    console.print(f"[success]✓ Switched: {old_model} → {client.model}[/success]")
                    found_model = True
                    break
        else:
            # If cache isn't loaded yet, assume they know what they are typing
            if "/" in potential_model:
                old_model = client.model
                client.model = potential_model
                console.print(f"[success]✓ Switched: {old_model} → {client.model}[/success]")
                found_model = True

        if not found_model:
            console.print(Panel(
                f"I didn't recognize the command or model '{command}'.\n\n[dim]Tip: Type /help to see all available commands, or /models to search for an AI model.[/dim]",
                title="Unknown Command",
                border_style="warning"
            ))

    return True


# ---------------------------------------------------------------------------
# Agentic execution loop (Phase 1 — ReAct)
# ---------------------------------------------------------------------------
async def agentic_loop(
    query: str,
    ctx_mgr: ContextManager,
    client: OpenRouterClient,
    tool_executor: ToolExecutor,
    permission_mode: str,
    print_mode: bool = False,
) -> None:
    """
    Core ReAct loop: Reason + Act cycle.
    1. Token budget check → compact if needed
    2. Stream API call via OpenRouter
    3. Parse tool calls
    4. Permission check
    5. Execute tools, feed results back
    6. Repeat until task complete or user intervention
    """
    if query:
        ctx_mgr.add_user_message(query)
    max_iterations = 50  # circuit-breaker

    for iteration in range(max_iterations):
        # 1. Token budget check
        if ctx_mgr.is_over_budget():
            console.print("[warning]⚠ Context budget exceeded — auto-compacting…[/warning]")
            await ctx_mgr.compact(client=client)

        # 2. Stream API call via OpenRouter
        tool_calls = []
        response_text = ""
        reasoning_text = ""

        if not print_mode:
            model_name = client.model.split('/')[-1]
            live_context = Live(Spinner("dots", text=f"thinking ({model_name})…", style="tool_call"), console=console, transient=True)
        else:
            live_context = None
        
        try:
            if live_context:
                live_context.start()

            reasoning_started = False
            text_started = False

            async for chunk in client.stream_chat(ctx_mgr.build_payload()):
                if live_context:
                    live_context.stop()
                    live_context = None

                if chunk.get("type") == "reasoning":
                    if not reasoning_started:
                        console.print(f"[dim]╭── 🧠 Internal Thought Process ({model_name})[/dim]")
                        reasoning_started = True
                    reasoning_text += chunk["content"]
                    console.print(chunk["content"], end="", markup=False, style="dim")
                elif chunk.get("type") == "text":
                    if not text_started:
                        if reasoning_started:
                            console.print("\n[dim]╰──[/dim]\n")
                        console.print(f"[success]▶[/success] [bold]RouterCode:[/bold] ", end="")
                        text_started = True
                    response_text += chunk["content"]
                    console.print(chunk["content"], end="", markup=False)
                elif chunk.get("type") == "tool_call":
                    tool_calls.append(chunk["tool_call"])
            if text_started:
                console.print()
        except Exception as e:
            console.print(Panel(
                f"The OpenRouter API encountered an issue: {e}\n\n[dim]Tip: Check your network connection or the model's current availability.[/dim]",
                title="API Interruption",
                border_style="error"
            ))
            break
        finally:
            if live_context:
                live_context.stop()

        # Add assistant message
        ctx_mgr.add_assistant_message(response_text, tool_calls)

        # If no tool calls, the agent is done reasoning
        if not tool_calls:
            if print_mode:
                console.print(response_text)
            break

        # 3-5. Process each tool call
        for i, tc in enumerate(tool_calls):
            tool_call_id = tc.get("id", f"call_{i}")
            tool_name = tc["name"]
            tool_params = tc.get("parameters", {})

            # Handle lazy tool loading (ToolSearchTool)
            if tool_name == "tool_search":
                # Lazy tool loader handles this specially
                console.print(f"  [tool_call]🔍 tool_search[/tool_call]")
                requested = tool_params.get("tool_names", [])
                ctx_mgr.add_tool_result(tool_call_id, tool_name, f"Loaded tools: {', '.join(requested)}")
                continue

            # Permission check — multi-mode with intent security
            if permission_mode == "plan":
                if tool_name not in ("read_file", "search_files", "list_directory"):
                    ctx_mgr.add_tool_result(
                        tool_call_id, tool_name, "Permission denied: plan mode is read-only"
                    )
                    console.print(f"[error]  ✗ {tool_name} blocked (plan mode)[/error]")
                    continue

            elif permission_mode == "auto":
                # Two-stage intent security classifier
                verdict, reason = await security.check(
                    tool_name, tool_params, query, client
                )
                if verdict == "block":
                    ctx_mgr.add_tool_result(tool_call_id, tool_name, f"Blocked: {reason}")
                    continue
                elif verdict == "ask":
                    detail = _format_tool_preview(tool_name, tool_params)
                    console.print(
                        Panel(
                            f"{detail}\n\n[dim]Reason: {reason}[/dim]",
                            title=f"[bold]🛡️ Intent unclear: {tool_name}[/bold]",
                            border_style="warning",
                        )
                    )
                    approved = typer.confirm("Allow?", default=True)
                    if not approved:
                        ctx_mgr.add_tool_result(tool_call_id, tool_name, "User denied permission")
                        continue
                # verdict == "allow" → proceed

            elif permission_mode == "ask-first":
                if tool_name in ("run_command", "apply_diff", "edit_lines", "write_file"):
                    detail = _format_tool_preview(tool_name, tool_params)
                    console.print(
                        Panel(
                            detail,
                            title=f"[bold]🔐 Permission: {tool_name}[/bold]",
                            border_style="warning",
                        )
                    )
                    approved = typer.confirm("Allow?", default=True)
                    if not approved:
                        ctx_mgr.add_tool_result(tool_call_id, tool_name, "User denied permission")
                        continue

            from rich.status import Status
            # Execute
            with Status(f"[tool_call]⚡ Executing {tool_name}[/tool_call]", spinner="dots", console=console, spinner_style="tool_call") as status:
                t0 = time.time()
                result = await tool_executor.execute(tool_name, tool_params)
                elapsed = time.time() - t0
                ctx_mgr.add_tool_result(tool_call_id, tool_name, result)
            console.print(f"  [success]✓[/success] [dim]{tool_name} completed in {elapsed:.1f}s[/dim]")

    else:
        console.print(Panel(
            "The agent reached the maximum number of iterations without completing the task.\n\n[dim]Tip: Try breaking your request into smaller, more specific steps.[/dim]",
            title="Task Paused",
            border_style="warning"
        ))


from rich.syntax import Syntax
from rich.console import Group

def _format_tool_preview(tool_name: str, params: dict) -> Any:
    """Format a human-readable preview of what a tool call wants to do."""
    if tool_name == "run_command":
        return f"[bold]Command:[/bold] {params.get('command', '?')}"
    elif tool_name == "apply_diff":
        path = params.get("file_path", "?")
        search = params.get("search", "")
        replace = params.get("replace", "")
        return Group(
            f"[bold]File:[/bold] {path}\n[bold]Replace:[/bold]",
            Syntax(search, "python", theme="monokai", line_numbers=False, background_color="default"),
            f"[bold]With:[/bold]",
            Syntax(replace, "python", theme="monokai", line_numbers=False, background_color="default"),
        )
    elif tool_name == "edit_lines":
        path = params.get("file_path", "?")
        start = params.get("start_line", "?")
        end = params.get("end_line", "?")
        new_content = params.get("new_content", "")
        return Group(
            f"[bold]File:[/bold] {path} (Lines {start}-{end})\n[bold]New Content:[/bold]",
            Syntax(new_content, "python", theme="monokai", line_numbers=False, background_color="default"),
        )
    elif tool_name == "write_file":
        path = params.get("file_path", "?")
        content = params.get("content", "")
        size = len(content)
        preview = content[:500] + ("..." if size > 500 else "")
        return Group(
            f"[bold]File:[/bold] {path} ({size:,} chars)\n[bold]Content Preview:[/bold]",
            Syntax(preview, "python", theme="monokai", line_numbers=False, background_color="default"),
        )
    return str(params)


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------
async def interactive_repl(
    client: OpenRouterClient,
    ctx_mgr: ContextManager,
    tool_executor: ToolExecutor,
    session_mgr: SessionManager,
    permission_mode: str,
    memory_mgr: MemoryManager | None = None,
) -> None:
    """Interactive Read-Eval-Print Loop with slash command support."""
    console.print(create_banner())

    # Load persistent memory into context
    if memory_mgr:
        memory_context = memory_mgr.load()
        if memory_context:
            ctx_mgr.add_user_message(f"[System: Project memory loaded]\n{memory_context}")
            console.print("  [success]📋 Project memory loaded[/success]")

    # Check for remote notification channels
    notifier = get_notifier()
    remote_status = "📱 Connected" if notifier.enabled else "—"

    # Kick off a background task to fetch models so autocomplete works instantly
    asyncio.create_task(client.list_models())

    # Show model info
    console.print(
        f"  [info]Model:[/info]   [bold]{client.model}[/bold]\n"
        f"  [info]Mode:[/info]    {permission_mode}\n"
        f"  [info]Session:[/info] {session_mgr.session_id[:8]}…\n"
        f"  [info]API:[/info]     [bold #00f5d4]OpenRouter[/bold #00f5d4] (openrouter.ai)\n"
        f"  [info]Remote:[/info]  {remote_status}\n"
    )
    console.print("[dim]  Type /help for commands · /models to browse · Ctrl+C to interrupt[/dim]\n")

    # Set up prompt_toolkit session for history and autocomplete
    history_file = Path.home() / ".routercode_history"
    smart_completer = RouterCodeCompleter(SLASH_COMMANDS, client)
    
    def get_bottom_toolbar():
        cwd = str(Path.cwd())
        if len(cwd) > 40:
            cwd = "..." + cwd[-37:]
            
        tokens = ctx_mgr.context_token_count()
        mode_color = "ansired" if permission_mode == "auto" else "ansiyellow"
        
        # Calculate cost
        cost = client.estimate_cost()
        cost_str = f"${cost.get('total_cost_usd', 0):.4f}"
        
        return HTML(
            f" <b>workspace:</b> <style fg='#89b4fa'>{cwd}</style> │ "
            f" <b>mode:</b> <style fg='#f38ba8'>{permission_mode}</style> │ "
            f" <b>model:</b> <style fg='#a6e3a1'>{client.model}</style> │ "
            f" <b>context:</b> <style fg='#bac2de'>{tokens}tk</style> │ "
            f" <b>cost:</b> <style fg='#fab387'>{cost_str}</style> "
        )
    
    prompt_session = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=smart_completer,
    )

    while True:
        try:
            # Use prompt_toolkit instead of rich console.input
            query = await prompt_session.prompt_async(
                HTML("<style fg='#cba6f7'>▶</style> <style fg='#bac2de'><b>RouterCode</b></style> <style fg='#cba6f7'>❯ </style>"),
                placeholder=HTML("<style fg='#6c7086'>Type a request, or /help for commands...</style>"),
                bottom_toolbar=get_bottom_toolbar
            )
        except KeyboardInterrupt:
            # Ctrl+C exits
            console.print("\n[success]Goodbye![/success]")
            break
        except EOFError:
            # Ctrl+D exits
            console.print("\n[success]Goodbye![/success]")
            break

        query = query.strip()
        if not query:
            continue

        # Slash commands
        if query.startswith("/"):
            result = await handle_slash_command(
                query, ctx_mgr, session_mgr, client,
                memory_mgr=memory_mgr, tool_executor=tool_executor,
            )
            if result is False:
                break
            elif isinstance(result, str) and result.startswith("MODE_SWITCH:"):
                permission_mode = result.split(":")[1]
                console.print(f"[success]✓ Switched permission mode to: {permission_mode}[/success]")
            continue

        # Normal query → agentic loop
        try:
            await agentic_loop(
                query, ctx_mgr, client, tool_executor, permission_mode
            )
        except KeyboardInterrupt:
            console.print("\n[warning]Interrupted by user[/warning]")
        except Exception as e:
            console.print(Panel(
                f"An unexpected issue occurred: {e}\n\n[dim]Please try your request again. If the issue persists, consider starting a fresh session with /clear.[/dim]",
                title="Session Interruption",
                border_style="error"
            ))

    # Persist session on exit
    session_mgr.save(ctx_mgr)
    # Show final cost
    cost = client.estimate_cost()
    if cost["prompt_tokens"] > 0:
        console.print(
            f"\n[dim]Session cost: ${cost['total_cost_usd']:.4f} "
            f"({cost['prompt_tokens']:,} prompt + {cost['completion_tokens']:,} completion tokens) "
            f"via OpenRouter[/dim]"
        )


# ---------------------------------------------------------------------------
# Typer commands
# ---------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(None, help="Direct query to execute"),
    print_mode: bool = typer.Option(False, "-p", "--print", help="Print mode: execute and exit"),
    continue_session: bool = typer.Option(False, "-c", "--continue", help="Continue last session"),
    resume: Optional[str] = typer.Option(None, "-r", "--resume", help="Resume specific session"),
    model: str = typer.Option(
        "anthropic/claude-sonnet-4", "--model", "-m",
        help="OpenRouter model ID (e.g. anthropic/claude-opus-4, google/gemini-2.5-pro)",
    ),
    permission_mode: str = typer.Option(
        "ask-first",
        "--mode",
        help="Permission mode: plan | ask-first | auto | trust",
    ),
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
) -> None:
    """RouterCode — OpenRouter-powered coding agent CLI."""
    if version:
        console.print(f"[info]routercode v{__version__}[/info]")
        console.print("[dim]API: OpenRouter (openrouter.ai)[/dim]")
        raise typer.Exit()

    # If a subcommand (models, update) was invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    if print_mode and not query:
        console.print("[error]Error: -p requires a query argument[/error]")
        raise typer.Exit(1)

    # Validate permission mode
    valid_modes = ("plan", "ask-first", "auto", "trust")
    if permission_mode not in valid_modes:
        console.print(f"[error]Invalid mode. Choose from: {', '.join(valid_modes)}[/error]")
        raise typer.Exit(1)

    # API key (stored in OS keychain, NOT in plaintext)
    key_mgr = APIKeyManager()
    api_key = key_mgr.get_api_key()
    if not api_key:
        console.print(Panel(
            "An OpenRouter API key is required to start the agent.\n\n[dim]Tip: Set the OPENROUTER_API_KEY environment variable or run the setup process.[/dim]",
            title="Setup Required",
            border_style="warning"
        ))
        raise typer.Exit(1)

    # Session
    session_mgr = SessionManager()
    if continue_session:
        session_mgr.load_latest()
    elif resume:
        session_mgr.load(resume)
    else:
        session_mgr.create_new()

    # Core components — OpenRouter client (NOT OpenAI!)
    client = OpenRouterClient(api_key=api_key, model=model)
    ctx_mgr = ContextManager(session=session_mgr)
    tool_executor = ToolExecutor(working_dir=Path.cwd())
    memory_mgr = MemoryManager(project_root=Path.cwd())

    # Wire tool definitions — with lazy loading if too many
    tool_loader = LazyToolLoader(TOOL_DEFINITIONS)
    ctx_mgr.tool_definitions = tool_loader.get_active_tools()

    # Run
    if query:
        # Load memory for direct queries too
        memory_context = memory_mgr.load()
        if memory_context:
            ctx_mgr.add_user_message(f"[System: Project memory loaded]\n{memory_context}")
        asyncio.run(
            agentic_loop(query, ctx_mgr, client, tool_executor, permission_mode, print_mode)
        )
        # Show cost in print mode
        cost = client.estimate_cost()
        if cost["prompt_tokens"] > 0 and not print_mode:
            console.print(f"\n[dim]Cost: ${cost['total_cost_usd']:.4f} via OpenRouter[/dim]")
    else:
        asyncio.run(
            interactive_repl(client, ctx_mgr, tool_executor, session_mgr, permission_mode, memory_mgr)
        )


@app.command()
def models(
    query: Optional[str] = typer.Argument(None, help="Filter models by name"),
) -> None:
    """Browse available models on OpenRouter."""
    key_mgr = APIKeyManager()
    api_key = key_mgr.get_api_key()
    if not api_key:
        console.print("[error]No API key configured.[/error]")
        raise typer.Exit(1)

    async def _list():
        client = OpenRouterClient(api_key=api_key)
        results = await client.list_models(query or "")
        table = Table(
            title=f"🌐 OpenRouter Models{f' ({query})' if query else ''}",
            border_style="#00f5d4",
            show_header=True,
            header_style="bold #00f5d4",
        )
        table.add_column("Model ID", style="bold", max_width=45)
        table.add_column("Context", justify="right")
        table.add_column("$/M in", justify="right", style="green")
        table.add_column("$/M out", justify="right", style="yellow")
        for m in results[:40]:
            ctx_str = f"{m.context_length // 1000}K" if m.context_length else "?"
            table.add_row(m.id, ctx_str, f"${m.pricing_prompt:.2f}", f"${m.pricing_completion:.2f}")
        console.print(table)
        console.print(f"\n[dim]{len(results)} models available · Use: routercode --model <id>[/dim]")
        await client.close()

    asyncio.run(_list())


@app.command()
def update(
    target_version: Optional[str] = typer.Argument(None, help="Specific version to install"),
) -> None:
    """Update CLI to latest or specific version."""
    import subprocess

    cmd = ["pip", "install", "--upgrade", "openrouter-agent"]
    if target_version:
        cmd[-1] = f"openrouter-agent=={target_version}"

    console.print(f"[info]Updating: {' '.join(cmd)}[/info]")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[success]✓ Update complete[/success]")
    else:
        console.print(f"[error]Update failed:\n{result.stderr}[/error]")


if __name__ == "__main__":
    app()

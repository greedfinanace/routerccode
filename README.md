# 🚀 RouterCode CLI

A production-grade CLI coding agent powered by OpenRouter. Model-agnostic, ReAct-driven, and designed for surgical codebase modifications.

## ✨ Features

* **ReAct Agentic Loop:** Continuous Reason+Act cycle with autonomous tool execution.
* **Multi-Layer Context Compression:** 4-layer pipeline prevents context window overflow.
* **Secure API Key Management:** OS keychain via keyring, with environment variable fallback.
* **Surgical File Editing:** Search-and-replace diffs and line-range edits (no full rewrites).
* **MCP Integration:** Model Context Protocol for extensible tool discovery.
* **Self-Healing Loop:** Autonomous test-fix-test debugging with circuit breakers.
* **Beautiful Terminal UI:** Rich-powered interface with high-signal output.
* **Session Management:** Continue, fork, rewind, and export conversations.
* **Codebase Mapping:** AST-based repository skeleton generation.
* **Prompt Caching:** Cost reduction via smart payload structuring.

## 📦 Installation

### Option 1: Python (Recommended)
Install directly using pip:
```bash
pip install .
```

### Option 2: NPM
Install globally using npm:
```bash
npm install -g .
```

### Option 3: Bun
Install globally using bun:
```bash
bun install -g .
```

## 🚀 Quick Start

```bash
# First run: will prompt for API key
routercode

# Direct query
routercode "refactor the auth module to use JWT"

# Print mode (non-interactive)
routercode -p "explain this codebase"

# Continue last session
routercode -c

# Specify model
routercode --model "anthropic/claude-3.5-sonnet" "fix the failing tests"
```

## ⌨️ Slash Commands

| Command | Description |
|:---|:---|
| `/compact` | Compress context with optional focus |
| `/clear` | Hard reset conversation |
| `/model <name>` | Switch model mid-session |
| `/fork` | Branch conversation |
| `/diff` | View file changes |
| `/context` | Visualize token usage |
| `/cost` | Show API spending |
| `/export` | Save conversation |
| `/help` | Show all commands |
| `/quit` | Exit session |

## 🔐 Permission Modes

| Mode | Behavior |
|:---|:---|
| `plan` | Read-only, no edits or execution |
| `ask-first` | Prompt before tool calls (default) |
| `auto` | Auto-approve edits, prompt for destructive commands |
| `bypass` | Full automation (CI/CD only) |

```bash
routercode --mode auto "implement the feature from issue #42"
```

## 🏗️ Architecture

* `main.py`: Typer CLI and ReAct agentic loop
* `api_client.py`: OpenRouter streaming client
* `context.py`: 4-layer context compression
* `tools.py`: Tool schemas and execution engine
* `key_manager.py`: Secure API key management
* `self_heal.py`: Autonomous test-fix-test loop
* `codebase_map.py`: AST-based repo mapping

## 🧪 Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

## 📄 License

MIT

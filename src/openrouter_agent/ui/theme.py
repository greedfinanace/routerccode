"""
Rich Terminal UI Theme - Catppuccin Mocha Aesthetic.

The visual identity of the OpenRouter Agent CLI:
  - #cba6f7 (mauve) primary accent
  - #89b4fa (blue) secondary accent
  - Catppuccin pastel palette for reduced visual fatigue
  - Modern, minimalist banner
"""

from __future__ import annotations

from rich.text import Text
from rich.theme import Theme

CATPPUCCIN_MOCHA_THEME = Theme({
    "info": "#89b4fa",
    "warning": "#f9e2af",
    "error": "bold #f38ba8",
    "success": "bold #a6e3a1",
    "tool_call": "#cba6f7 bold",
    "code": "#94e2d5",
    "border": "#585b70",
    "dim": "#6c7086",
    "accent": "#b4befe bold",
    "cost": "bold #fab387",
    "model_id": "bold #89dceb",
})


def create_banner() -> Text:
    """Create the startup banner for the interactive REPL."""
    banner = Text()
    banner.append("\n")

    lines = [
        "                                 __                                                          __           ",
        "                                /  |                                                        /  |          ",
        "  ______    ______   __    __  _$$ |_     ______    ______          _______   ______    ____$$ |  ______  ",
        " /      \\  /      \\ /  |  /  |/ $$   |   /      \\  /      \\        /       | /      \\  /    $$ | /      \\ ",
        "/$$$$$$  |/$$$$$$  |$$ |  $$ |$$$$$$/   /$$$$$$  |/$$$$$$  |      /$$$$$$$/ /$$$$$$  |/$$$$$$$ |/$$$$$$  |",
        "$$ |  $$/ $$ |  $$ |$$ |  $$ |  $$ | __ $$    $$ |$$ |  $$/       $$ |      $$ |  $$ |$$ |  $$ |$$    $$ |",
        "$$ |      $$ \\__$$ |$$ \\__$$ |  $$ |/  |$$$$$$$$/ $$ |            $$ \\_____ $$ \\__$$ |$$ \\__$$ |$$$$$$$$/ ",
        "$$ |      $$    $$/ $$    $$/   $$  $$/ $$       |$$ |            $$       |$$    $$/ $$    $$ |$$       |",
        "$$/        $$$$$$/   $$$$$$/     $$$$/   $$$$$$$/ $$/              $$$$$$$/  $$$$$$/   $$$$$$$/  $$$$$$$/ ",
        "                                                                                                          ",
        "              Production-grade coding assistant - Vibecoding Era - Powered by OpenRouter                  "
    ]

    colors = [
        "#cba6f7", "#f5c2e7", "#f2cdcd", "#eba0ac", "#f38ba8",
        "#f5c2e7", "#cba6f7", "#89b4fa", "#89dceb", "#94e2d5",
        "#a6e3a1", "#cba6f7"
    ]

    for i, line in enumerate(lines):
        color = colors[i % len(colors)]
        banner.append(line + "\n", style=f"bold {color}")

    return banner

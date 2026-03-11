"""Validate and normalize tool-call actions from the LLM.

With native function calling, the LLM returns structured tool calls
(name + typed args).  This module maps them to Playwright CLI commands
and validates element refs.
"""

from __future__ import annotations

from dataclasses import dataclass

from browser_agent.tool_definitions import tool_call_to_cli


class ActionParseError(RuntimeError):
    """Raised when a tool call cannot be converted to a valid CLI command."""


@dataclass(slots=True)
class ParsedAction:
    action: str          # Full CLI command string (e.g. "playwright-cli click e12")
    command: str         # CLI command name (e.g. "click")
    args: list[str]      # CLI positional args
    tool_name: str       # Original tool name from LLM
    tool_args: dict[str, str]  # Original tool args from LLM


def parse_tool_call(tool_name: str, tool_args: dict[str, str]) -> ParsedAction:
    """Convert a structured tool call into a validated ParsedAction."""
    if tool_name == "finish":
        return ParsedAction(
            action="",
            command="finish",
            args=[],
            tool_name=tool_name,
            tool_args=tool_args,
        )

    # Validate element refs where required.
    _validate_ref_args(tool_name, tool_args)

    cli_command = tool_call_to_cli(tool_name, tool_args)
    if cli_command is None:
        raise ActionParseError(f"Unknown tool: {tool_name}")

    # Split into command + args for downstream use.
    parts = cli_command.split()
    # parts[0] = "playwright-cli", parts[1] = command, rest = args
    command = parts[1] if len(parts) > 1 else tool_name
    args = parts[2:] if len(parts) > 2 else []

    return ParsedAction(
        action=cli_command,
        command=command,
        args=args,
        tool_name=tool_name,
        tool_args=tool_args,
    )


def _validate_ref_args(tool_name: str, tool_args: dict[str, str]) -> None:
    """Validate that element ref args look like e1, e2, etc."""
    _REF_TOOLS = {
        "click": ["ref"],
        "dblclick": ["ref"],
        "hover": ["ref"],
        "fill": ["ref"],
        "select": ["ref"],
        "check": ["ref"],
        "uncheck": ["ref"],
        "upload": ["ref"],
        "drag": ["source_ref", "target_ref"],
    }

    ref_keys = _REF_TOOLS.get(tool_name)
    if not ref_keys:
        return

    for key in ref_keys:
        value = tool_args.get(key, "")
        if not _is_element_ref(value):
            raise ActionParseError(
                f"Tool '{tool_name}' requires a valid element ref for '{key}' "
                f"(e.g. e12), got: '{value}'"
            )


def _is_element_ref(value: str) -> bool:
    return len(value) >= 2 and value.startswith("e") and value[1:].isdigit()

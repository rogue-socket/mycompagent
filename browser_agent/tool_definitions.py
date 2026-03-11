"""Tool definitions for Gemini function calling.

Each Playwright CLI command is defined as a structured tool so the LLM
returns typed function calls instead of free-form text.
"""

from __future__ import annotations

import shlex

from google.genai import types

# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------

_TOOLS: list[types.FunctionDeclaration] = [
    # -- Element interaction --
    types.FunctionDeclaration(
        name="click",
        description="Click an element by its snapshot reference.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref from snapshot (e.g. e12)"),
            },
            required=["ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="dblclick",
        description="Double-click an element.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref"),
            },
            required=["ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="hover",
        description="Hover over an element.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref"),
            },
            required=["ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="fill",
        description="Clear a form field and type new text into it.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref of the input field"),
                "value": types.Schema(type="STRING", description="Text to enter"),
            },
            required=["ref", "value"],
        ),
    ),
    types.FunctionDeclaration(
        name="type",
        description="Type text character-by-character into the currently focused element. Use fill instead when targeting a specific element.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "text": types.Schema(type="STRING", description="Text to type"),
            },
            required=["text"],
        ),
    ),
    types.FunctionDeclaration(
        name="press",
        description="Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown).",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "key": types.Schema(type="STRING", description="Key name (e.g. Enter, Tab, Escape, ArrowDown, Backspace)"),
            },
            required=["key"],
        ),
    ),
    types.FunctionDeclaration(
        name="select",
        description="Select an option from a dropdown/select element.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref of the select"),
                "value": types.Schema(type="STRING", description="Option value to select"),
            },
            required=["ref", "value"],
        ),
    ),
    types.FunctionDeclaration(
        name="check",
        description="Check a checkbox.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref of the checkbox"),
            },
            required=["ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="uncheck",
        description="Uncheck a checkbox.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref of the checkbox"),
            },
            required=["ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="drag",
        description="Drag one element to another.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "source_ref": types.Schema(type="STRING", description="Element ref to drag from"),
                "target_ref": types.Schema(type="STRING", description="Element ref to drag to"),
            },
            required=["source_ref", "target_ref"],
        ),
    ),
    types.FunctionDeclaration(
        name="upload",
        description="Upload a file to a file input element.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "ref": types.Schema(type="STRING", description="Element ref of the file input"),
                "file_path": types.Schema(type="STRING", description="Path to the file to upload"),
            },
            required=["ref", "file_path"],
        ),
    ),
    # -- Navigation --
    types.FunctionDeclaration(
        name="goto",
        description="Navigate to a URL.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "url": types.Schema(type="STRING", description="URL to navigate to"),
            },
            required=["url"],
        ),
    ),
    types.FunctionDeclaration(
        name="go_back",
        description="Go back to the previous page (browser back button).",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    types.FunctionDeclaration(
        name="go_forward",
        description="Go forward to the next page (browser forward button).",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    types.FunctionDeclaration(
        name="reload",
        description="Reload the current page.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    # -- Page info --
    types.FunctionDeclaration(
        name="snapshot",
        description="Take an accessibility snapshot of the current page to see element refs and page structure.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    types.FunctionDeclaration(
        name="screenshot",
        description="Take a screenshot of the current page.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    # -- Tabs --
    types.FunctionDeclaration(
        name="tab_list",
        description="List all open browser tabs.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    types.FunctionDeclaration(
        name="tab_new",
        description="Open a new browser tab, optionally at a URL.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "url": types.Schema(type="STRING", description="Optional URL to open in the new tab"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="tab_close",
        description="Close a browser tab by index.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "index": types.Schema(type="STRING", description="Tab index to close (default: current tab)"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="tab_select",
        description="Switch to a browser tab by index.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "index": types.Schema(type="STRING", description="Tab index to switch to"),
            },
            required=["index"],
        ),
    ),
    # -- Session --
    types.FunctionDeclaration(
        name="state_save",
        description="Save browser session state (cookies, localStorage) to a file.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "path": types.Schema(type="STRING", description="File path to save state to"),
            },
            required=["path"],
        ),
    ),
    types.FunctionDeclaration(
        name="state_load",
        description="Load browser session state from a file.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "path": types.Schema(type="STRING", description="File path to load state from"),
            },
            required=["path"],
        ),
    ),
    # -- Browser control --
    types.FunctionDeclaration(
        name="close",
        description="Close the browser.",
        parameters=types.Schema(type="OBJECT", properties={}),
    ),
    # -- Completion --
    types.FunctionDeclaration(
        name="finish",
        description="Call this when the task is complete. Provide a summary of what was accomplished.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "reason": types.Schema(type="STRING", description="Summary of what was accomplished and why the task is complete"),
            },
            required=["reason"],
        ),
    ),
]

TOOL_DECLARATIONS = types.Tool(function_declarations=_TOOLS)

# ---------------------------------------------------------------------------
# Tool-call → CLI command mapping
# ---------------------------------------------------------------------------

# Map function names with underscores to their playwright-cli equivalents.
_CLI_NAME_MAP: dict[str, str] = {
    "go_back": "go-back",
    "go_forward": "go-forward",
    "tab_list": "tab-list",
    "tab_new": "tab-new",
    "tab_close": "tab-close",
    "tab_select": "tab-select",
    "state_save": "state-save",
    "state_load": "state-load",
}


def tool_call_to_cli(name: str, args: dict[str, str]) -> str | None:
    """Convert a Gemini function call to a playwright-cli command string.

    Returns ``None`` for the ``finish`` tool (not a CLI command).
    """
    if name == "finish":
        return None

    cli_name = _CLI_NAME_MAP.get(name, name)
    parts = ["playwright-cli", cli_name]

    # Build argument list based on the specific tool.
    if name in {"click", "dblclick", "hover", "check", "uncheck"}:
        parts.append(args["ref"])
    elif name == "fill":
        parts.extend([args["ref"], args["value"]])
    elif name == "type":
        parts.append(args["text"])
    elif name == "press":
        parts.append(args["key"])
    elif name == "select":
        parts.extend([args["ref"], args["value"]])
    elif name == "drag":
        parts.extend([args["source_ref"], args["target_ref"]])
    elif name == "upload":
        parts.extend([args["ref"], args["file_path"]])
    elif name == "goto":
        url = args["url"]
        if "://" not in url and "." in url:
            url = "https://" + url
        parts.append(url)
    elif name in {"tab_new", "tab_close", "tab_select"}:
        if name == "tab_new" and args.get("url"):
            parts.append(args["url"])
        elif name == "tab_close" and args.get("index"):
            parts.append(args["index"])
        elif name == "tab_select":
            parts.append(args["index"])
    elif name in {"state_save", "state_load"}:
        parts.append(args["path"])
    # snapshot, screenshot, go_back, go_forward, reload, close — no extra args

    return " ".join(shlex.quote(part) for part in parts)

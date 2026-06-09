"""Tool definitions for Gemini function calling.

Each Playwright CLI command is defined as a structured tool so the LLM
returns typed function calls instead of free-form text.
"""

from __future__ import annotations

import shlex
import re

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
        name="format_selection",
        description=(
            "Apply rich-text formatting to the current selection in the focused "
            "editable field. Focus the editor and select text first."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "format": types.Schema(
                    type="STRING",
                    description="Formatting command, such as bold, italic, underline, or strikeThrough.",
                ),
            },
            required=["format"],
        ),
    ),
    types.FunctionDeclaration(
        name="scroll",
        description="Scroll the page vertically with the mouse wheel. Positive dy scrolls down; negative dy scrolls up.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "dy": types.Schema(type="STRING", description="Vertical scroll amount, e.g. 900 or -900"),
            },
            required=["dy"],
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
        name="draw_circle",
        description=(
            "Draw one circular mouse path around the visible center target on a "
            "canvas-style page. Use for drawing games that ask for a freehand circle."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "radius": types.Schema(
                    type="STRING",
                    description="Optional pixel radius, default 170, clamped to a safe range.",
                ),
                "steps": types.Schema(
                    type="STRING",
                    description="Optional number of path segments, default 24.",
                ),
            },
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
    types.FunctionDeclaration(
        name="ask_human",
        description=(
            "Ask the human operator for missing information that is visible to "
            "them but unavailable in the page state, such as a CAPTCHA. Use this "
            "instead of finishing when the task can continue with a short answer."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "question": types.Schema(
                    type="STRING",
                    description="Short, specific question to show the human operator.",
                ),
                "reason": types.Schema(
                    type="STRING",
                    description="Why human input is needed.",
                ),
            },
            required=["question"],
        ),
    ),
    types.FunctionDeclaration(
        name="password_game_elements",
        description=(
            "Compute Neal.fun Password Game Rule 18 element sums and suggest an "
            "edit. Use this before changing a password for the atomic-number rule."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "password": types.Schema(
                    type="STRING",
                    description="Current visible password text.",
                ),
                "target": types.Schema(
                    type="STRING",
                    description="Target atomic-number sum, default 200.",
                ),
            },
            required=["password"],
        ),
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
                "output": types.Schema(type="STRING", description="Optional concise answer or extracted data for the caller"),
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
    "scroll": "mousewheel",
}


def tool_call_to_cli(name: str, args: dict[str, str]) -> str | None:
    """Convert a Gemini function call to a playwright-cli command string.

    Returns ``None`` for non-CLI tools.
    """
    if name in {"finish", "ask_human", "password_game_elements"}:
        return None
    if name == "draw_circle":
        return _draw_circle_command(args)
    if name == "format_selection":
        return _format_selection_command(args)

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
    elif name == "scroll":
        parts.extend([args.get("dy", "900"), "0"])
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


def _draw_circle_command(args: dict[str, str]) -> str:
    radius = _int_arg(args.get("radius"), default=170, minimum=40, maximum=320)
    steps = _int_arg(args.get("steps"), default=24, minimum=16, maximum=72)
    code = f"""async page => {{
  const requestedRadius = {radius};
  const steps = {steps};
  const target = await page.evaluate((requestedRadius) => {{
    const visible = (el) => {{
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return (
        rect.width > 5 &&
        rect.height > 5 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden'
      );
    }};
    const rectInfo = (el) => {{
      const rect = el.getBoundingClientRect();
      const cx = rect.x + rect.width / 2;
      const cy = rect.y + rect.height / 2;
      return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height, cx, cy }};
    }};
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const main = document.querySelector('main') || document.body;
    const candidates = Array.from(main.querySelectorAll('canvas, img, svg'))
      .filter(visible)
      .map(rectInfo)
      .sort((a, b) => {{
        const da = Math.hypot(a.cx - vw / 2, a.cy - vh / 2);
        const db = Math.hypot(b.cx - vw / 2, b.cy - vh / 2);
        return da - db;
      }});
    const center = candidates[0] || {{ cx: vw / 2, cy: vh / 2 }};
    const edgeLimit = Math.max(
      40,
      Math.min(center.cx - 8, vw - center.cx - 8, center.cy - 8, vh - center.cy - 8)
    );
    const radius = Math.max(40, Math.min(requestedRadius, edgeLimit));
    return {{ cx: center.cx, cy: center.cy, radius }};
  }}, requestedRadius);
  await page.mouse.move(target.cx + target.radius, target.cy);
  await page.mouse.down();
  for (let i = 1; i <= steps; i += 1) {{
    const angle = (Math.PI * 2 * i) / steps;
    await page.mouse.move(
      target.cx + Math.cos(angle) * target.radius,
      target.cy + Math.sin(angle) * target.radius
    );
  }}
  await page.mouse.up();
  await page.waitForTimeout(1500);
  return `Drew circle at ${{Math.round(target.cx)}},${{Math.round(target.cy)}} radius ${{Math.round(target.radius)}}`;
}}"""
    return "playwright-cli run-code " + shlex.quote(code)


def _format_selection_command(args: dict[str, str]) -> str:
    command = _format_command(args.get("format", "bold"))
    code = f"""async page => {{
  const command = {command!r};
  const active = await page.evaluate((command) => {{
    const before = String(window.getSelection ? window.getSelection() : '');
    const ok = document.execCommand(command, false, null);
    const active = document.activeElement;
    const html = active && active.innerHTML ? active.innerHTML.slice(0, 500) : '';
    return {{ ok, selectedText: before.slice(0, 200), activeTag: active ? active.tagName : '', html }};
  }}, command);
  return active;
}}"""
    return "playwright-cli run-code " + shlex.quote(code)


def _format_command(value: str | None) -> str:
    normalized = re.sub(r"[^A-Za-z]", "", str(value or "")).lower()
    allowed = {
        "bold": "bold",
        "italic": "italic",
        "underline": "underline",
        "strikethrough": "strikeThrough",
    }
    return allowed.get(normalized, "bold")


def _int_arg(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value)) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))

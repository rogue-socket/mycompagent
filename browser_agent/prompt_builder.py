"""Build ReAct-style prompts for the browser agent."""

from __future__ import annotations

from browser_agent.constants import ALLOWED_COMMANDS
from browser_agent.interpreter import InterpreterState


def build_prompt(
    task: str,
    state: InterpreterState,
    action_history: list[str],
    max_elements: int,
    skill_text: str | None = None,
) -> str:
    element_lines = [
        f"{e.element_id}: {e.element_type} - {e.text}" for e in state.clickable_elements[:max_elements]
    ]
    history_lines = action_history[-12:]

    instructions = (
        "You are a browser automation agent. Use the Playwright CLI to control the browser.\n"
        "Return strict JSON only, no markdown.\n"
        "Schema:\n"
        "{\n"
        '  "thought": "internal reasoning",\n'
        '  "action": "playwright-cli <command> [args]",\n'
        '  "reasoning_summary": "brief summary for logs",\n'
        '  "final": false\n'
        "}\n"
        "Only use allowed commands. Never invent element ids.\n"
        "The action MUST start with 'playwright-cli '.\n"
        "Do not use element actions (click/fill/check/etc.) unless you have a valid element ref (e1, e2, ...).\n"
        "Do NOT use selector flags like --selector; only use element refs from the snapshot.\n"
        "Only use flags for: open(--persistent/--headed/--browser/--profile/--config), "
        "snapshot(--filename), screenshot(--filename), pdf(--filename), "
        "cookie-list(--domain/--path), "
        "cookie-set(--domain/--path/--httpOnly/--secure/--sameSite/--expires), "
        "route(--status/--body/--content-type/--header/--remove-header).\n"
        "Do NOT call 'open' inside the loop; the browser is already open.\n"
        "Do NOT specify --browser unless the user explicitly asked for a specific browser.\n"
        "If the task is complete, set final=true, explain why in reasoning_summary, and choose a safe final action like snapshot.\n"
    )

    skill_section = ""
    if skill_text:
        skill_section = "Skill guidance (use this):\n" + skill_text.strip() + "\n\n"

    prompt = (
        f"Goal:\n{task}\n\n"
        f"Allowed commands:\n{', '.join(sorted(ALLOWED_COMMANDS))}\n\n"
        f"Current page:\nURL: {state.url}\nTitle: {state.title}\nType: {state.page_type}\n\n"
        f"Page summary:\n{state.page_summary}\n\n"
        "Clickable elements:\n"
        + ("\n".join(element_lines) if element_lines else "(none)")
        + "\n\n"
        "Visible text (truncated):\n"
        + (state.visible_text[:800] if state.visible_text else "(none)")
        + "\n\n"
        "Previous actions:\n"
        + ("\n".join(history_lines) if history_lines else "(none)")
        + "\n\n"
        "Decide the next best action."
    )

    return instructions + "\n" + skill_section + prompt

"""Build system instructions and per-step messages for the chat planner."""

from __future__ import annotations

from browser_agent.interpreter import InterpreterState


def build_system_instruction(task: str, skill_text: str | None = None) -> str:
    """Build a one-time system instruction for the chat session.

    This is set once when the chat starts and never changes.
    """
    parts = [
        "You are a browser automation agent. You control a browser using tool calls.",
        "You have tools for clicking, filling forms, navigating, taking snapshots, etc.",
        "Each step you will receive the current page state (URL, title, elements, visible text).",
        "",
        "## How to think (ReAct pattern)",
        "",
        "Before EVERY tool call, you MUST think step by step. Write your reasoning as text",
        "BEFORE making the tool call. Follow this pattern every step:",
        "",
        "1. **Observe**: What does the current page state show? What URL am I on? What elements are available?",
        "2. **Think**: What progress have I made toward the goal? What should I do next and why?",
        "   Consider what happened on previous steps — did my last action succeed? Am I stuck in a loop?",
        "3. **Act**: Call exactly ONE tool based on your reasoning.",
        "",
        "Always emit your reasoning text BEFORE the tool call in the same response.",
        "",
        "## Rules",
        "",
        "- Only use element refs (e1, e2, ...) from the most recent page state. Never invent refs.",
        "- Use 'fill' to enter text into a specific input field. Use 'type' only for the focused element.",
        "- Use 'press' for keyboard keys like Enter, Tab, Escape.",
        "- Call 'finish' when the task is complete.",
        "- If you are stuck, try 'snapshot' to see the current page state.",
        "- If your previous action failed, try a different approach instead of repeating it.",
        "- After typing in a search box, press Enter or click the search button to submit.",
        "",
        "## Examples of good reasoning",
        "",
        _FEW_SHOT_EXAMPLES,
        "",
        f"## Goal\n\n{task}",
    ]

    if skill_text:
        parts.extend(["", "## Reference documentation", "", skill_text.strip()])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Few-shot examples (reasoning + tool call patterns)
# ---------------------------------------------------------------------------

_FEW_SHOT_EXAMPLES = """### Example 1 — Searching
Page shows google.com with a search box (e37) and a "Google Search" button (e60).
Goal: Search for "weather in London"

Good reasoning:
"I'm on google.com. I can see a search combobox at e37 and a Google Search button at e60.
I need to search for 'weather in London'. I'll fill the search box first."
→ Tool call: fill(ref="e37", value="weather in London")

Next step reasoning:
"I filled the search box with 'weather in London'. Now I need to submit the search.
I can either click the search button e60 or press Enter. I'll press Enter."
→ Tool call: press(key="Enter")

### Example 2 — Navigating to a link
Page shows search results with links: e5 "Weather in London - BBC", e8 "London Weather - Met Office".
Goal: Find the BBC weather for London.

Good reasoning:
"The search results are showing. I can see e5 'Weather in London - BBC' which matches my goal.
I'll click that link to go to the BBC weather page."
→ Tool call: click(ref="e5")

### Example 3 — Completing a task
Page shows the BBC weather page with today's forecast: "15°C, Partly Cloudy".
Goal: Find the current weather in London.

Good reasoning:
"I'm on the BBC weather page. I can see the current forecast is 15°C, Partly Cloudy.
This is what the user asked for. The task is complete."
→ Tool call: finish(reason="Found London weather: 15°C, Partly Cloudy according to BBC Weather")

### Example 4 — Recovery from error
Previous action failed: "Element e25 not found in current page"

Good reasoning:
"My last click on e25 failed because that element doesn't exist on this page anymore.
The page may have changed. Let me take a snapshot to see the current state."
→ Tool call: snapshot()

### Common mistakes to avoid
- Do NOT call click immediately after fill — press Enter or click the search/submit button instead.
- Do NOT repeat the same failing action. If it failed once, try something different.
- Do NOT invent element refs. Only use refs from the most recent page state.
- Do NOT call fill on a non-input element. Check the element type first.
- Do NOT call finish until the task is actually complete and you can see the result."""


def build_page_message(
    state: InterpreterState,
    action_history: list[str],
    max_elements: int = 60,
    last_error: str | None = None,
) -> str:
    """Build a per-step user message with current page state."""
    element_lines = [
        f"{e.element_id}: {e.element_type} - {e.text}"
        for e in state.clickable_elements[:max_elements]
    ]
    history_lines = action_history[-12:]

    sections = [
        f"Current page:\nURL: {state.url}\nTitle: {state.title}\nType: {state.page_type}",
        f"Page summary:\n{state.page_summary}",
        "Clickable elements:\n" + ("\n".join(element_lines) if element_lines else "(none)"),
        "Visible text (truncated):\n" + (state.visible_text[:800] if state.visible_text else "(none)"),
        "Previous actions:\n" + ("\n".join(history_lines) if history_lines else "(none)"),
    ]

    if last_error:
        sections.append(f"IMPORTANT - Last action failed:\n{last_error}\nTry a different approach.")

    sections.append("Call the appropriate tool for the next action.")

    return "\n\n".join(sections)

"""Build system instructions and per-step messages for the chat planner."""

from __future__ import annotations

import re

from browser_agent.interpreter import InterpreterState
from browser_agent.route_planner import wikipedia_route_hints


def build_system_instruction(
    task: str,
    skill_text: str | None = None,
    tier1_lessons: list | None = None,
) -> str:
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
        "- If 'fill' fails, use click(ref) to focus the input first, then type(text) to enter the text.",
        "- Use 'press' for keyboard keys like Enter, Tab, Escape.",
        "- After entering text in a search box, press Enter to submit. Do NOT click the search button —",
        "  autocomplete dropdowns often cover it and cause timeout errors.",
        "- Call 'finish' when the task is complete.",
        "- If you are stuck, try 'snapshot' to see the current page state.",
        "- If your previous action failed, try a different approach instead of repeating it.",
        "",
        "## Examples of good reasoning",
        "",
        _FEW_SHOT_EXAMPLES,
        "",
        f"## Goal\n\n{task}",
    ]

    if tier1_lessons:
        lesson_lines = [f"- {ls.lesson}" for ls in tier1_lessons]
        parts.extend([
            "",
            "## Lessons from experience",
            "",
            "These are lessons learned from previous runs. Follow them.",
            *lesson_lines,
        ])

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
I should press Enter rather than clicking the search button, because autocomplete dropdowns often cover it."
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

### Example 5 — Recovery from fill failure
Previous action failed: fill(ref="e37", value="padel rackets") → "too many arguments"

Good reasoning:
"My fill action failed. I'll try an alternative approach: click the input field first to focus it,
then use type to enter the text."
→ Tool call: click(ref="e37")

Next step:
"I clicked e37 to focus it. Now I'll type my search text."
→ Tool call: type(text="padel rackets")

### Common mistakes to avoid
- Do NOT click search/submit buttons after entering text — autocomplete dropdowns often cover them. Use press(key="Enter") instead.
- Do NOT repeat the same failing action. If it failed once, try something different.
- Do NOT invent element refs. Only use refs from the most recent page state.
- Do NOT call fill on a non-input element. Check the element type first.
- Do NOT call finish until the task is actually complete and you can see the result."""


def build_page_message(
    state: InterpreterState,
    action_history: list[str],
    max_elements: int = 60,
    last_error: str | None = None,
    domain_context: str | None = None,
    task: str | None = None,
    evidence_text: str | None = None,
) -> str:
    """Build a per-step user message with current page state."""
    selected_elements = _select_clickable_elements(
        state.clickable_elements,
        max_elements,
        task=task,
        current_title=state.title,
    )
    if state.page_type == "article":
        article_lines = [
            _format_element_line(e.element_id, e.element_type, e.text, e.href, e.area)
            for e in selected_elements
            if getattr(e, "area", "other") == "article"
        ]
        other_lines = [
            _format_element_line(e.element_id, e.element_type, e.text, e.href, e.area)
            for e in selected_elements
            if getattr(e, "area", "other") != "article"
        ]
        clickable_section = "\n\n".join(
            [
                "Article content links:\n"
                + ("\n".join(article_lines) if article_lines else "(none)"),
                "Other clickable elements:\n"
                + ("\n".join(other_lines) if other_lines else "(none)"),
            ]
        )
    else:
        element_lines = [
            _format_element_line(e.element_id, e.element_type, e.text, e.href, e.area)
            for e in selected_elements
        ]
        clickable_section = "Clickable elements:\n" + (
            "\n".join(element_lines) if element_lines else "(none)"
        )
    history_lines = action_history[-12:]
    evidence_snippets = _task_evidence_snippets(
        state.visible_text,
        evidence_text or "",
        task or "",
        state.title,
    )
    redirect_note = _redirect_or_canonical_note(
        task or "",
        state.title,
        evidence_text or "",
    )
    route_quality_note = _route_quality_note(state, selected_elements, task or "")
    route_hints = wikipedia_route_hints(state, task or "")

    sections = [
        f"Current page:\nURL: {state.url}\nTitle: {state.title}\nType: {state.page_type}",
        f"Page summary:\n{state.page_summary}",
        clickable_section,
        "Visible text (truncated):\n" + (state.visible_text[:800] if state.visible_text else "(none)"),
        "Previous actions:\n" + ("\n".join(history_lines) if history_lines else "(none)"),
    ]

    if evidence_snippets:
        evidence_lines = [f"- {snippet}" for snippet in evidence_snippets]
        sections.insert(3, "Task-focused evidence:\n" + "\n".join(evidence_lines))

    if redirect_note:
        sections.insert(2, "Redirect/canonical note:\n" + redirect_note)

    if route_quality_note:
        sections.insert(2, "Route-quality note:\n" + route_quality_note)

    if route_hints:
        hint_lines = [
            (
                f"- {hint.element.element_id}: {_quoted_label(hint.element.text)} "
                f"({hint.reason})"
            )
            for hint in route_hints
        ]
        sections.insert(2, "Route helper candidates:\n" + "\n".join(hint_lines))

    if last_error:
        recovery_note = _custom_control_recovery_note(last_error, selected_elements, task or "")
        if recovery_note:
            sections.append("Custom control recovery:\n" + recovery_note)
        sections.append(f"IMPORTANT - Last action failed:\n{last_error}\nTry a different approach.")

    if domain_context:
        sections.append(f"Tips for this site:\n{domain_context}")

    sections.append("Call the appropriate tool for the next action.")

    return "\n\n".join(sections)


def _format_element_line(
    element_id: str,
    element_type: str,
    text: str,
    href: str,
    area: str = "other",
) -> str:
    suffix = f" -> {href}" if href else ""
    return f"{element_id}: [{area}] {element_type} - {text}{suffix}"


def _select_clickable_elements(
    elements: list,
    max_elements: int,
    *,
    task: str | None,
    current_title: str,
) -> list:
    if len(elements) <= max_elements:
        return elements

    scored: list[tuple[int, int, object]] = []
    terms = _task_terms(task or "", current_title)
    if terms:
        for index, element in enumerate(elements):
            score = _element_relevance_score(element.text, element.href, terms)
            if score:
                scored.append((score, -index, element))

    selected: list = []
    seen: set[str] = set()
    for _, _, element in sorted(scored, reverse=True):
        selected.append(element)
        seen.add(element.element_id)
        if len(selected) >= max_elements // 2:
            break

    for element in elements:
        if element.element_id in seen:
            continue
        selected.append(element)
        if len(selected) >= max_elements:
            break
    return selected


def _task_terms(task: str, current_title: str) -> set[str]:
    stopwords = {
        "article",
        "articles",
        "blue",
        "click",
        "clicking",
        "finish",
        "given",
        "goal",
        "hyperlink",
        "hyperlinks",
        "link",
        "links",
        "minimum",
        "number",
        "only",
        "page",
        "route",
        "search",
        "start",
        "starting",
        "time",
        "title",
        "url",
        "website",
        "wikipedia",
    }
    title_terms = {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", current_title)
    }
    terms = {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", task)
        if word.lower() not in stopwords
    }
    return terms - title_terms


def _element_relevance_score(text: str, href: str, terms: set[str]) -> int:
    haystack = f"{text} {href}".lower()
    label = _quoted_label(text).lower()
    score = 0
    for term in terms:
        if label == term:
            score += 100
        elif term in label:
            score += 40
        elif term in haystack:
            score += 15
    return score


def _quoted_label(text: str) -> str:
    match = re.search(r'"([^"]+)"', text)
    return match.group(1) if match else text


def _custom_control_recovery_note(last_error: str, selected_elements: list, task: str) -> str:
    lowered_error = last_error.lower()
    if "selectoption" not in lowered_error and "not a <select>" not in lowered_error:
        return ""

    candidates = [
        element
        for element in selected_elements
        if getattr(element, "element_type", "") in {"option", "button"}
    ]
    if not candidates:
        return ""

    terms = _task_terms(task, "")
    matching = [
        element
        for element in candidates
        if _element_relevance_score(element.text, getattr(element, "href", ""), terms)
    ]
    if not matching:
        matching = candidates

    candidate_lines = [
        f"- {element.element_id}: {_quoted_label(element.text)} ({element.element_type})"
        for element in matching[:5]
    ]
    return (
        "The failed select target is not a native <select>. For ARIA/custom dropdowns, "
        "do not retry select or guess keyboard navigation when visible option/button refs "
        "match the requested value. Click the combobox/listbox trigger if needed, then "
        "click the matching option/button ref.\n"
        "Visible candidates:\n"
        + "\n".join(candidate_lines)
    )


def _task_evidence_snippets(
    visible_text: str,
    evidence_text: str,
    task: str,
    current_title: str,
    *,
    max_snippets: int = 4,
    window_chars: int = 180,
) -> list[str]:
    terms = _task_terms(task, current_title)
    if not terms:
        return []

    combined = "\n".join(part for part in (visible_text, evidence_text[:200_000]) if part)
    normalized = re.sub(r"\s+", " ", combined).strip()
    if not normalized:
        return []

    snippets: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        for match in pattern.finditer(normalized):
            start = max(0, match.start() - window_chars)
            end = min(len(normalized), match.end() + window_chars)
            snippet = normalized[start:end].strip()
            if start > 0:
                snippet = "... " + snippet
            if end < len(normalized):
                snippet += " ..."
            dedupe_key = snippet.lower()
            if dedupe_key in seen:
                continue
            snippets.append(snippet)
            seen.add(dedupe_key)
            if len(snippets) >= max_snippets:
                return snippets
    return snippets


def _redirect_or_canonical_note(
    task: str,
    current_title: str,
    evidence_text: str,
) -> str:
    if not task or not evidence_text:
        return ""

    task_targets = _task_target_candidates(task)
    if not task_targets:
        return ""

    redirected_from = _redirected_from_titles(evidence_text)
    if not redirected_from:
        return ""

    normalized_targets = {_normalize_article_title(target) for target in task_targets}
    for source_title in redirected_from:
        normalized_source = _normalize_article_title(source_title)
        if normalized_source in normalized_targets:
            canonical_title = _strip_site_suffix(current_title)
            return (
                f"This page is the canonical article for task target "
                f"'{source_title}' (current title: '{canonical_title}'). "
                "If that target is the goal, call finish instead of looking for a "
                "redirect-disabled duplicate link."
            )
    return ""


def _task_target_candidates(task: str) -> list[str]:
    numbered = re.findall(r"(?:^|[\n,;])\s*\d+\.\s*([^,\n;.]+)", task)
    if numbered:
        return [_clean_task_target(item) for item in numbered if item.strip()]

    arrow_match = re.search(r"->\s*([^.\n;]+)", task)
    if arrow_match:
        return [_clean_task_target(arrow_match.group(1))]

    from_to_match = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.\n;]|$)",
        task,
        re.IGNORECASE,
    )
    if from_to_match:
        return [_clean_task_target(from_to_match.group(2))]
    return []


def _clean_task_target(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"\s+on\s+wikipedia\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+wikipedia\s+article\b.*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _redirected_from_titles(text: str) -> list[str]:
    return [
        match.strip()
        for match in re.findall(r"Redirected\s+from\s+([A-Za-z0-9][^\\\n|]{1,80})", text)
        if match.strip()
    ]


def _strip_site_suffix(title: str) -> str:
    return re.sub(r"\s+-\s+Wikipedia\s*$", "", title).strip()


def _normalize_article_title(title: str) -> str:
    stripped = _strip_site_suffix(title)
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _route_quality_note(
    state: InterpreterState,
    selected_elements: list,
    task: str,
) -> str:
    if state.page_type != "article":
        return ""
    if _task_mentions_taxonomy(task):
        return ""
    taxonomy_links = [
        element
        for element in selected_elements
        if getattr(element, "area", "other") == "taxonomy"
    ]
    if not taxonomy_links:
        return ""
    examples = ", ".join(_quoted_label(link.text) for link in taxonomy_links[:3])
    return (
        "Visible links include biological taxonomy/classification links "
        f"({examples}). Avoid looping deeper into local classification unless it "
        "directly moves toward the target; prefer broader bridge links such as "
        "food, cuisine, culture, geography, technology, media, or history when "
        "they match the goal."
    )


def _task_mentions_taxonomy(task: str) -> bool:
    lowered = task.lower()
    return any(
        marker in lowered
        for marker in (
            "taxonomy",
            "taxonomic",
            "species",
            "genus",
            "biology",
            "biological",
        )
    )

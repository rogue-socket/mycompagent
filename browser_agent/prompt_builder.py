"""Build system instructions and per-step messages for the chat planner."""

from __future__ import annotations

import ast
import html
import re
import shlex

from browser_agent.interpreter import InterpreterState


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
        "- Cursor-pointer generic card/result refs shown in the page state are valid click targets.",
        "- When visible cards, result refs, or search controls exist, click/use those refs instead of guessing detail/entity URLs.",
        "- If a guessed detail/entity URL lands on a 404 or not-found page, go back, use visible refs, or search instead of guessing another entity URL.",
        "- Do not rely on visible text alone when a matching element ref is available.",
        "- Use 'fill' to enter text into a specific input field. Use 'type' only for the focused element.",
        "- If 'fill' fails, use click(ref) to focus the input first, then type(text) to enter the text.",
        "- Use 'press' for keyboard keys like Enter, Tab, Escape.",
        "- Use 'select_text' to select an exact substring in the focused editable before replacing or formatting only that substring.",
        "- Use 'format_selection' for rich-text formatting after focusing an editable field and selecting text.",
        "- If the current editable has rich HTML, avoid plain fill actions that replace the whole value; use targeted typing, selection-based formatting, or another formatting-preserving edit.",
        "- After entering text in a search box, press Enter to submit. Do NOT click the search button —",
        "  autocomplete dropdowns often cover it and cause timeout errors.",
        "- Check DOM evidence for image src, iframe src, links, active editable HTML, and button state before asking the human.",
        "- For short visual values, inspect image src/alt/title and any src_token in DOM evidence before asking the human.",
        "- If a nearby image has src_token='abc123' and the requirement asks for a short code/text visible in that image, treat that token as page evidence to try before asking the human or fetching binary image variants.",
        "- For multi-rule or requirement-driven tasks, preserve earlier satisfied constraints. Before editing a value, compare the current requirements/status lines with the current value, make the smallest reversible edit that targets the unsatisfied constraint, then verify what changed.",
        "- Do not fill an editable with the exact value it already contains. If the current value already satisfies the task, finish; otherwise make a value-changing edit that targets a visible failing requirement.",
        "- Before adding or replacing a token in a multi-constraint value, check whether the token's letters, digits, symbols, case, length, or other visible properties could affect already-satisfied constraints. Prefer a candidate that satisfies the new requirement while staying neutral for existing numeric, symbolic, text, and length requirements.",
        "- If a needed value is public information, use browser navigation, search, or a new tab to find it before asking the human. Reserve ask_human for operator-only visual/private values that page evidence and browser lookup cannot obtain.",
        "- If page evidence shows a public text-like asset URL such as SVG, XML, JSON, HTML, or plain text, use 'fetch_url' before browser 'goto' so the task tab keeps its state. If a fetch result is truncated, increase max_chars or choose a narrower source before fetching the same URL again.",
        "- On a loaded long article, documentation page, table, or search result where the needed text is below the viewport, use 'extract_page_text' with a query instead of repeatedly scrolling.",
        "- A new tab opens blank. If you need a lookup URL, call tab_new, then call goto in that current blank tab. Stay there until you have loaded and extracted the lookup result.",
        "- If an about:blank tab already exists, select and reuse it for lookup work instead of opening another blank tab.",
        "- Do not call tab_select for the tab that is already marked current; it is a no-op. If you are on the intended lookup tab, load the lookup URL with goto or choose another state-changing action.",
        "- When the original task page has active form/editable state, do not use goto on that tab for more lookup, including same-site asset or detail URLs. Switch to an existing lookup tab or open a new blank tab, then return to the task tab only to edit visible task controls.",
        "- After a lookup result is loaded and the needed value is extracted, switch back to the original task tab before using task-page element refs or filling task-page controls.",
        "- For iframe-only visual clues, inspect iframe src/title/nearby DOM evidence and use public lookup when possible. Do not use frame-scoped refs as normal element refs unless they are listed as current clickable elements.",
        "- Use 'draw_circle' when a game asks for a freehand circle on a canvas-like surface.",
        "- Use 'ask_human' when a short value visible to the operator is needed to continue.",
        "- Call 'finish' when the task is complete.",
        "- Do NOT call 'snapshot' for recovery; every step already includes a fresh page state.",
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
The current page state already shows the available refs. I'll choose the matching visible result link instead."
→ Tool call: click(ref="e8")

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
    last_observation: str | None = None,
    domain_context: str | None = None,
    task: str | None = None,
    evidence_text: str | None = None,
    task_context: str | None = None,
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
        card_lines = [
            _format_element_line(e.element_id, e.element_type, e.text, e.href, e.area)
            for e in selected_elements
            if _is_card_element(e)
        ]
        other_lines = [
            _format_element_line(e.element_id, e.element_type, e.text, e.href, e.area)
            for e in selected_elements
            if not _is_card_element(e)
        ]
        if card_lines:
            clickable_section = "\n\n".join(
                [
                    "Clickable cards/results (cursor-pointer generic refs are valid click targets):\n"
                    + "\n".join(card_lines),
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
    bad_url_guess_note = _bad_url_guess_note(state, action_history, selected_elements)
    status_summary = _current_status_summary(state.dom_evidence)
    editable_summary = _current_editable_summary(state.dom_evidence)
    compact_sections: list[str] = []
    if status_summary:
        compact_sections.append("Current status indicators:\n" + status_summary)
    if editable_summary:
        compact_sections.append("Current editable values:\n" + editable_summary)

    sections = [
        f"Current page:\nURL: {state.url}\nTitle: {state.title}\nType: {state.page_type}",
        f"Page summary:\n{state.page_summary}",
        *compact_sections,
        clickable_section,
        "Visible text (truncated):\n" + (state.visible_text[:800] if state.visible_text else "(none)"),
        "Previous actions:\n" + ("\n".join(history_lines) if history_lines else "(none)"),
    ]

    if state.dom_evidence:
        sections.insert(3 + len(compact_sections), "DOM evidence:\n" + state.dom_evidence)

    if evidence_snippets:
        evidence_lines = [f"- {snippet}" for snippet in evidence_snippets]
        sections.insert(3 + len(compact_sections), "Task-focused evidence:\n" + "\n".join(evidence_lines))

    if task_context:
        sections.insert(2, "Task contract and evidence so far:\n" + task_context)

    if redirect_note:
        sections.insert(2, "Redirect/canonical note:\n" + redirect_note)

    if bad_url_guess_note:
        sections.insert(2, "URL recovery note:\n" + bad_url_guess_note)

    variant_note = _variant_guess_recovery_note(action_history, state.visible_text, state.dom_evidence)
    if variant_note:
        sections.insert(2, "Variant-loop recovery note:\n" + variant_note)

    if last_error:
        blank_page_note = _blank_page_ref_recovery_note(
            last_error,
            state,
            action_history,
        )
        if blank_page_note:
            sections.append("Blank-page recovery:\n" + blank_page_note)
        tab_recovery_note = _task_tab_recovery_note(
            last_error,
            state,
            action_history,
            task or "",
        )
        if tab_recovery_note:
            sections.append("Task-tab recovery:\n" + tab_recovery_note)
        recovery_note = _custom_control_recovery_note(last_error, selected_elements, task or "")
        if recovery_note:
            sections.append("Custom control recovery:\n" + recovery_note)
        sections.append(f"IMPORTANT - Last action failed:\n{last_error}\nTry a different approach.")

    if last_observation:
        sections.append(f"Observed change after last successful action:\n{last_observation}")

    if domain_context:
        sections.append(f"Tips for this site:\n{domain_context}")

    finish_instruction = (
        "Call the appropriate tool for the next action. If the visible text already "
        "satisfies the task, call finish now. Do not call snapshot unless the user "
        "explicitly asked for an extra snapshot."
    )
    if task_context:
        finish_instruction = (
            "Call finish only when the current page plus the task evidence ledger "
            "satisfy every hard constraint and any cheapest/lowest/best comparison "
            "is consistent with the best evidence so far. Otherwise keep gathering "
            "or normalizing evidence. Do not call snapshot unless the user explicitly "
            "asked for an extra snapshot."
        )
    sections.append(finish_instruction)

    return "\n\n".join(sections)


def planner_state_debug_payload(
    state: InterpreterState,
    *,
    max_elements: int,
    task: str | None = None,
    evidence_text: str | None = None,
) -> dict:
    selected_elements = _select_clickable_elements(
        state.clickable_elements,
        max_elements,
        task=task,
        current_title=state.title,
    )
    return {
        "selected_clickables": [
            {
                "ref": element.element_id,
                "type": element.element_type,
                "area": element.area,
                "text": element.text,
                "href": element.href,
            }
            for element in selected_elements
        ],
        "prioritized_card_refs": [
            element.element_id for element in selected_elements if _is_card_element(element)
        ],
        "evidence_snippets": _task_evidence_snippets(
            state.visible_text,
            evidence_text or "",
            task or "",
            state.title,
        ),
        "dom_evidence": state.dom_evidence,
    }


def _format_element_line(
    element_id: str,
    element_type: str,
    text: str,
    href: str,
    area: str = "other",
) -> str:
    suffix = f" -> {href}" if href else ""
    return f"{element_id}: [{area}] {element_type} - {text}{suffix}"


def _current_status_summary(dom_evidence: str) -> str:
    statuses = _status_indicators_from_dom_evidence(dom_evidence)
    if not statuses:
        return ""

    failing = [label for label, status in statuses if status == "error"]
    satisfied = [label for label, status in statuses if status == "success"]
    sections: list[str] = []
    if failing:
        sections.append("Failing:\n" + "\n".join(f"- {label}" for label in failing[:8]))
    if satisfied:
        sections.append(
            "Satisfied:\n" + "\n".join(f"- {label}" for label in satisfied[:8])
        )
    return "\n".join(sections)


def _current_editable_summary(dom_evidence: str) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for line in (dom_evidence or "").splitlines():
        if "active_editable:" not in line and "editable:" not in line:
            continue
        text = _normalize_status_label(_quoted_dom_field(line, "text"))
        if not text or text in seen:
            continue
        seen.add(text)
        role = _normalize_status_label(_quoted_dom_field(line, "role"))
        tag = _normalize_status_label(_quoted_dom_field(line, "tag"))
        label = role or tag or "editable"
        html_value = html.unescape(_quoted_dom_field(line, "html"))
        rich_suffix = " (rich HTML)" if _html_has_rich_markup(html_value) else ""
        values.append(f"- {label}: {text!r}{rich_suffix}")
    return "\n".join(values[:3])


def _status_indicators_from_dom_evidence(dom_evidence: str) -> list[tuple[str, str]]:
    indicators: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in (dom_evidence or "").splitlines():
        status = _quoted_dom_field(line, "status")
        if status not in {"success", "error"}:
            continue
        label = _normalize_status_label(_quoted_dom_field(line, "nearby"))
        if not label:
            continue
        item = (label, status)
        if item in seen:
            continue
        seen.add(item)
        indicators.append(item)
    return indicators


def _quoted_dom_field(line: str, field: str) -> str:
    match = re.search(
        rf"\b{re.escape(field)}=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
        line or "",
    )
    if not match:
        return ""
    raw_value = match.group(1)
    try:
        return str(ast.literal_eval(raw_value))
    except (SyntaxError, ValueError):
        return raw_value[1:-1]


def _normalize_status_label(label: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(label or "")).strip()[:240]


def _html_has_rich_markup(html_value: str) -> bool:
    if not html_value:
        return False
    if re.search(
        r"</?(?:b|strong|i|em|u|s|strike|mark|sub|sup|span)\b",
        html_value,
        re.I,
    ):
        return True
    return bool(
        re.search(
            r"\bstyle\s*=\s*['\"][^'\"]*(?:font-|font-weight|font-style|"
            r"text-decoration|color|background)",
            html_value,
            re.I,
        )
    )


def _select_clickable_elements(
    elements: list,
    max_elements: int,
    *,
    task: str | None,
    current_title: str,
) -> list:
    if len(elements) <= max_elements:
        return elements

    selected: list = []
    seen: set[str] = set()

    for element in elements:
        if _is_card_element(element):
            selected.append(element)
            seen.add(element.element_id)
        if len(selected) >= max_elements:
            return selected

    scored: list[tuple[int, int, object]] = []
    terms = _task_terms(task or "", current_title)
    if terms:
        for index, element in enumerate(elements):
            if element.element_id in seen:
                continue
            score = _element_relevance_score(element.text, element.href, terms)
            if score:
                scored.append((score, -index, element))

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


def _is_card_element(element: object) -> bool:
    return (
        getattr(element, "element_type", "") == "card"
        or getattr(element, "area", "") in {"card", "result_card"}
    )


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
    return value.strip()


def _redirected_from_titles(text: str) -> list[str]:
    return [
        match.strip()
        for match in re.findall(r"Redirected\s+from\s+([A-Za-z0-9][^\\\n|]{1,80})", text)
        if match.strip()
    ]


def _strip_site_suffix(title: str) -> str:
    return re.sub(r"\s+[-|]\s+[^-|]{2,80}$", "", title).strip()


def _normalize_article_title(title: str) -> str:
    stripped = _strip_site_suffix(title)
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _bad_url_guess_note(
    state: InterpreterState,
    action_history: list[str],
    selected_elements: list,
) -> str:
    if not _looks_like_not_found_page(state):
        return ""
    if not any("playwright-cli goto" in action for action in action_history[-3:]):
        return ""

    preferred_refs = [
        element.element_id
        for element in selected_elements
        if _is_card_element(element) or getattr(element, "element_type", "") == "input"
    ][:5]
    ref_note = (
        f" Preferred visible refs: {', '.join(preferred_refs)}."
        if preferred_refs
        else ""
    )
    return (
        "The current page appears to be a 404/not-found page after direct URL navigation. "
        "Do not guess another detail/entity URL; go back, use visible result refs, or use "
        "a search/filter control instead."
        + ref_note
    )


def _variant_guess_recovery_note(
    action_history: list[str],
    visible_text: str,
    dom_evidence: str,
) -> str:
    fills = [_fill_target(action) for action in action_history[-8:]]
    fills = [target for target in fills if target]
    if len(fills) < 4:
        return ""
    most_recent = fills[-1]
    if sum(1 for target in fills if target == most_recent) < 4:
        return ""
    status_text = f"{visible_text[:1200]}\n{dom_evidence[:1200]}".lower()
    has_unresolved_status = any(
        marker in status_text
        for marker in (
            "status='error'",
            'status="error"',
            "error",
            "invalid",
            "must",
            "required",
            "requires",
            "requirement",
            "rule",
        )
    )
    if not has_unresolved_status:
        return ""
    return (
        "Several recent fills changed variants of the same field while a requirement/status "
        "still appears unresolved. Stop trying more synonyms or formatting variants. First "
        "inspect the status indicators and available page evidence to identify what is actually "
        "wrong. Check whether the last edit fixed one requirement but changed letters, digits, "
        "symbols, case, length, or other properties that another requirement uses; choose a "
        "candidate that satisfies both instead of toggling. If the value is public information, "
        "use browser lookup in a separate tab when possible; if it is operator-only visual/private "
        "information and human input is enabled, ask the human."
    )


def _fill_target(action: str) -> str:
    match = re.search(r"\bfill\s+(e\d+)\b", action)
    return match.group(1) if match else ""


def _task_tab_recovery_note(
    last_error: str,
    state: InterpreterState,
    action_history: list[str],
    task: str,
) -> str:
    lowered_error = last_error.lower()
    if not any(
        marker in lowered_error
        for marker in (
            "ref ",
            "not found in the current page snapshot",
            "element is not",
            "not an <input>",
        )
    ):
        return ""
    if not any(
        marker in action
        for action in action_history[-8:]
        for marker in ("tab-new", "tab_select", "tab-select", "goto http", "go-back")
    ):
        return ""
    task_terms = _task_terms(task, "")
    current_haystack = f"{state.url} {state.title}".lower()
    on_task_page = any(term in current_haystack for term in task_terms)
    has_task_control = any(
        getattr(element, "element_type", "") == "input"
        for element in state.clickable_elements
    )
    if on_task_page and has_task_control:
        return ""
    return (
        "The failed action looks like it used a task-page ref while the browser is on a "
        "lookup/result tab or a stale snapshot. Do not click random lookup-page refs or type "
        "into the lookup page. Use tab_list if needed, switch back to the tab whose URL/title "
        "matches the original task, wait for its fresh page state, and only then fill a visible "
        "task-page input ref."
    )


def _blank_page_ref_recovery_note(
    last_error: str,
    state: InterpreterState,
    action_history: list[str],
) -> str:
    lowered_error = last_error.lower()
    if "not found in the current page snapshot" not in lowered_error:
        return ""
    if not (state.url or "").startswith("about:"):
        return ""

    ref, value = _last_fill_value(action_history)
    if not ref or value is None:
        return (
            "The browser appears to have reopened on a blank page after a stale ref "
            "failure. Do not treat this as a fresh task. Restore the last non-blank "
            "task page from prior page states, wait for fresh refs, then continue from "
            "the last known task state."
        )
    return (
        "The browser appears to have reopened on a blank page after a stale ref "
        "failure. Do not start the task over. Restore the last non-blank task page "
        "from prior page states, wait for fresh refs, then replay the intended full "
        f"field value from the failed action. The stale ref was {ref}; if refs changed, "
        f"use the fresh visible input ref. Intended value: {value!r}."
    )


def _last_fill_value(action_history: list[str]) -> tuple[str, str | None]:
    for action in reversed(action_history):
        try:
            parts = shlex.split(action)
        except ValueError:
            continue
        if len(parts) >= 4 and parts[0] == "playwright-cli" and parts[1] == "fill":
            return parts[2], parts[3]
    return "", None


def _looks_like_not_found_page(state: InterpreterState) -> bool:
    haystack = f"{state.title}\n{state.visible_text[:1200]}".lower()
    return any(
        marker in haystack
        for marker in (
            "404",
            "not found",
            "page not found",
            "couldn't find",
            "could not find",
        )
    )

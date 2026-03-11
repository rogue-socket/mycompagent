"""Interpreter layer: convert raw snapshot into structured page state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from browser_agent.playwright_executor import PlaywrightExecutor
from browser_agent.snapshot_parser import ElementRef, SnapshotState


@dataclass(slots=True)
class ClickableElement:
    element_id: str
    element_type: str
    text: str


@dataclass(slots=True)
class InterpreterState:
    url: str
    title: str
    page_type: str
    clickable_elements: list[ClickableElement]
    visible_text: str
    page_summary: str


CLICKABLE_KEYWORDS = (
    "link",
    "button",
    "combobox",
    "textbox",
    "input",
    "select",
    "checkbox",
    "radio",
    "search",
)


def interpret_page(
    snapshot: SnapshotState,
    executor: PlaywrightExecutor,
    *,
    max_clickables: int = 50,
    max_visible_chars: int = 2000,
) -> InterpreterState:
    clickable = _extract_clickables(snapshot.elements, max_clickables)
    visible_text = _get_visible_text(executor, max_visible_chars)
    page_type = _detect_page_type(snapshot.url, snapshot.title, visible_text, clickable)
    page_summary = _summarize_page(visible_text, clickable, page_type)

    return InterpreterState(
        url=snapshot.url,
        title=snapshot.title,
        page_type=page_type,
        clickable_elements=clickable,
        visible_text=visible_text,
        page_summary=page_summary,
    )


def _extract_clickables(elements: list[ElementRef], max_items: int) -> list[ClickableElement]:
    clickables: list[ClickableElement] = []
    for elem in elements:
        desc = elem.description.lower()
        if any(keyword in desc for keyword in CLICKABLE_KEYWORDS):
            element_type = _classify_element_type(desc)
            text = _extract_label(elem.description)
            clickables.append(
                ClickableElement(element_id=elem.ref, element_type=element_type, text=text)
            )
        if len(clickables) >= max_items:
            break
    return clickables


def _classify_element_type(desc: str) -> str:
    if "link" in desc:
        return "link"
    if "button" in desc:
        return "button"
    if "combobox" in desc or "textbox" in desc or "input" in desc or "search" in desc:
        return "input"
    if "select" in desc:
        return "select"
    if "checkbox" in desc:
        return "checkbox"
    if "radio" in desc:
        return "radio"
    return "other"


def _extract_label(description: str) -> str:
    cleaned = re.sub(r"\s+", " ", description).strip()
    return cleaned[:160]


def _get_visible_text(executor: PlaywrightExecutor, max_chars: int) -> str:
    result = executor.run('playwright-cli eval "document.body.innerText"')
    text = _extract_eval_output(result.stdout)
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _extract_eval_output(output: str) -> str:
    if "```" in output:
        # Extract first fenced code block
        parts = output.split("```")
        if len(parts) >= 2:
            return parts[1].strip()
    lines = []
    for line in output.splitlines():
        if line.startswith("###"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _detect_page_type(
    url: str,
    title: str,
    visible_text: str,
    clickables: list[ClickableElement],
) -> str:
    url_lower = url.lower()
    title_lower = title.lower()
    text_lower = visible_text.lower()

    if "search" in url_lower or "search" in title_lower:
        return "search_results"
    if any(token in text_lower for token in ("sign in", "log in", "password")):
        return "login_page"
    if any(token in text_lower for token in ("add to cart", "buy now", "checkout")):
        return "ecommerce"
    if any(elem.element_type == "input" for elem in clickables):
        return "form"
    if len(visible_text.splitlines()) > 30:
        return "article"
    return "unknown"


def _summarize_page(
    visible_text: str,
    clickables: list[ClickableElement],
    page_type: str,
) -> str:
    lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
    top_lines = lines[:6]
    if page_type == "search_results":
        top_links = [c.text for c in clickables if c.element_type == "link"]
        return "Search results page. Top visible lines: " + "; ".join(top_lines[:3]) + "."
    if top_lines:
        return " ".join(top_lines[:3])
    return "No summary available."

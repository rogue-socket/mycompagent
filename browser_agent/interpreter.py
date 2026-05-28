"""Interpreter layer: convert raw snapshot into structured page state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from browser_agent.playwright_executor import PlaywrightExecutor
from browser_agent.snapshot_parser import ElementRef, SnapshotState


@dataclass(slots=True)
class ClickableElement:
    element_id: str
    element_type: str
    text: str
    href: str = ""
    area: str = "other"


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
    clickable = _extract_clickables(
        snapshot.elements,
        max_clickables,
        url=snapshot.url,
        title=snapshot.title,
    )
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


def _extract_clickables(
    elements: list[ElementRef],
    max_items: int,
    *,
    url: str = "",
    title: str = "",
) -> list[ClickableElement]:
    clickables: list[ClickableElement] = []
    is_article_page = _looks_like_article_page(url, title)
    for elem in elements:
        desc = elem.description.lower()
        if _is_clickable_element(elem):
            element_type = _classify_element_type(desc, elem)
            text = _extract_card_label(elem) if element_type == "card" else _extract_label(elem.description)
            clickables.append(
                ClickableElement(
                    element_id=elem.ref,
                    element_type=element_type,
                    text=text,
                    href=elem.url,
                    area=_classify_clickable_area(
                        element_type,
                        text,
                        elem.url,
                        url,
                        is_article_page=is_article_page,
                    ),
                )
            )
    if is_article_page:
        clickables = _rank_article_clickables(clickables, url)
    elif any(item.element_type == "card" for item in clickables):
        clickables = _rank_card_clickables(clickables)
    return clickables[:max_items]


def _is_clickable_element(elem: ElementRef) -> bool:
    desc = elem.description.lower()
    return _is_clickable_description(desc) or (
        _is_cursor_pointer_generic(elem) and _has_meaningful_card_text(elem)
    )


def _is_clickable_description(desc: str) -> bool:
    return any(keyword in desc for keyword in CLICKABLE_KEYWORDS) or _starts_with_role(
        desc,
        "option",
    )


def _starts_with_role(desc: str, role: str) -> bool:
    return desc == role or desc.startswith(f"{role} ") or desc.startswith(f'{role}"')


def _classify_element_type(desc: str, elem: ElementRef | None = None) -> str:
    if elem is not None and _is_cursor_pointer_generic(elem):
        return "card"
    if _starts_with_role(desc, "option"):
        return "option"
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


def _is_cursor_pointer_generic(elem: ElementRef) -> bool:
    metadata = {item.lower() for item in getattr(elem, "metadata", ())}
    return _starts_with_role(elem.description.lower(), "generic") and "cursor=pointer" in metadata


def _has_meaningful_card_text(elem: ElementRef) -> bool:
    text = f"{elem.description} {getattr(elem, 'child_text', '')}".strip()
    cleaned = re.sub(r"\s+", " ", text).strip()
    return len(cleaned) >= 8 and bool(re.search(r"[A-Za-z0-9]", cleaned))


def _extract_card_label(elem: ElementRef) -> str:
    child_text = re.sub(r"\s+", " ", getattr(elem, "child_text", "")).strip()
    if child_text:
        return f'generic card "{child_text[:140]}"'
    return _extract_label(elem.description)


def _extract_label(description: str) -> str:
    cleaned = re.sub(r"\s+", " ", description).strip()
    return cleaned[:160]


def _looks_like_article_page(url: str, title: str) -> bool:
    lowered_url = url.lower()
    lowered_title = title.lower()
    return "/wiki/" in lowered_url or " - wikipedia" in lowered_title or " | mdn" in lowered_title


def _rank_article_clickables(
    clickables: list[ClickableElement],
    current_url: str,
) -> list[ClickableElement]:
    return sorted(
        clickables,
        key=lambda item: (
            _article_clickable_priority(item, current_url),
            _first_number(item.element_id),
        ),
    )


def _rank_card_clickables(clickables: list[ClickableElement]) -> list[ClickableElement]:
    return [
        item
        for _, item in sorted(
            enumerate(clickables),
            key=lambda pair: (_card_clickable_priority(pair[1]), pair[0]),
        )
    ]


def _card_clickable_priority(item: ClickableElement) -> int:
    if item.element_type == "card" or item.area == "result_card":
        return 0 if _is_rich_result_card_label(item.text) else 3
    if item.element_type == "input":
        return 1
    if item.element_type in {"button", "option", "select", "checkbox", "radio"}:
        return 2
    if item.area in {"navigation", "account", "language"}:
        return 5
    return 4


def _is_rich_result_card_label(text: str) -> bool:
    lowered = text.lower()
    if "generic card" not in lowered:
        return False
    return len(lowered) >= 45 or any(
        marker in lowered
        for marker in (
            "bookable",
            "featured",
            "rating",
            "start-rating",
            " km",
            " kms",
            "inr ",
            " | ",
        )
    )


def _article_clickable_priority(item: ClickableElement, current_url: str) -> int:
    area_priority = {
        "article": 0,
        "other": 1,
        "taxonomy": 2,
        "action": 2,
        "contents": 3,
        "navigation": 4,
        "account": 4,
        "language": 4,
    }
    return area_priority.get(item.area, 1)


def _classify_clickable_area(
    element_type: str,
    text: str,
    href: str,
    current_url: str,
    *,
    is_article_page: bool,
) -> str:
    if element_type == "card":
        return "result_card"
    if element_type != "link":
        return "action"

    lowered = text.lower()
    if _is_same_page_anchor(href, current_url):
        return "contents"
    if _is_account_link(lowered, href):
        return "account"
    if _is_language_link(lowered, href, current_url):
        return "language"
    if _is_page_chrome_link(lowered) or _is_navigation_href(href):
        return "navigation"
    if is_article_page and _is_taxonomy_link(lowered, href):
        return "taxonomy"
    if is_article_page and _is_article_content_href(href, current_url):
        return "article"
    return "other"


def _is_same_page_anchor(href: str, current_url: str) -> bool:
    if not href:
        return False
    if href.startswith("#"):
        return True
    parsed_href = urlparse(href)
    parsed_current = urlparse(current_url)
    if not parsed_href.fragment:
        return False
    href_path = parsed_href.path
    current_path = parsed_current.path
    return bool(href_path and current_path and href_path == current_path)


def _is_page_chrome_link(text: str) -> bool:
    chrome_exact = {
        "link \"jump to content\"",
        "link \"skip to main content\"",
        "link \"skip to search\"",
        "link \"wikipedia the free encyclopedia\"",
        "link \"mdn\"",
        "link \"donate\"",
        "link \"create account\"",
        "link \"log in\"",
        "link \"(top)\"",
        "link \"article\"",
        "link \"talk\"",
        "link \"read\"",
        "link \"edit\"",
        "link \"view source\"",
        "link \"view history\"",
        "link \"tools\"",
    }
    if text in chrome_exact:
        return True
    if "navigation" in text:
        return True
    if text.startswith("link \"") and any(
        marker in text
        for marker in (
            "toggle ",
            "special:",
            "help:",
            "wikipedia:",
            "category:",
            "file:",
        )
    ):
        return True
    toc_labels = {
        "etymology and symbol",
        "natural history",
        "composition and structure",
        "gravity and magnetic field",
        "moon and orbital space",
        "orbit and rotation",
        "atmosphere and climate",
        "hydrosphere",
        "biosphere",
        "human geography",
        "in culture",
        "see also",
        "notes",
        "references",
        "external links",
        "syntax",
        "description",
        "examples",
        "specifications",
        "browser compatibility",
    }
    return any(text == f'link "{label}"' for label in toc_labels)


def _is_account_link(text: str, href: str) -> bool:
    href_lower = href.lower()
    return any(
        marker in f"{text} {href_lower}"
        for marker in (
            "create account",
            "login",
            "log in",
            "userlogin",
            "donate",
        )
    )


def _is_language_link(text: str, href: str, current_url: str) -> bool:
    if "languages" in text or text == 'link "language"':
        return True
    parsed_href = urlparse(href)
    parsed_current = urlparse(current_url)
    if not parsed_href.netloc or not parsed_current.netloc:
        return False
    return (
        "wikipedia.org" in parsed_href.netloc
        and "wikipedia.org" in parsed_current.netloc
        and parsed_href.netloc != parsed_current.netloc
    )


def _is_navigation_href(href: str) -> bool:
    parsed = urlparse(href)
    path = unquote(parsed.path).lower()
    if path.startswith("/w/"):
        return True
    if not path.startswith("/wiki/"):
        return False
    page_name = path.removeprefix("/wiki/")
    return page_name.startswith(
        (
            "special:",
            "help:",
            "wikipedia:",
            "file:",
            "category:",
            "template:",
            "portal:",
            "talk:",
        )
    )


def _is_taxonomy_link(text: str, href: str) -> bool:
    haystack = f"{text} {unquote(urlparse(href).path).lower()}"
    return any(
        marker in haystack
        for marker in (
            "taxonomy",
            "taxon",
            "species",
            "genus",
            "genera",
            "family_(biology)",
            "order_(biology)",
            "class_(biology)",
            "phylum",
            "kingdom_(biology)",
            "actinopterygii",
            "notothenioidei",
            "perciformes",
            "chordata",
            "animalia",
            "binomial nomenclature",
        )
    )


def _is_article_content_href(href: str, current_url: str) -> bool:
    if not href:
        return False
    if href.startswith("#"):
        return False
    parsed_href = urlparse(href)
    parsed_current = urlparse(current_url)
    if parsed_href.netloc and parsed_current.netloc and parsed_href.netloc != parsed_current.netloc:
        return False
    if _is_navigation_href(href):
        return False
    return bool(parsed_href.path and parsed_href.path != parsed_current.path)


def _first_number(value: str) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else 0


def _get_visible_text(executor: PlaywrightExecutor, max_chars: int) -> str:
    result = executor.run('playwright-cli eval "document.body.innerText"')
    text = _extract_eval_output(result.stdout)
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _extract_eval_output(output: str) -> str:
    result_match = re.search(
        r"^### Result\s*\n(?P<result>.*?)(?=^### |\Z)",
        output,
        re.M | re.S,
    )
    if result_match:
        result_text = result_match.group("result").strip()
        if result_text:
            try:
                import json

                decoded = json.loads(result_text)
                return str(decoded) if decoded is not None else ""
            except json.JSONDecodeError:
                return result_text
        return ""

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
    if _looks_like_article_page(url, title):
        return "article"
    if _looks_like_listing_page(url, title, visible_text, clickables):
        return "listing_results"
    if "password" in text_lower or (
        "sign in" in text_lower and any(elem.element_type == "input" for elem in clickables)
    ):
        return "login_page"
    if any(token in text_lower for token in ("add to cart", "buy now", "checkout")):
        return "ecommerce"
    if any(elem.element_type == "input" for elem in clickables):
        return "form"
    return "unknown"


def _looks_like_listing_page(
    url: str,
    title: str,
    visible_text: str,
    clickables: list[ClickableElement],
) -> bool:
    text_lower = visible_text.lower()
    chrome_lower = f"{url} {title}".lower()
    result_card_count = sum(
        1
        for elem in clickables
        if elem.element_type == "card" or elem.area == "result_card"
    )
    if result_card_count >= 2:
        return True
    listing_markers = (
        "bookable",
        "filter",
        "filters",
        "results",
        "search results",
        "showing",
        "sort by",
        "venues",
    )
    if result_card_count and any(marker in text_lower for marker in listing_markers):
        return True
    if any(marker in chrome_lower for marker in ("venues", "listing", "results")) and any(
        marker in text_lower for marker in listing_markers
    ):
        return True
    return text_lower.count("bookable") >= 2


def _summarize_page(
    visible_text: str,
    clickables: list[ClickableElement],
    page_type: str,
) -> str:
    lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
    if page_type == "search_results":
        top_lines = lines[:6]
        top_links = [c.text for c in clickables if c.element_type == "link"]
        return "Search results page. Top visible lines: " + "; ".join(top_lines[:3]) + "."
    if page_type == "listing_results":
        card_labels = [
            c.text for c in clickables if c.element_type == "card" or c.area == "result_card"
        ]
        if card_labels:
            return "Listing/results page. Visible result cards: " + "; ".join(card_labels[:3]) + "."
    if page_type == "article":
        article_lines = _article_summary_lines(lines)
        if article_lines:
            return " ".join(article_lines[:3])
    top_lines = lines[:6]
    if top_lines:
        return " ".join(top_lines[:3])
    return "No summary available."


def _article_summary_lines(lines: list[str]) -> list[str]:
    skip = {
        "jump to content",
        "skip to main content",
        "skip to search",
        "main menu",
        "search",
        "donate",
        "create account",
        "log in",
        "contents hide",
        "article",
        "talk",
        "read",
        "edit",
        "view source",
        "view history",
        "tools",
        "appearance hide",
        "text",
        "small",
        "standard",
        "large",
        "width",
        "wide",
        "color",
        "automatic",
        "light",
        "dark",
    }
    result: list[str] = []
    after_source_marker = False
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("from wikipedia") or lowered.startswith("baseline "):
            after_source_marker = True
            continue
        if lowered in skip or lowered.startswith("toggle "):
            continue
        if re.fullmatch(r"\d+\s+languages?", lowered):
            continue
        if after_source_marker or result:
            result.append(line)
        elif len(line) > 24 and not lowered.startswith("("):
            result.append(line)
        if len(result) >= 4:
            break
    return result

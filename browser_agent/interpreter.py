"""Interpreter layer: convert raw snapshot into structured page state."""

from __future__ import annotations

import json
import re
import shlex
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
    dom_evidence: str = ""


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
    dom_evidence = (
        _get_dom_evidence(executor) if _supports_dom_evidence(executor) else ""
    )
    page_type = _detect_page_type(snapshot.url, snapshot.title, visible_text, clickable)
    page_summary = _summarize_page(visible_text, clickable, page_type)

    return InterpreterState(
        url=snapshot.url,
        title=snapshot.title,
        page_type=page_type,
        clickable_elements=clickable,
        visible_text=visible_text,
        page_summary=page_summary,
        dom_evidence=dom_evidence,
    )


def _extract_clickables(
    elements: list[ElementRef],
    max_items: int,
    *,
    url: str = "",
    title: str = "",
) -> list[ClickableElement]:
    clickables: list[ClickableElement] = []
    has_result_cards = any(
        _is_cursor_pointer_generic(elem) and _has_meaningful_card_text(elem)
        for elem in elements
    )
    is_article_page = _looks_like_article_page(url, title) and not has_result_cards
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
    if any(item.element_type == "card" for item in clickables):
        clickables = _rank_card_clickables(clickables)
    elif is_article_page:
        clickables = _rank_article_clickables(clickables, url)
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
    lowered_title = title.lower()
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    return (
        len(path_parts) >= 1
        and not parsed.query
        and bool(title.strip())
        and not any(term in lowered_title for term in ("search", "login", "sign in"))
    )


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
            "category:",
            "file:",
        )
    ):
        return True
    generic_section_labels = {
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
    return any(text == f'link "{label}"' for label in generic_section_labels)


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
    return _registrable_domain(parsed_href.netloc) == _registrable_domain(
        parsed_current.netloc
    ) and parsed_href.netloc != parsed_current.netloc


def _registrable_domain(host: str) -> str:
    parts = [part for part in host.lower().split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def _is_navigation_href(href: str) -> bool:
    parsed = urlparse(href)
    path_parts = [
        part
        for part in unquote(parsed.path).lower().split("/")
        if part
    ]
    if not path_parts:
        return False
    chrome_prefixes = (
        "account",
        "admin",
        "auth",
        "category",
        "file",
        "help",
        "login",
        "logout",
        "portal",
        "search",
        "settings",
        "special",
        "talk",
        "template",
        "user",
    )
    return any(
        part == prefix or part.startswith(f"{prefix}:")
        for part in path_parts
        for prefix in chrome_prefixes
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


def _supports_dom_evidence(executor: PlaywrightExecutor) -> bool:
    return bool(
        getattr(
            executor,
            "supports_dom_evidence",
            executor.__class__ is PlaywrightExecutor,
        )
    )


def _get_dom_evidence(executor: PlaywrightExecutor, max_chars: int = 5000) -> str:
    code = """async page => {
  return await page.evaluate(() => {
    const textOf = (el, max = 80) => (el.innerText || el.textContent || '')
      .replace(/\\s+/g, ' ')
      .trim()
      .slice(0, max);
    const attr = (el, name) => el.getAttribute(name) || '';
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 &&
        style.visibility !== 'hidden' && style.display !== 'none';
    };
    const clip = (value, max = 180) => String(value || '').slice(0, max);
    const sourceToken = (url) => {
      try {
        const parsed = new URL(url, window.location.href);
        const base = (parsed.pathname.split('/').pop() || '')
          .replace(/\\.[a-z0-9]{1,8}$/i, '');
        return /^(?=.*[A-Za-z])(?=.*\\d)[A-Za-z0-9_-]{3,24}$/.test(base) ? base : '';
      } catch {
        return '';
      }
    };
    const images = Array.from(document.images)
      .filter((img) => visible(img) && (img.currentSrc || img.src || img.alt))
      .slice(0, 20)
      .map((img) => ({
        kind: 'image',
        src: clip(img.currentSrc || img.src, 220),
        src_token: sourceToken(img.currentSrc || img.src),
        alt: clip(img.alt),
        title: clip(img.title),
        aria: clip(attr(img, 'aria-label')),
        nearby: clip(textOf(img.closest('figure, label, div, section, article') || img.parentElement), 120),
      }));
    const links = Array.from(document.querySelectorAll('a[href]'))
      .filter((link) => visible(link))
      .slice(0, 25)
      .map((link) => ({
        kind: 'link',
        text: clip(textOf(link), 120),
        href: clip(link.href, 220),
        title: clip(link.title),
      }));
    const iframes = Array.from(document.querySelectorAll('iframe'))
      .filter((frame) => visible(frame) && (frame.src || frame.title || attr(frame, 'aria-label')))
      .slice(0, 10)
      .map((frame) => ({
        kind: 'iframe',
        src: clip(frame.src, 220),
        title: clip(frame.title),
        aria: clip(attr(frame, 'aria-label')),
        nearby: clip(textOf(frame.closest('figure, label, div, section, article') || frame.parentElement), 120),
      }));
    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
      .filter((button) => visible(button))
      .slice(0, 20)
      .map((button) => ({
        kind: 'button',
        text: clip(textOf(button), 120),
        aria: clip(attr(button, 'aria-label')),
        title: clip(button.title),
        pressed: attr(button, 'aria-pressed'),
      }));
    const active = document.activeElement;
    const activeEditable = active && (
      active.isContentEditable ||
      ['INPUT', 'TEXTAREA'].includes(active.tagName) ||
      attr(active, 'role') === 'textbox'
    ) ? [{
      kind: 'active_editable',
      tag: active.tagName,
      role: clip(attr(active, 'role')),
      text: clip(active.value || textOf(active, 240), 240),
      html: clip(active.innerHTML || '', 300),
      selection: clip(String(window.getSelection ? window.getSelection() : ''), 240),
    }] : [];
    const activeSeen = active || null;
    const editables = Array.from(document.querySelectorAll(
      'input, textarea, [contenteditable="true"], [role="textbox"]'
    ))
      .filter((el) => visible(el) && el !== activeSeen)
      .slice(0, 10)
      .map((el) => ({
        kind: 'editable',
        tag: el.tagName,
        role: clip(attr(el, 'role')),
        aria: clip(attr(el, 'aria-label')),
        placeholder: clip(attr(el, 'placeholder')),
        text: clip(el.value || textOf(el, 240), 240),
        html: clip(el.innerHTML || '', 300),
      }));
    return [...activeEditable, ...editables, ...iframes, ...buttons, ...images, ...links];
  });
}"""
    result = executor.run("playwright-cli run-code " + shlex.quote(code))
    items = _extract_json_result(result.stdout)
    if not isinstance(items, list):
        return ""
    lines = _format_dom_evidence(items)
    evidence = "\n".join(lines)
    return evidence[:max_chars]


def _extract_json_result(output: str) -> Any | None:
    result_match = re.search(
        r"^### Result\s*\n(?P<result>.*?)(?=^### |\Z)",
        output,
        re.M | re.S,
    )
    if not result_match:
        return None
    result_text = result_match.group("result").strip()
    if not result_text:
        return None
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        return None


def _format_dom_evidence(items: list[Any]) -> list[str]:
    lines: list[str] = []
    for item in sorted(
        (item for item in items if isinstance(item, dict)),
        key=_dom_evidence_priority,
    ):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "node")
        status = _status_indicator(item)
        if status:
            nearby = str(item.get("nearby") or item.get("text") or item.get("alt") or "")
            src = str(item.get("src") or "")
            detail_parts = [f"status={status!r}"]
            if nearby:
                detail_parts.append(f"nearby={nearby!r}")
            if src:
                detail_parts.append(f"src={src!r}")
            lines.append("- status_indicator: " + " ".join(detail_parts))
        fields = [
            (key, str(value))
            for key, value in item.items()
            if key != "kind" and value not in {"", "None", "null"}
        ]
        if not fields:
            continue
        detail = " ".join(f"{key}={value!r}" for key, value in fields[:5])
        lines.append(f"- {kind}: {detail}")
    return lines


def _dom_evidence_priority(item: dict[str, Any]) -> int:
    kind = str(item.get("kind") or "")
    if kind == "active_editable":
        return 0
    if kind == "editable":
        return 1
    if kind == "iframe":
        return 2
    if _status_indicator(item):
        return 3
    if kind == "button":
        return 4
    if kind == "image":
        return 5
    if kind == "link":
        return 6
    return 7


def _status_indicator(item: dict[str, Any]) -> str:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("src", "alt", "title", "aria", "text")
    ).lower()
    if any(token in haystack for token in ("error", "invalid", "fail", "warning")):
        return "error"
    if any(token in haystack for token in ("checkmark", "check-mark", "success", "valid", "complete")):
        return "success"
    return ""


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
    if _looks_like_listing_page(url, title, visible_text, clickables):
        return "listing_results"
    if _looks_like_article_page(url, title):
        return "article"
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
        if lowered.startswith("from ") or lowered.startswith("baseline "):
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

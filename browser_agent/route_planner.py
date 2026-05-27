"""Lightweight route hints for Wikipedia link-navigation tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from browser_agent.interpreter import ClickableElement, InterpreterState


@dataclass(slots=True)
class RouteHint:
    element: ClickableElement
    reason: str
    score: int


_TARGET_BRIDGES: dict[str, tuple[str, ...]] = {
    "sushi": ("food", "cuisine", "culinary", "seafood", "japan", "japanese"),
    "manga": ("japan", "japanese", "comic", "anime", "literature", "book", "art"),
    "pokemon": ("video game", "game", "nintendo", "japan", "media", "anime"),
    "hip hop": ("music", "genre", "culture", "dance", "art"),
    "heavy metal": ("music", "genre", "rock", "culture"),
}

_GENERAL_BRIDGES = (
    "art",
    "book",
    "culture",
    "cuisine",
    "food",
    "game",
    "history",
    "japan",
    "language",
    "literature",
    "media",
    "music",
    "technology",
)


def wikipedia_route_hints(
    state: InterpreterState,
    task: str,
    *,
    max_hints: int = 5,
) -> list[RouteHint]:
    if state.page_type != "article" or "wikipedia.org/wiki/" not in state.url:
        return []

    target = _target_article(task)
    target_terms = _terms(target)
    bridge_terms = _bridge_terms(target)
    if not target_terms and not bridge_terms:
        return []

    hints: list[RouteHint] = []
    for element in state.clickable_elements:
        if element.element_type != "link":
            continue
        area = element.area
        if area not in {"article", "taxonomy", "contents"}:
            continue
        label = _label(element)
        score, reasons = _score_link(label, element.href, area, target_terms, bridge_terms)
        if score <= 0:
            continue
        hints.append(RouteHint(element=element, reason=", ".join(reasons), score=score))

    hints.sort(key=lambda hint: (-hint.score, _first_number(hint.element.element_id)))
    return hints[:max_hints]


def _score_link(
    label: str,
    href: str,
    area: str,
    target_terms: set[str],
    bridge_terms: set[str],
) -> tuple[int, list[str]]:
    haystack = f"{label} {href}".lower()
    score = 0
    reasons: list[str] = []

    exact_matches = [term for term in target_terms if term and term in haystack]
    if exact_matches:
        score += 100
        reasons.append("matches target term")

    bridge_matches = [term for term in bridge_terms if term and term in haystack]
    if bridge_matches:
        score += 35 + min(len(bridge_matches), 3) * 5
        reasons.append("matches bridge term")

    general_matches = [term for term in _GENERAL_BRIDGES if term in haystack]
    if general_matches:
        score += 10
        reasons.append("broad bridge")

    if area == "contents":
        score -= 10
        reasons.append("same-page section")
    elif area == "taxonomy":
        score -= 25
        reasons.append("taxonomy link")

    return score, reasons


def _target_article(task: str) -> str:
    numbered = re.findall(r"(?:^|[\n,;])\s*\d+\.\s*([^,\n;.]+)", task)
    if numbered:
        return _clean_target(numbered[-1])

    arrow_match = re.search(r"->\s*([^.\n;]+)", task)
    if arrow_match:
        return _clean_target(arrow_match.group(1))

    from_to_match = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.\n;]|$)",
        task,
        re.IGNORECASE,
    )
    if from_to_match:
        return _clean_target(from_to_match.group(2))
    return ""


def _clean_target(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"\s+on\s+wikipedia\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+wikipedia\s+article\b.*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _bridge_terms(target: str) -> set[str]:
    normalized = _normalize(target)
    bridges: set[str] = set()
    for key, terms in _TARGET_BRIDGES.items():
        if key in normalized:
            bridges.update(terms)
    return bridges


def _terms(value: str) -> set[str]:
    return {
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", value)
    }


def _label(element: ClickableElement) -> str:
    match = re.search(r'"([^"]+)"', element.text)
    return match.group(1) if match else element.text


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _first_number(value: str) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else 0

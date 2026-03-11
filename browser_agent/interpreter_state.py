"""Helper for serializing interpreter state."""

from __future__ import annotations

from typing import Any

from browser_agent.interpreter import InterpreterState


def to_dict(state: InterpreterState) -> dict[str, Any]:
    return {
        "url": state.url,
        "title": state.title,
        "page_type": state.page_type,
        "clickable_elements": [
            {"id": e.element_id, "type": e.element_type, "text": e.text}
            for e in state.clickable_elements
        ],
        "visible_text": state.visible_text,
        "page_summary": state.page_summary,
    }

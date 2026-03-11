"""Human-in-the-loop approval system."""

from __future__ import annotations

from browser_agent.action_parser import ParsedAction
from browser_agent.guardrails import is_risky_action
from browser_agent.snapshot_parser import ElementRef


def requires_approval(mode: str, action: ParsedAction, elements: list[ElementRef]) -> bool:
    if mode == "safe":
        return True
    if mode == "auto":
        return False
    return is_risky_action(action, elements)


def ask_approval(action: ParsedAction) -> bool:
    answer = input(f"Proposed Action: {action.action}\nApprove? (y/n): ").strip().lower()
    return answer in {"y", "yes"}

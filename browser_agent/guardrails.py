"""Safety guardrails and loop controls."""

from __future__ import annotations

import re
from typing import Iterable

from browser_agent.action_parser import ParsedAction
from browser_agent.constants import ALLOWED_COMMANDS
from browser_agent.snapshot_parser import ElementRef

_RISK_KEYWORDS = re.compile(r"buy|checkout|purchase|pay|submit|order|place order", re.I)

_RISKY_COMMANDS = {
    "open",
    "goto",
    "tab-new",
    "tab-close",
    "close",
    "delete-data",
    "run-code",
    "route",
    "unroute",
    "dialog-accept",
    "dialog-dismiss",
    "cookie-set",
    "cookie-delete",
    "cookie-clear",
    "localstorage-set",
    "localstorage-delete",
    "localstorage-clear",
    "sessionstorage-set",
    "sessionstorage-delete",
    "sessionstorage-clear",
}

_INPUT_COMMANDS = {
    "fill",
    "type",
    "press",
    "select",
    "check",
    "uncheck",
    "upload",
    "keydown",
    "keyup",
}


def is_risky_action(action: ParsedAction, elements: Iterable[ElementRef]) -> bool:
    if action.command in _RISKY_COMMANDS or action.command in _INPUT_COMMANDS:
        return True
    if action.command == "click":
        if action.args:
            target = action.args[0]
            for elem in elements:
                if elem.ref == target and _RISK_KEYWORDS.search(elem.description or ""):
                    return True
    return False


def detect_repeated_action(history: list[str], current: str, max_repeat: int = 3) -> bool:
    if len(history) < max_repeat:
        return False
    return all(item == current for item in history[-max_repeat:])


def detect_no_change(last_snapshot_hash: str, current_snapshot_hash: str, repeats: int) -> bool:
    return repeats >= 2 and last_snapshot_hash == current_snapshot_hash

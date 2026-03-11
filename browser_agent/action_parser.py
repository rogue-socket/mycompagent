"""Validate and normalize LLM actions."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from browser_agent.constants import ALLOWED_COMMANDS


class ActionParseError(RuntimeError):
    """Raised when action payload is invalid."""


@dataclass(slots=True)
class ParsedAction:
    thought: str
    reasoning_summary: str
    action: str
    command: str
    args: list[str]


def parse_action(payload: dict[str, Any]) -> ParsedAction:
    payload = _normalize_payload(payload)

    thought = str(payload.get("thought", "")).strip()
    reasoning_summary = str(payload.get("reasoning_summary", "")).strip()
    action_text = str(payload.get("action", "")).strip()

    if not action_text:
        raise ActionParseError("Missing action field")

    action_text = _normalize_action_text(action_text)

    parts = shlex.split(action_text)
    if len(parts) < 2:
        raise ActionParseError("Action missing command")

    command, args, session_args = _extract_command(parts[1:])
    command, args = _normalize_command(command, args)
    args = _normalize_command_args(command, args)
    if command not in ALLOWED_COMMANDS:
        raise ActionParseError(f"Command '{command}' not allowed")

    _validate_flags(command, args)
    _validate_command_args(command, args)

    action_text = _build_action_text(command, args, session_args)

    return ParsedAction(
        thought=thought,
        reasoning_summary=reasoning_summary,
        action=action_text,
        command=command,
        args=args,
    )


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("reasoning_summary") is None:
        normalized["reasoning_summary"] = normalized.get("reason") or ""
    if normalized.get("thought") is None:
        normalized["thought"] = ""
    if normalized.get("action") is None and normalized.get("command") is not None:
        cmd = str(normalized.get("command", "")).strip()
        args = normalized.get("args") or []
        if isinstance(args, list):
            normalized["action"] = "playwright-cli " + " ".join([cmd, *map(str, args)]).strip()
    if isinstance(normalized.get("action"), str):
        normalized["action"] = normalized["action"].strip().strip("`")
    return normalized


def _extract_command(tokens: list[str]) -> tuple[str, list[str], list[str]]:
    session_args: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("-s=") or token.startswith("--session="):
            session_args.append(token)
            idx += 1
            continue
        if token in {"-s", "--session"} and idx + 1 < len(tokens):
            session_args.extend([token, tokens[idx + 1]])
            idx += 2
            continue
        break
    if idx >= len(tokens):
        raise ActionParseError("Action missing command after session flags")
    command = tokens[idx]
    args = tokens[idx + 1 :]
    return command, args, session_args


def _normalize_action_text(action_text: str) -> str:
    text = action_text.strip()
    if text.startswith("playwright-cli "):
        return text
    # If model returns a raw command like "click e12", normalize it.
    parts = shlex.split(text)
    if not parts:
        return text
    command = parts[0]
    if command in ALLOWED_COMMANDS:
        return "playwright-cli " + text
    return text


def _normalize_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    if command == "type" and args and _is_element_ref(args[0]):
        # The Playwright CLI expects `type <text>` for the focused element.
        # If the model uses `type e12 ...`, convert to `fill e12 "..."`.
        text = " ".join(args[1:]).strip()
        return "fill", [args[0], text] if text else [args[0]]

    if command == "type" and len(args) > 1:
        # Normalize unquoted text into a single argument unless flags are present.
        if any(arg.startswith("--") for arg in args):
            return command, args
        return command, [" ".join(args).strip()]

    if command == "fill":
        normalized = _normalize_fill_args(args)
        return command, normalized

    return command, args


def _normalize_command_args(command: str, args: list[str]) -> list[str]:
    # Normalize --url flag into positional URL for open/goto.
    if command in {"open", "goto"}:
        normalized: list[str] = []
        idx = 0
        while idx < len(args):
            arg = args[idx]
            if arg.startswith("--url="):
                normalized.append(arg.split("=", 1)[1])
                idx += 1
                continue
            if arg == "--url" and idx + 1 < len(args):
                normalized.append(args[idx + 1])
                idx += 2
                continue
            normalized.append(arg)
            idx += 1
        # If the first positional looks like a bare domain, add scheme.
        if normalized:
            candidate = normalized[0]
            if "://" not in candidate and "." in candidate and not candidate.startswith("-"):
                normalized[0] = "https://" + candidate
        return normalized
    return args


def _normalize_fill_args(args: list[str]) -> list[str]:
    element: str | None = None
    value_parts: list[str] = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in {"--element", "--el", "--ref"} and idx + 1 < len(args):
            element = args[idx + 1]
            idx += 2
            continue
        if arg.startswith("--element="):
            element = arg.split("=", 1)[1]
            idx += 1
            continue
        if arg in {"--value", "--text", "--query"} and idx + 1 < len(args):
            value_parts.append(args[idx + 1])
            idx += 2
            continue
        if arg.startswith("--value=") or arg.startswith("--text=") or arg.startswith("--query="):
            value_parts.append(arg.split("=", 1)[1])
            idx += 1
            continue
        if element is None and _is_element_ref(arg):
            element = arg
            idx += 1
            continue
        value_parts.append(arg)
        idx += 1

    if element is None:
        return args

    if value_parts:
        return [element, " ".join(value_parts).strip()]

    return [element]


def _validate_command_args(command: str, args: list[str]) -> None:
    element_required = {
        "click",
        "dblclick",
        "hover",
        "check",
        "uncheck",
    }
    element_value_required = {
        "fill",
        "select",
    }
    element_pair_required = {
        "drag",
    }

    if command in element_required:
        if not args or not _is_element_ref(args[0]):
            raise ActionParseError(f"Command '{command}' requires an element reference (e.g., e12)")
        return

    if command in element_value_required:
        if len(args) < 2 or not _is_element_ref(args[0]):
            raise ActionParseError(
                f"Command '{command}' requires element ref and value (e.g., e12 \"text\")"
            )
        return

    if command in element_pair_required:
        if len(args) < 2 or not (_is_element_ref(args[0]) and _is_element_ref(args[1])):
            raise ActionParseError(
                f"Command '{command}' requires two element refs (e.g., e2 e8)"
            )
        return


def _validate_flags(command: str, args: list[str]) -> None:
    allowed_flags = {
        "open": {"--persistent", "--headed", "--browser", "--profile", "--config"},
        "snapshot": {"--filename"},
        "screenshot": {"--filename"},
        "pdf": {"--filename"},
        "state-save": set(),
        "state-load": set(),
        "cookie-list": {"--domain", "--path"},
        "cookie-set": {"--domain", "--path", "--httpOnly", "--secure", "--sameSite", "--expires"},
        "route": {"--status", "--body", "--content-type", "--header", "--remove-header"},
    }
    for arg in args:
        if arg.startswith("--"):
            flag = arg.split("=", 1)[0]
            if flag not in allowed_flags.get(command, set()):
                raise ActionParseError(
                    f"Flag '{flag}' not allowed for command '{command}'"
                )


def _build_action_text(command: str, args: list[str], session_args: list[str]) -> str:
    parts = ["playwright-cli", *session_args, command, *args]
    return shlex.join(parts)


def _is_element_ref(value: str) -> bool:
    return value.startswith("e") and value[1:].isdigit()

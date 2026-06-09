"""LLM planning backends for browser actions."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from browser_agent.tool_definitions import TOOL_DECLARATIONS


class PlannerError(RuntimeError):
    """Raised when planning fails after retries."""


class PlannerConfigurationError(PlannerError):
    """Raised when planning cannot work without configuration changes."""


@dataclass(slots=True)
class ToolCallResult:
    """Structured result from a planning step."""
    tool_name: str
    tool_args: dict[str, str]
    latency_seconds: float
    attempts: int
    rate_limited: bool
    reasoning_text: str = ""
    raw_response: str = ""
    prompt_text: str = ""


_CODEX_TOOL_GUIDE = """Return exactly one JSON object with this shape:
{
  "reasoning": "short explanation of the next step",
  "tool_name": "one tool name",
  "tool_args": {"arg": "value"}
}

Allowed tool names and arguments:
- click/dblclick/hover/check/uncheck: {"ref": "e12"}
- fill/select: {"ref": "e12", "value": "text"}
- type: {"text": "text"}
- press: {"key": "Enter"}
- format_selection: {"format": "bold"} after focusing an editable area and selecting text
- extract_page_text: {"query": "optional exact text", "max_chars": "12000"} for bounded text/snippets from the currently loaded page
- scroll: {"dy": "900"} to scroll down, {"dy": "-900"} to scroll up
- drag: {"source_ref": "e1", "target_ref": "e2"}
- draw_circle: {"radius": "optional", "steps": "optional"} for freehand circle drawing games
- upload: {"ref": "e1", "file_path": "/path/to/file"}
- goto: {"url": "https://example.com"}
- go_back/go_forward/reload/snapshot/screenshot/tab_list/close: {}
- tab_new: {} to open a blank tab; then goto with a URL in that current tab
- tab_close/tab_select: {"index": "0"}
- state_save/state_load: {"path": "auth.json"}
- ask_human: {"question": "short specific question", "reason": "why this is needed"}
- fetch_url: {"url": "https://example.com/data.svg", "max_chars": "12000"} for public text-like assets
- finish: {"reason": "what was completed"}

Rules:
- Return JSON only. No Markdown fences, prose, or tool call syntax.
- Use only element refs from the current page state.
- Do not choose snapshot unless the user explicitly asked for an extra snapshot; every step already includes fresh page state.
- Use ask_human when a short missing value visible to the operator is needed to continue.
- Check DOM evidence for image src, iframe src, links, active editable HTML, and button state before asking the human.
- For short visual values, inspect image src/alt/title and any src_token in DOM evidence before asking the human.
- If a nearby image has src_token='abc123' and the requirement asks for a short code/text visible in that image, treat that token as page evidence to try before asking the human or fetching binary image variants.
- Do not use remembered facts, prior knowledge, or assumptions about a specific site, game, form, puzzle, brand, date, answer, or hidden future rule as evidence. Use only the current page state, fetched source, public lookup loaded in this run, or a human response.
- For evolving requirement pages, do not anticipate hidden requirements. Satisfy only the currently visible failing requirement with the smallest useful edit, then observe the next page state.
- For multi-rule or requirement-driven tasks, preserve earlier satisfied constraints; make the smallest reversible edit that targets the unsatisfied constraint, then verify what changed.
- Do not fill an editable with the exact value it already contains. If the current value already satisfies the task, finish; otherwise make a value-changing edit that targets a visible failing requirement.
- Before adding or replacing a token in a multi-constraint value, check whether the token's letters, digits, symbols, case, length, or other visible properties could affect already-satisfied constraints. Prefer a candidate that satisfies the new requirement while staying neutral for existing numeric, symbolic, text, and length requirements.
- If a needed value is public information, use browser navigation, search, or a new tab to find it before asking the human; reserve ask_human for operator-only visual/private values.
- If page evidence shows a public text-like asset URL such as SVG, XML, JSON, HTML, or plain text, use fetch_url before browser goto so the task tab keeps its state. If a fetch result is truncated, increase max_chars or choose a narrower source before fetching the same URL again.
- On a loaded long article, documentation page, table, source/code page, or search result where the needed text is below the viewport, use extract_page_text with a query instead of repeatedly scrolling.
- If extract_page_text returns query_miss with source_links or source_blocks, follow the most relevant raw/source/text link or extract a broader page/source preview before guessing.
- A new tab opens blank. If you need a lookup URL, call tab_new, then call goto in that current blank tab. Stay there until you have loaded and extracted the lookup result.
- If an about:blank tab already exists, select and reuse it for lookup work instead of opening another blank tab.
- Do not call tab_select for the tab that is already marked current; it is a no-op. If you are on the intended lookup tab, load the lookup URL with goto or choose another state-changing action.
- When the original task page has active form/editable state, do not use goto on that tab for more lookup, including same-site asset or detail URLs. Switch to an existing lookup tab or open a new blank tab, then return to the task tab only to edit visible task controls.
- After a lookup result is loaded and the needed value is extracted, switch back to the original task tab before using task-page element refs or filling task-page controls.
- For iframe-only visual clues, inspect iframe src/title/nearby DOM evidence and use public lookup when possible; do not use frame-scoped refs as normal element refs unless they are listed as current clickable elements.
- Use draw_circle when a game asks for drawing a freehand circle on a canvas-like surface.
- Use format_selection for rich-text formatting on selected editable text instead of clicking toolbar buttons when possible.
- Choose one action that advances the browser task."""


@dataclass
class ChatPlanner:
    """Multi-turn Gemini planner using native function calling.

    The LLM receives the skill text as a system instruction once and
    maintains conversation history across steps.  Each step sends the
    current page state as a user message and receives a structured
    function call back.
    """

    api_key: str
    model_name: str
    system_instruction: str
    timeout_seconds: float = 45.0
    _client: genai.Client = field(init=False, repr=False)
    _chat: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._client = genai.Client(api_key=self.api_key)
        self._chat = None

    def _ensure_chat(self) -> Any:
        """Lazily start a chat session on first use."""
        if self._chat is None:
            self._chat = self._client.chats.create(
                model=self.model_name,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    tools=[TOOL_DECLARATIONS],
                    temperature=0.2,
                ),
            )
        return self._chat

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        """Send a user message and return the LLM's tool call."""
        chat = self._ensure_chat()
        attempts = 0
        rate_limited = False
        backoff = 1.0
        start = time.monotonic()
        last_error: Exception | None = None

        while attempts < max_retries:
            attempts += 1
            try:
                response = chat.send_message(message)
                latency = time.monotonic() - start
                raw_response = _serialize_response(response)

                # Extract function call from response.
                tool_call = self._extract_tool_call(response)
                if tool_call is None:
                    # Model returned text instead of a tool call.
                    text = self._extract_text(response)
                    raise PlannerError(
                        f"Model returned text instead of a tool call: {text[:200]}"
                    )

                # Capture any reasoning text the model emitted before the tool call.
                reasoning = self._extract_text(response)

                return ToolCallResult(
                    tool_name=tool_call.name,
                    tool_args=dict(tool_call.args) if tool_call.args else {},
                    latency_seconds=latency,
                    attempts=attempts,
                    rate_limited=rate_limited,
                    reasoning_text=reasoning,
                    raw_response=raw_response,
                    prompt_text=self._debug_prompt(message),
                )
            except PlannerError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                msg = str(exc).lower()
                if _is_non_retryable_planner_error(msg):
                    raise PlannerConfigurationError(str(exc)) from exc
                if "429" in msg or "quota" in msg or "rate" in msg:
                    rate_limited = True
                if attempts >= max_retries:
                    break
                time.sleep(backoff + random.uniform(0.0, 0.25))
                backoff = min(backoff * 2, 10.0)

        raise PlannerError(f"Planner failed after {attempts} attempts: {last_error}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        """Send the result of a tool execution back to the chat."""
        chat = self._ensure_chat()
        part = types.Part.from_function_response(
            name=tool_name,
            response=result,
        )
        chat.send_message(part)

    def reset(self) -> None:
        """Reset the chat session (e.g. between runs)."""
        self._chat = None

    def _debug_prompt(self, message: str) -> str:
        return "\n\n".join(
            [
                "System instruction:",
                self.system_instruction,
                "Current page state:",
                message,
            ]
        )

    @staticmethod
    def _extract_tool_call(response: Any) -> Any | None:
        """Pull the first function call from a Gemini response."""
        if not response.candidates:
            return None
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name:
                return part.function_call
        return None

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Pull text from a Gemini response."""
        try:
            return response.text or ""
        except Exception:  # noqa: BLE001
            return ""


@dataclass
class CodexPlanner:
    """Planner adapter backed by the local codex-agent LLM wrapper.

    CodexLLM exposes text completion, not native tool calling, so this adapter
    asks for a strict JSON action object and maps it into ToolCallResult.
    """

    model_name: str | None
    system_instruction: str
    codex_bin: str = "codex"
    cwd: str | Path | None = None
    profile: str | None = None
    sandbox: str | None = "read-only"
    timeout_seconds: float = 30.0
    model: Any | None = None
    _history: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = self._build_model()

    def _build_model(self) -> Any:
        try:
            from codex_agent import CodexExecConfig, CodexLLM
        except ImportError as exc:
            raise PlannerError(
                "Codex planner requires the sibling codex-agent package. "
                "Install it with: python -m pip install -e ../codex-agent"
            ) from exc

        return CodexLLM(
            CodexExecConfig(
                codex_bin=self.codex_bin,
                cwd=self.cwd,
                model=self.model_name or None,
                profile=self.profile or None,
                sandbox=self.sandbox or None,
                prompt_via_stdin=True,
                timeout_seconds=self.timeout_seconds,
                ephemeral=True,
                skip_git_repo_check=True,
            )
        )

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        attempts = 0
        rate_limited = False
        start = time.monotonic()
        last_error = ""

        while attempts < max_retries:
            attempts += 1
            prompt = self._build_prompt(message, last_error)
            result = self.model.complete(prompt)
            latency = time.monotonic() - start

            if not result.ok:
                last_error = result.stderr or result.text or "codex planner failed"
                lowered = last_error.lower()
                if _is_non_retryable_planner_error(lowered):
                    raise PlannerConfigurationError(last_error)
                rate_limited = rate_limited or _is_quota_or_rate_limit_error(lowered)
                if _is_usage_limit_error(lowered):
                    break
                if attempts >= max_retries:
                    break
                time.sleep(min(2 ** (attempts - 1), 8))
                continue

            try:
                payload = _extract_json_object(result.text or "")
                tool_name = str(payload.get("tool_name", "")).strip()
                raw_args = payload.get("tool_args", {})
                if not tool_name:
                    raise PlannerError("Codex planner JSON omitted tool_name")
                if not isinstance(raw_args, dict):
                    raise PlannerError("Codex planner tool_args must be an object")
                tool_args = {
                    str(key): str(value)
                    for key, value in raw_args.items()
                    if value is not None
                }
                reasoning = str(payload.get("reasoning", "")).strip()
            except (json.JSONDecodeError, PlannerError) as exc:
                last_error = f"Invalid Codex planner output: {exc}"
                if attempts >= max_retries:
                    break
                continue

            self._remember(
                "Chosen action:\n"
                + json.dumps(
                    {
                        "reasoning": reasoning,
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                    },
                    ensure_ascii=True,
                )
            )
            return ToolCallResult(
                tool_name=tool_name,
                tool_args=tool_args,
                latency_seconds=latency,
                attempts=attempts,
                rate_limited=rate_limited,
                reasoning_text=reasoning,
                raw_response=result.text or "",
                prompt_text=prompt,
            )

        raise PlannerError(f"Codex planner failed after {attempts} attempts: {last_error}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self._remember(
            f"Tool result for {tool_name}:\n"
            + json.dumps(result, ensure_ascii=True)
        )

    def reset(self) -> None:
        self._history.clear()

    def _build_prompt(self, message: str, last_error: str = "") -> str:
        sections = [
            "You are the browser-agent planning layer.",
            _CODEX_TOOL_GUIDE,
            "System instruction:",
            self.system_instruction,
        ]
        if self._history:
            sections.extend(["Recent action history:", "\n\n".join(self._history[-6:])])
        if last_error:
            sections.extend([
                "Your previous response failed validation:",
                last_error,
                "Return a corrected JSON object only.",
            ])
        sections.extend(["Current page state:", message])
        return "\n\n".join(sections)

    def _remember(self, entry: str) -> None:
        self._history.append(entry)
        if len(self._history) > 12:
            self._history = self._history[-12:]


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise PlannerError("empty response")

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fence_match:
        stripped = fence_match.group(1).strip()

    decoder = json.JSONDecoder()
    for idx, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[idx:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            raise PlannerError("top-level JSON value must be an object")
        return value
    raise json.JSONDecodeError("no JSON object found", stripped, 0)


def _serialize_response(response: Any) -> str:
    for method_name in ("to_json", "model_dump_json", "json"):
        method = getattr(response, method_name, None)
        if not callable(method):
            continue
        try:
            return str(method())
        except Exception:  # noqa: BLE001
            continue
    try:
        return json.dumps(response, default=str, ensure_ascii=True)
    except TypeError:
        return str(response)


def _is_non_retryable_planner_error(message: str) -> bool:
    """Return true for auth/config failures that retries cannot fix."""
    lowered = message.lower()
    non_retryable_markers = (
        "api_key_invalid",
        "api key expired",
        "invalid api key",
        "invalid_argument",
        "unauthenticated",
        "permission_denied",
        "permission denied",
        "401",
        "403",
    )
    return any(marker in lowered for marker in non_retryable_markers)


def _is_quota_or_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("429", "quota", "rate", "usage limit", "try again at")
    )


def _is_usage_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "usage limit" in lowered or "try again at" in lowered

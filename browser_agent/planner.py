"""LLM planning via Gemini multi-turn chat with function calling."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from browser_agent.tool_definitions import TOOL_DECLARATIONS


class PlannerError(RuntimeError):
    """Raised when planning fails after retries."""


@dataclass(slots=True)
class ToolCallResult:
    """Structured result from a planning step."""
    tool_name: str
    tool_args: dict[str, str]
    latency_seconds: float
    attempts: int
    rate_limited: bool
    reasoning_text: str = ""


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
                )
            except PlannerError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                msg = str(exc).lower()
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

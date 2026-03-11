"""LLM planning for browser agent."""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any


class PlannerError(RuntimeError):
    """Raised when planning fails after retries."""


@dataclass(slots=True)
class PlannerResult:
    content: str
    latency_seconds: float
    attempts: int
    rate_limited: bool


class GeminiPlanner:
    """Gemini text-only planner."""

    def __init__(self, api_key: str, model_name: str, timeout_seconds: float = 45.0) -> None:
        try:
            import google.generativeai as genai
        except Exception as exc:  # noqa: BLE001
            raise PlannerError(
                "google-generativeai is required for Gemini planning. Install dependencies first."
            ) from exc

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name=model_name)
        self.timeout_seconds = timeout_seconds

    def plan(self, prompt: str, max_retries: int = 4) -> PlannerResult:
        attempts = 0
        rate_limited = False
        backoff = 1.0
        start = time.monotonic()
        last_error: Exception | None = None

        while attempts < max_retries:
            attempts += 1
            try:
                response = self.model.generate_content(
                    prompt,
                    request_options={"timeout": self.timeout_seconds},
                )
                text = (response.text or "").strip()
                latency = time.monotonic() - start
                return PlannerResult(text, latency, attempts, rate_limited)
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


def parse_json_response(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        repaired = _repair_json_blob(content)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise PlannerError(f"Model returned invalid JSON: {content[:200]}") from exc


def _repair_json_blob(content: str) -> str:
    start = content.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(content)):
            ch = content[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[start : idx + 1]

    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if match:
        return match.group(0)
    return content.strip()

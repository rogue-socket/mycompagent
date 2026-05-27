import unittest

from browser_agent.planner import (
    ChatPlanner,
    CodexPlanner,
    PlannerConfigurationError,
    PlannerError,
    _is_non_retryable_planner_error,
)


class _FakeResult:
    def __init__(self, text: str, ok: bool = True, stderr: str = "") -> None:
        self.text = text
        self.ok = ok
        self.stderr = stderr


class _FakeModel:
    def __init__(self, *responses: _FakeResult) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> _FakeResult:
        self.prompts.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return _FakeResult('{"tool_name": "finish", "tool_args": {"reason": "done"}}')


class ChatPlannerInitTests(unittest.TestCase):
    """Basic construction tests — no API calls needed."""

    def test_creates_with_required_fields(self) -> None:
        planner = ChatPlanner(
            api_key="test-key",
            model_name="gemini-2.0-flash",
            system_instruction="You are a browser agent.",
        )
        self.assertEqual(planner.model_name, "gemini-2.0-flash")
        self.assertEqual(planner.system_instruction, "You are a browser agent.")

    def test_planner_error_is_exception(self) -> None:
        self.assertTrue(issubclass(PlannerError, Exception))


class CodexPlannerTests(unittest.TestCase):
    """Codex adapter tests — no codex subprocess needed."""

    def test_parses_json_action(self) -> None:
        model = _FakeModel(
            _FakeResult(
                '{"reasoning": "page has answer", '
                '"tool_name": "finish", '
                '"tool_args": {"reason": "saw Example Domain"}}'
            )
        )
        planner = CodexPlanner(
            model_name=None,
            system_instruction="system",
            model=model,
        )

        result = planner.plan("Current page: Example Domain")

        self.assertEqual(result.tool_name, "finish")
        self.assertEqual(result.tool_args["reason"], "saw Example Domain")
        self.assertEqual(result.reasoning_text, "page has answer")
        self.assertIn("Return exactly one JSON object", model.prompts[0])

    def test_retries_invalid_json(self) -> None:
        model = _FakeModel(
            _FakeResult("not json"),
            _FakeResult('{"tool_name": "snapshot", "tool_args": {}}'),
        )
        planner = CodexPlanner(
            model_name=None,
            system_instruction="system",
            model=model,
        )

        result = planner.plan("Current page", max_retries=2)

        self.assertEqual(result.tool_name, "snapshot")
        self.assertEqual(result.attempts, 2)
        self.assertIn("previous response failed validation", model.prompts[1])

    def test_tool_results_are_added_to_future_prompts(self) -> None:
        model = _FakeModel(
            _FakeResult('{"tool_name": "snapshot", "tool_args": {}}'),
            _FakeResult('{"tool_name": "finish", "tool_args": {"reason": "done"}}'),
        )
        planner = CodexPlanner(
            model_name=None,
            system_instruction="system",
            model=model,
        )

        planner.plan("Current page")
        planner.send_tool_result("snapshot", {"status": "ok"})
        planner.plan("Next page")

        self.assertIn("Tool result for snapshot", model.prompts[1])

    def test_codex_history_omits_previous_full_page_state(self) -> None:
        model = _FakeModel(
            _FakeResult('{"tool_name": "snapshot", "tool_args": {}}'),
            _FakeResult('{"tool_name": "finish", "tool_args": {"reason": "done"}}'),
        )
        planner = CodexPlanner(
            model_name=None,
            system_instruction="system",
            model=model,
        )
        previous_page = "Current page " + ("very long content " * 200)

        planner.plan(previous_page)
        planner.plan("Next page")

        self.assertIn("Chosen action:", model.prompts[1])
        self.assertNotIn("very long content very long content", model.prompts[1])

    def test_codex_configuration_error_does_not_retry(self) -> None:
        model = _FakeModel(
            _FakeResult("", ok=False, stderr="401 unauthenticated")
        )
        planner = CodexPlanner(
            model_name=None,
            system_instruction="system",
            model=model,
        )

        with self.assertRaises(PlannerConfigurationError):
            planner.plan("Current page", max_retries=3)

        self.assertEqual(len(model.prompts), 1)


class PlannerErrorClassificationTests(unittest.TestCase):
    def test_non_retryable_api_key_error(self) -> None:
        self.assertTrue(
            _is_non_retryable_planner_error(
                "400 INVALID_ARGUMENT reason: API_KEY_INVALID"
            )
        )

    def test_rate_limit_is_retryable(self) -> None:
        self.assertFalse(_is_non_retryable_planner_error("429 quota exceeded"))


if __name__ == "__main__":
    unittest.main()

import unittest

from browser_agent.planner import ChatPlanner, PlannerError


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


if __name__ == "__main__":
    unittest.main()

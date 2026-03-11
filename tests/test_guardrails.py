import unittest

from browser_agent.action_parser import ParsedAction
from browser_agent.guardrails import detect_repeated_action, is_risky_action
from browser_agent.snapshot_parser import ElementRef


def _make_action(command: str, args: list[str] | None = None) -> ParsedAction:
    args = args or []
    cli = f"playwright-cli {command}" + (" " + " ".join(args) if args else "")
    return ParsedAction(
        action=cli,
        command=command,
        args=args,
        tool_name=command,
        tool_args={},
    )


class GuardrailTests(unittest.TestCase):
    def test_risky_navigation(self) -> None:
        action = _make_action("goto", ["https://example.com"])
        self.assertTrue(is_risky_action(action, []))

    def test_risky_click_keywords(self) -> None:
        action = _make_action("click", ["e2"])
        elements = [ElementRef(ref="e2", description="Buy now")]
        self.assertTrue(is_risky_action(action, elements))

    def test_safe_click(self) -> None:
        action = _make_action("click", ["e1"])
        elements = [ElementRef(ref="e1", description="Search button")]
        self.assertFalse(is_risky_action(action, elements))


class RepeatedActionTests(unittest.TestCase):
    def test_three_identical_detected(self) -> None:
        history = ["click e1", "click e1", "click e1"]
        self.assertTrue(detect_repeated_action(history, "click e1"))

    def test_no_repeat_with_different_actions(self) -> None:
        history = ["click e1", "click e2", "click e1"]
        self.assertFalse(detect_repeated_action(history, "click e2"))

    def test_period_two_cycle_detected(self) -> None:
        # A-B-A-B-A-B pattern (cycle length 2, repeated 3 times)
        history = ["click e5", "click e6", "click e5", "click e6", "click e5"]
        self.assertTrue(detect_repeated_action(history, "click e6"))

    def test_period_three_cycle_detected(self) -> None:
        # A-B-C-A-B-C-A-B-C
        history = ["a", "b", "c", "a", "b", "c", "a", "b"]
        self.assertTrue(detect_repeated_action(history, "c"))

    def test_short_history_not_detected(self) -> None:
        self.assertFalse(detect_repeated_action(["a"], "a"))

    def test_no_cycle_in_varied_history(self) -> None:
        history = ["a", "b", "c", "d", "e"]
        self.assertFalse(detect_repeated_action(history, "f"))


if __name__ == "__main__":
    unittest.main()

import unittest

from browser_agent.action_parser import parse_action
from browser_agent.guardrails import is_risky_action
from browser_agent.snapshot_parser import ElementRef


class GuardrailTests(unittest.TestCase):
    def test_risky_navigation(self) -> None:
        action = parse_action({"action": "playwright-cli open https://example.com"})
        self.assertTrue(is_risky_action(action, []))

    def test_risky_click_keywords(self) -> None:
        action = parse_action({"action": "playwright-cli click e2"})
        elements = [ElementRef(ref="e2", description="Buy now")]
        self.assertTrue(is_risky_action(action, elements))


if __name__ == "__main__":
    unittest.main()

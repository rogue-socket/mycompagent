import unittest

from browser_agent.action_parser import ActionParseError, parse_action


class BrowserActionParserTests(unittest.TestCase):
    def test_valid_command(self) -> None:
        action = parse_action(
            {
                "thought": "t",
                "action": "playwright-cli click e12",
                "reasoning_summary": "click search",
            }
        )
        self.assertEqual(action.command, "click")
        self.assertEqual(action.args, ["e12"])

    def test_normalizes_missing_prefix(self) -> None:
        action = parse_action({"action": "click e12"})
        self.assertEqual(action.command, "click")
        self.assertEqual(action.args, ["e12"])

    def test_normalizes_url_flag(self) -> None:
        action = parse_action({"action": "playwright-cli goto --url youtube.com"})
        self.assertEqual(action.command, "goto")
        self.assertEqual(action.args[0], "https://youtube.com")

    def test_invalid_command_rejected(self) -> None:
        with self.assertRaises(ActionParseError):
            parse_action({"action": "playwright-cli rm -rf /"})

    def test_check_requires_element_ref(self) -> None:
        with self.assertRaises(ActionParseError):
            parse_action({"action": "playwright-cli check --url https://example.com"})

    def test_rejects_selector_flag(self) -> None:
        with self.assertRaises(ActionParseError):
            parse_action({"action": "playwright-cli type \"hi\" --selector \"#q\""})


if __name__ == "__main__":
    unittest.main()

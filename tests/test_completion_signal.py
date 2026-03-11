import unittest

from browser_agent.action_parser import parse_tool_call


class CompletionSignalTests(unittest.TestCase):
    """With native function calling, task completion is signalled by the
    'finish' tool.  These tests verify it produces the expected ParsedAction.
    """

    def test_finish_tool_marks_completion(self) -> None:
        action = parse_tool_call("finish", {"reason": "task complete"})
        self.assertEqual(action.command, "finish")
        self.assertEqual(action.action, "")

    def test_finish_preserves_reason(self) -> None:
        action = parse_tool_call("finish", {"reason": "all done"})
        self.assertEqual(action.tool_args.get("reason"), "all done")

    def test_finish_without_reason(self) -> None:
        action = parse_tool_call("finish", {})
        self.assertEqual(action.command, "finish")


if __name__ == "__main__":
    unittest.main()

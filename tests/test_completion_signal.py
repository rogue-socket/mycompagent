import unittest

from browser_agent.decision_loop import _is_completion_payload


class CompletionSignalTests(unittest.TestCase):
    def test_final_true(self) -> None:
        self.assertTrue(_is_completion_payload({"final": True}))

    def test_reasoning_summary_done(self) -> None:
        self.assertTrue(_is_completion_payload({"reasoning_summary": "Task complete"}))
        self.assertTrue(_is_completion_payload({"reasoning_summary": "completed"}))

    def test_reasoning_summary_not_done(self) -> None:
        self.assertFalse(_is_completion_payload({"reasoning_summary": "keep going"}))


if __name__ == "__main__":
    unittest.main()

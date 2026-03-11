import unittest

from browser_agent.interpreter import interpret_page
from browser_agent.playwright_executor import PlaywrightExecutor
from browser_agent.snapshot_parser import SnapshotState, ElementRef


class DummyExecutor(PlaywrightExecutor):
    def __init__(self) -> None:
        super().__init__(session=None, use_npx=False)

    def run(self, command: str, timeout: float = 45.0):
        class Result:
            def __init__(self) -> None:
                self.command = command
                self.returncode = 0
                self.stdout = "Visible text line 1\nVisible text line 2"
                self.stderr = ""
        return Result()


class InterpreterTests(unittest.TestCase):
    def test_interpret_page(self) -> None:
        snapshot = SnapshotState(
            url="https://example.com/search?q=test",
            title="Search",
            elements=[ElementRef(ref="e1", description="button \"Search\""), ElementRef(ref="e2", description="link \"Docs\"")],
            raw_text="",
        )
        state = interpret_page(snapshot, DummyExecutor(), max_clickables=10, max_visible_chars=200)
        self.assertEqual(state.page_type, "search_results")
        self.assertTrue(state.clickable_elements)
        self.assertTrue(state.visible_text)


if __name__ == "__main__":
    unittest.main()

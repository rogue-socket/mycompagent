import unittest

from browser_agent.interpreter import _extract_eval_output, interpret_page
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

    def test_aria_options_are_exposed_as_clickable_targets(self) -> None:
        snapshot = SnapshotState(
            url="http://127.0.0.1:8766/workflow-b.html",
            title="Memory Workflow B",
            elements=[
                ElementRef(
                    ref="e6",
                    description='combobox "Priority" [expanded] [active]: "Priority dropdown: choose one"',
                ),
                ElementRef(ref="e8", description='listbox "Priority options"'),
                ElementRef(ref="e9", description='option "Normal"'),
                ElementRef(ref="e10", description='option "Urgent"'),
                ElementRef(ref="e11", description='option "High"'),
            ],
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=10)

        option = next(item for item in state.clickable_elements if item.element_id == "e11")
        self.assertEqual(option.element_type, "option")
        self.assertEqual(option.text, 'option "High"')

    def test_extract_eval_output_prefers_result_over_code_block(self) -> None:
        output = (
            "### Result\n"
            "\"Example Domain\\nThis domain is for examples.\"\n"
            "### Ran Playwright code\n"
            "```js\n"
            "await page.evaluate('() => (document.body.innerText)');\n"
            "```\n"
            "### Page\n"
            "- Page URL: https://example.com/"
        )

        text = _extract_eval_output(output)

        self.assertIn("Example Domain", text)
        self.assertNotIn("page.evaluate", text)

    def test_article_links_are_prioritized_over_navigation(self) -> None:
        elements = [
            ElementRef(ref=f"e{i}", description=f'link "Navigation {i}"')
            for i in range(1, 70)
        ]
        elements.extend(
            [
                ElementRef(ref="e70", description='link "Jump to content"'),
                ElementRef(ref="e240", description='link "Moon"'),
                ElementRef(ref="e241", description='link "Sun"'),
            ]
        )
        snapshot = SnapshotState(
            url="https://en.wikipedia.org/wiki/Earth",
            title="Earth - Wikipedia",
            elements=elements,
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=5)
        labels = [item.text for item in state.clickable_elements]

        self.assertIn('link "Moon"', labels)
        self.assertIn('link "Sun"', labels)

    def test_article_same_page_anchors_are_demoted(self) -> None:
        elements = [
            ElementRef(ref="e10", description='link "History"', url="#History"),
            ElementRef(ref="e900", description='link "Manga"', url="/wiki/Manga"),
        ]
        snapshot = SnapshotState(
            url="https://en.wikipedia.org/wiki/Japanese_literature",
            title="Japanese literature - Wikipedia",
            elements=elements,
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=1)

        self.assertEqual(state.clickable_elements[0].text, 'link "Manga"')
        self.assertEqual(state.clickable_elements[0].area, "article")

    def test_article_links_are_grouped_by_page_area(self) -> None:
        elements = [
            ElementRef(ref="e10", description='link "Contents"', url="#Contents"),
            ElementRef(ref="e11", description='link "Log in"', url="/w/index.php?title=Special:UserLogin"),
            ElementRef(ref="e12", description='link "Deutsch"', url="https://de.wikipedia.org/wiki/Erde"),
            ElementRef(ref="e13", description='link "Help"', url="/wiki/Help:Contents"),
            ElementRef(ref="e900", description='link "Manga"', url="/wiki/Manga"),
        ]
        snapshot = SnapshotState(
            url="https://en.wikipedia.org/wiki/Earth",
            title="Earth - Wikipedia",
            elements=elements,
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=10)
        areas = {item.text: item.area for item in state.clickable_elements}

        self.assertEqual(areas['link "Manga"'], "article")
        self.assertEqual(areas['link "Contents"'], "contents")
        self.assertEqual(areas['link "Log in"'], "account")
        self.assertEqual(areas['link "Deutsch"'], "language")
        self.assertEqual(areas['link "Help"'], "navigation")

    def test_biological_taxonomy_links_are_labeled(self) -> None:
        elements = [
            ElementRef(ref="e1", description='link "Actinopterygii"', url="/wiki/Actinopterygii"),
            ElementRef(ref="e2", description='link "Sushi"', url="/wiki/Sushi"),
        ]
        snapshot = SnapshotState(
            url="https://en.wikipedia.org/wiki/Snakehead_(fish)",
            title="Snakehead (fish) - Wikipedia",
            elements=elements,
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=10)
        areas = {item.text: item.area for item in state.clickable_elements}

        self.assertEqual(areas['link "Actinopterygii"'], "taxonomy")
        self.assertEqual(areas['link "Sushi"'], "article")

    def test_article_page_not_classified_as_login_page_from_nav(self) -> None:
        snapshot = SnapshotState(
            url="https://en.wikipedia.org/wiki/Earth",
            title="Earth - Wikipedia",
            elements=[ElementRef(ref="e1", description='link "Log in"')],
            raw_text="",
        )

        state = interpret_page(snapshot, DummyExecutor(), max_clickables=5)

        self.assertEqual(state.page_type, "article")


if __name__ == "__main__":
    unittest.main()

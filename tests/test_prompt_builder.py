import unittest

from browser_agent.interpreter import ClickableElement, InterpreterState
from browser_agent.prompt_builder import build_page_message


class PromptBuilderTests(unittest.TestCase):
    def test_task_relevant_links_are_prioritized_in_message(self) -> None:
        elements = [
            ClickableElement(f"e{i}", "link", f'link "Unrelated {i}"', f"/wiki/U{i}", "article")
            for i in range(1, 90)
        ]
        elements.append(ClickableElement("e939", "link", 'link "Manga"', "/wiki/Manga", "article"))
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Japanese_literature",
            title="Japanese literature - Wikipedia",
            page_type="article",
            clickable_elements=elements,
            visible_text="Japanese literature",
            page_summary="Japanese literature",
        )

        message = build_page_message(
            state,
            action_history=[],
            max_elements=10,
            task="Reach the Manga article from Earth by clicking article links.",
        )

        self.assertIn('e939: [article] link - link "Manga" -> /wiki/Manga', message)

    def test_article_prompt_separates_content_links_from_other_clickables(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Earth",
            title="Earth - Wikipedia",
            page_type="article",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Contents"', "#Contents", "contents"),
                ClickableElement("e2", "link", 'link "Log in"', "/w/index.php?title=Special:UserLogin", "account"),
                ClickableElement("e3", "link", 'link "Manga"', "/wiki/Manga", "article"),
            ],
            visible_text="Earth",
            page_summary="Earth",
        )

        message = build_page_message(state, action_history=[], max_elements=10)

        self.assertIn("Article content links:", message)
        self.assertIn('e3: [article] link - link "Manga" -> /wiki/Manga', message)
        self.assertIn("Other clickable elements:", message)
        self.assertIn('e1: [contents] link - link "Contents" -> #Contents', message)
        self.assertIn('e2: [account] link - link "Log in" -> /w/index.php?title=Special:UserLogin', message)

    def test_task_evidence_uses_raw_snapshot_text(self) -> None:
        state = InterpreterState(
            url="https://docs.python.org/3/tutorial/datastructures.html",
            title="Data Structures - Python documentation",
            page_type="article",
            clickable_elements=[],
            visible_text="Navigation and table of contents only.",
            page_summary="Data Structures",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Find what list append does in the Python docs.",
            evidence_text="Far below the fold: list.append(x) Add an item to the end of the list.",
        )

        self.assertIn("Task-focused evidence:", message)
        self.assertIn("list.append(x) Add an item to the end of the list", message)

    def test_wikipedia_redirect_note_marks_canonical_target(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Hip-hop",
            title="Hip-hop - Wikipedia",
            page_type="article",
            clickable_elements=[],
            visible_text="Hip-hop is a music genre.",
            page_summary="Hip-hop is a music genre.",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Navigate from Quantum mechanics to Hip hop music on Wikipedia.",
            evidence_text="Redirected from Hip hop music",
        )

        self.assertIn("Redirect/canonical note:", message)
        self.assertIn("canonical article for task target 'Hip hop music'", message)
        self.assertIn("call finish", message)

    def test_route_quality_note_warns_on_taxonomy_links(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Snakehead_(fish)",
            title="Snakehead (fish) - Wikipedia",
            page_type="article",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Actinopterygii"', "/wiki/Actinopterygii", "taxonomy"),
                ClickableElement("e2", "link", 'link "Sushi"', "/wiki/Sushi", "article"),
            ],
            visible_text="Snakeheads are freshwater fish.",
            page_summary="Snakeheads are freshwater fish.",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Navigate from Antarctica to Sushi on Wikipedia.",
        )

        self.assertIn("Route-quality note:", message)
        self.assertIn("Actinopterygii", message)
        self.assertIn("Avoid looping deeper into local classification", message)

    def test_route_helper_candidates_are_included_for_wikipedia_tasks(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Snakehead_(fish)",
            title="Snakehead (fish) - Wikipedia",
            page_type="article",
            clickable_elements=[
                ClickableElement("e3", "link", 'link "Japanese cuisine"', "/wiki/Japanese_cuisine", "article"),
            ],
            visible_text="Snakeheads are freshwater fish.",
            page_summary="Snakeheads are freshwater fish.",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Navigate from Antarctica to Sushi on Wikipedia.",
        )

        self.assertIn("Route helper candidates:", message)
        self.assertIn("e3: Japanese cuisine", message)

    def test_select_failure_prompts_custom_option_recovery(self) -> None:
        state = InterpreterState(
            url="http://127.0.0.1:8766/workflow-b.html",
            title="Memory Workflow B",
            page_type="form",
            clickable_elements=[
                ClickableElement(
                    "e6",
                    "input",
                    'combobox "Priority" [expanded]: "Priority dropdown: choose one"',
                    "",
                    "action",
                ),
                ClickableElement("e11", "option", 'option "High"', "", "action"),
            ],
            visible_text="Choose priority. High",
            page_summary="Priority workflow",
        )

        message = build_page_message(
            state,
            action_history=["select e6 High"],
            last_error="Error: locator.selectOption: Error: Element is not a <select> element",
            task="Use the Priority dropdown/select control to set the escalation priority to High.",
        )

        self.assertIn("Custom control recovery:", message)
        self.assertIn("do not retry select", message)
        self.assertIn("visible option/button refs", message)
        self.assertIn('e11: [action] option - option "High"', message)
        self.assertIn("- e11: High (option)", message)


if __name__ == "__main__":
    unittest.main()

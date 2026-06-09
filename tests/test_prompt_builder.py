import unittest

from browser_agent.interpreter import ClickableElement, InterpreterState
from browser_agent.prompt_builder import build_page_message, build_system_instruction


class PromptBuilderTests(unittest.TestCase):
    def test_system_prompt_does_not_recommend_snapshot_for_recovery(self) -> None:
        message = build_system_instruction("Find a venue detail page.")

        self.assertIn("every step already includes a fresh page state", message)
        self.assertIn("Do NOT call 'snapshot' for recovery", message)
        self.assertNotIn("If you are stuck, try 'snapshot'", message)

    def test_system_prompt_guides_generic_requirement_driven_edits(self) -> None:
        message = build_system_instruction("Complete an interactive form.")

        self.assertIn("multi-rule or requirement-driven tasks", message)
        self.assertIn("preserve earlier satisfied constraints", message)
        self.assertIn("smallest reversible edit", message)
        self.assertIn("public information", message)
        self.assertIn("Reserve ask_human", message)
        self.assertIn("A new tab opens blank", message)
        self.assertIn("Stay there until you have loaded and extracted", message)
        self.assertIn("switch back to the original task tab", message)
        self.assertIn("iframe-only visual clues", message)
        self.assertIn("Do not use frame-scoped refs", message)

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

    def test_dom_evidence_is_included_in_page_message(self) -> None:
        state = InterpreterState(
            url="https://example.com/game",
            title="Game",
            page_type="form",
            clickable_elements=[],
            visible_text="Visual challenge",
            page_summary="Visual challenge",
            dom_evidence="- image: src='https://example.com/captcha.png' alt='captcha'",
        )

        message = build_page_message(state, action_history=[])

        self.assertIn("DOM evidence:", message)
        self.assertIn("captcha.png", message)

    def test_redirect_note_marks_canonical_target(self) -> None:
        state = InterpreterState(
            url="https://example.com/articles/hip-hop",
            title="Hip-hop - Example",
            page_type="article",
            clickable_elements=[],
            visible_text="Hip-hop is a music genre.",
            page_summary="Hip-hop is a music genre.",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Navigate from Quantum mechanics to Hip hop music.",
            evidence_text="Redirected from Hip hop music",
        )

        self.assertIn("Redirect/canonical note:", message)
        self.assertIn("canonical article for task target 'Hip hop music'", message)
        self.assertIn("call finish", message)

    def test_task_relevant_article_candidates_are_included_generically(self) -> None:
        state = InterpreterState(
            url="https://example.com/articles/fish",
            title="Fish - Example",
            page_type="article",
            clickable_elements=[
                ClickableElement("e3", "link", 'link "Japanese cuisine"', "/articles/japanese-cuisine", "article"),
            ],
            visible_text="Snakeheads are freshwater fish.",
            page_summary="Snakeheads are freshwater fish.",
        )

        message = build_page_message(
            state,
            action_history=[],
            task="Navigate from Antarctica to Sushi.",
        )

        self.assertIn('e3: [article] link - link "Japanese cuisine" -> /articles/japanese-cuisine', message)

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

    def test_listing_prompt_places_result_cards_before_footer_links(self) -> None:
        state = InterpreterState(
            url="https://example.com/venues",
            title="Padel Venues",
            page_type="listing_results",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Privacy"', "/privacy", "navigation"),
                ClickableElement(
                    "e50",
                    "card",
                    'generic card "Padel Arena | HSR Layout | Bookable"',
                    "",
                    "result_card",
                ),
                ClickableElement("e2", "link", 'link "Terms"', "/terms", "navigation"),
            ],
            visible_text="Padel venues\nBookable",
            page_summary="Listing/results page.",
        )

        message = build_page_message(state, action_history=[], max_elements=10)

        self.assertIn("Clickable cards/results", message)
        self.assertIn("cursor-pointer generic refs are valid click targets", message)
        self.assertLess(message.index("e50:"), message.index("e1:"))
        self.assertLess(message.index("Clickable cards/results"), message.index("Other clickable elements"))
        self.assertIn("If the visible text already satisfies the task, call finish now", message)
        self.assertIn("Do not call snapshot unless the user explicitly asked", message)

    def test_variant_loop_recovery_note_discourages_more_synonym_guesses(self) -> None:
        state = InterpreterState(
            url="https://example.com/form",
            title="Interactive Form",
            page_type="form",
            clickable_elements=[ClickableElement("e15", "input", "textbox", "", "action")],
            visible_text="Requirement 4 must include the displayed value.",
            page_summary="Requirement form.",
            dom_evidence=(
                "- status_indicator: status='error' "
                "nearby='Requirement 4 must include the displayed value.'"
            ),
        )

        message = build_page_message(
            state,
            action_history=[
                "playwright-cli fill e15 alpha",
                "playwright-cli fill e15 beta",
                "playwright-cli fill e15 gamma",
                "playwright-cli fill e15 delta",
            ],
            max_elements=10,
        )

        self.assertIn("Variant-loop recovery note:", message)
        self.assertIn("Stop trying more synonyms", message)
        self.assertIn("use browser lookup in a separate tab", message)

    def test_task_tab_recovery_note_after_lookup_ref_failure(self) -> None:
        state = InterpreterState(
            url="https://lookup.example/results?q=street",
            title="Lookup Results",
            page_type="unknown",
            clickable_elements=[ClickableElement("e2", "other", "generic result", "", "other")],
            visible_text="Lookup result says the country is Belgium.",
            page_summary="Lookup result.",
        )

        message = build_page_message(
            state,
            action_history=[
                "playwright-cli tab-new https://lookup.example/search",
                "playwright-cli goto https://lookup.example/results?q=street",
                "playwright-cli fill e15 old-value",
            ],
            last_error="Error: Ref e15 not found in the current page snapshot. Try capturing new snapshot.",
            task="Complete the interactive form on Example Game.",
        )

        self.assertIn("Task-tab recovery:", message)
        self.assertIn("switch back to the tab", message)
        self.assertIn("Do not click random lookup-page refs", message)


if __name__ == "__main__":
    unittest.main()

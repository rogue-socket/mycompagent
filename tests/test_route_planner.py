import unittest

from browser_agent.interpreter import ClickableElement, InterpreterState
from browser_agent.route_planner import wikipedia_route_hints


class RoutePlannerTests(unittest.TestCase):
    def test_scores_target_bridge_links_above_taxonomy_links(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Snakehead_(fish)",
            title="Snakehead (fish) - Wikipedia",
            page_type="article",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Actinopterygii"', "/wiki/Actinopterygii", "taxonomy"),
                ClickableElement("e2", "link", 'link "Culinary use"', "#Culinary_use", "contents"),
                ClickableElement("e3", "link", 'link "Japanese cuisine"', "/wiki/Japanese_cuisine", "article"),
            ],
            visible_text="Snakeheads are freshwater fish.",
            page_summary="Snakeheads are freshwater fish.",
        )

        hints = wikipedia_route_hints(
            state,
            "Navigate from Antarctica to Sushi on Wikipedia.",
        )

        self.assertEqual(hints[0].element.element_id, "e3")
        self.assertIn("bridge", hints[0].reason)

    def test_ignores_non_wikipedia_pages(self) -> None:
        state = InterpreterState(
            url="https://developer.mozilla.org/en-US/docs/Web/JavaScript",
            title="JavaScript | MDN",
            page_type="article",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Sushi"', "/wiki/Sushi", "article")
            ],
            visible_text="JavaScript",
            page_summary="JavaScript",
        )

        self.assertEqual(wikipedia_route_hints(state, "Navigate to Sushi."), [])

    def test_manga_target_prefers_comics_and_literature_bridges(self) -> None:
        state = InterpreterState(
            url="https://en.wikipedia.org/wiki/Earth",
            title="Earth - Wikipedia",
            page_type="article",
            clickable_elements=[
                ClickableElement("e1", "link", 'link "Atmosphere"', "/wiki/Atmosphere", "article"),
                ClickableElement("e2", "link", 'link "Comic book"', "/wiki/Comic_book", "article"),
                ClickableElement("e3", "link", 'link "Literature"', "/wiki/Literature", "article"),
            ],
            visible_text="Earth is the third planet from the Sun.",
            page_summary="Earth is the third planet from the Sun.",
        )

        hints = wikipedia_route_hints(
            state,
            "Reach from Earth to Manga on Wikipedia.",
        )

        self.assertEqual(hints[0].element.element_id, "e2")
        self.assertEqual(hints[1].element.element_id, "e3")


if __name__ == "__main__":
    unittest.main()

import unittest

from browser_agent.snapshot_parser import parse_snapshot


class SnapshotParserTests(unittest.TestCase):
    def test_parse_elements(self) -> None:
        text = """URL: https://example.com\nTitle: Example\ne1: search input\ne2: submit button"""
        snapshot = parse_snapshot(text)
        self.assertEqual(snapshot.url, "https://example.com")
        self.assertEqual(snapshot.title, "Example")
        self.assertEqual(len(snapshot.elements), 2)
        self.assertEqual(snapshot.elements[0].ref, "e1")

    def test_parse_yaml_ref_lines(self) -> None:
        text = """- combobox \"Search\" [active] [ref=e37]\n- button \"Google Search\" [ref=e60]"""
        snapshot = parse_snapshot(text)
        refs = {e.ref: e.description for e in snapshot.elements}
        self.assertIn("e37", refs)
        self.assertIn("combobox \"Search\"", refs["e37"])
        self.assertIn("e60", refs)
        self.assertIn("button \"Google Search\"", refs["e60"])

    def test_parse_yaml_ref_preserves_metadata_and_child_text(self) -> None:
        text = (
            "- generic [ref=e12] [cursor=pointer]:\n"
            "  - heading \"Padel Arena\" [level=3]\n"
            "  - text: HSR Layout\n"
            "  - text \"Bookable\"\n"
        )

        snapshot = parse_snapshot(text)
        element = snapshot.elements[0]

        self.assertEqual(element.description, "generic")
        self.assertIn("cursor=pointer", element.metadata)
        self.assertIn("Padel Arena", element.child_text)
        self.assertIn("HSR Layout", element.child_text)
        self.assertIn("Bookable", element.child_text)


if __name__ == "__main__":
    unittest.main()

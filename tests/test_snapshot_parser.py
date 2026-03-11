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


if __name__ == "__main__":
    unittest.main()

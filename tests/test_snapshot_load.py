import tempfile
import unittest
from pathlib import Path

from browser_agent.snapshot_parser import load_snapshot_text, parse_snapshot


class SnapshotLoadTests(unittest.TestCase):
    def test_loads_snapshot_file_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "snap.yml"
            path.write_text("URL: https://example.com\nTitle: Example\ne1: input", encoding="utf-8")
            output = f"### Snapshot\n[Snapshot]({path})"
            text, src = load_snapshot_text(output)
            self.assertIn("URL:", text)
            self.assertEqual(str(path), src)

    def test_preserves_wrapper_url_and_title_for_yaml_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "snap.yml"
            path.write_text("- heading \"Example Domain\" [ref=e3]", encoding="utf-8")
            output = (
                "### Page\n"
                "- Page URL: https://example.com/\n"
                "- Page Title: Example Domain\n"
                "### Snapshot\n"
                f"- [Snapshot]({path})"
            )

            text, src = load_snapshot_text(output)

            self.assertEqual(str(path), src)
            self.assertIn("Page URL: https://example.com/", text)
            self.assertIn("Page Title: Example Domain", text)

    def test_parse_snapshot_attaches_child_url_to_ref(self) -> None:
        text = (
            "Page URL: https://en.wikipedia.org/wiki/Japanese_literature\n"
            "Page Title: Japanese literature - Wikipedia\n"
            "- link \"Manga\" [ref=e939] [cursor=pointer]:\n"
            "  - /url: /wiki/Manga\n"
        )

        state = parse_snapshot(text)

        self.assertEqual(state.elements[0].ref, "e939")
        self.assertEqual(state.elements[0].url, "/wiki/Manga")


if __name__ == "__main__":
    unittest.main()

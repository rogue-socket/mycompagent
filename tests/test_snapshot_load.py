import tempfile
import unittest
from pathlib import Path

from browser_agent.snapshot_parser import load_snapshot_text


class SnapshotLoadTests(unittest.TestCase):
    def test_loads_snapshot_file_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "snap.yml"
            path.write_text("URL: https://example.com\nTitle: Example\ne1: input", encoding="utf-8")
            output = f"### Snapshot\n[Snapshot]({path})"
            text, src = load_snapshot_text(output)
            self.assertIn("URL:", text)
            self.assertEqual(str(path), src)


if __name__ == "__main__":
    unittest.main()

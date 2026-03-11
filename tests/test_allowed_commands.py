import unittest

from browser_agent.constants import ALLOWED_COMMANDS


class AllowedCommandsTests(unittest.TestCase):
    def test_close_allowed(self) -> None:
        self.assertIn("close", ALLOWED_COMMANDS)
        self.assertIn("close-all", ALLOWED_COMMANDS)
        self.assertIn("kill-all", ALLOWED_COMMANDS)


if __name__ == "__main__":
    unittest.main()

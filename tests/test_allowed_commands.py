import unittest

from browser_agent.constants import ALLOWED_COMMANDS
from browser_agent.tool_definitions import TOOL_DECLARATIONS


class AllowedCommandsTests(unittest.TestCase):
    def test_legacy_registry_includes_close_commands(self) -> None:
        self.assertIn("close", ALLOWED_COMMANDS)
        self.assertIn("close-all", ALLOWED_COMMANDS)
        self.assertIn("kill-all", ALLOWED_COMMANDS)

    def test_legacy_registry_is_not_the_planner_surface(self) -> None:
        tool_names = {getattr(tool, "name", "") for tool in TOOL_DECLARATIONS}

        self.assertIn("run-code", ALLOWED_COMMANDS)
        self.assertNotIn("run-code", tool_names)


if __name__ == "__main__":
    unittest.main()

import unittest

from browser_agent.action_parser import ActionParseError, parse_tool_call


class ToolCallParserTests(unittest.TestCase):
    def test_click(self) -> None:
        action = parse_tool_call("click", {"ref": "e12"})
        self.assertEqual(action.command, "click")
        self.assertIn("e12", action.args)
        self.assertIn("click", action.action)

    def test_fill(self) -> None:
        action = parse_tool_call("fill", {"ref": "e5", "value": "hello world"})
        self.assertEqual(action.command, "fill")
        self.assertIn("e5", action.args)

    def test_goto(self) -> None:
        action = parse_tool_call("goto", {"url": "https://example.com"})
        self.assertEqual(action.command, "goto")
        self.assertIn("https://example.com", action.action)

    def test_tab_new_opens_blank_even_when_url_is_supplied(self) -> None:
        action = parse_tool_call("tab_new", {"url": "https://example.com"})
        self.assertEqual(action.command, "tab-new")
        self.assertEqual(action.args, [])
        self.assertEqual(action.action, "playwright-cli tab-new")

    def test_press(self) -> None:
        action = parse_tool_call("press", {"key": "Enter"})
        self.assertEqual(action.command, "press")

    def test_scroll(self) -> None:
        action = parse_tool_call("scroll", {"dy": "900"})
        self.assertEqual(action.command, "mousewheel")
        self.assertEqual(action.args, ["900", "0"])

    def test_hover(self) -> None:
        action = parse_tool_call("hover", {"ref": "e25"})
        self.assertEqual(action.command, "hover")

    def test_snapshot(self) -> None:
        action = parse_tool_call("snapshot", {})
        self.assertEqual(action.command, "snapshot")

    def test_finish_returns_finish_command(self) -> None:
        action = parse_tool_call("finish", {"reason": "task done"})
        self.assertEqual(action.command, "finish")
        self.assertEqual(action.action, "")

    def test_ask_human_returns_non_cli_command(self) -> None:
        action = parse_tool_call("ask_human", {"question": "What is the CAPTCHA?"})
        self.assertEqual(action.command, "ask_human")
        self.assertEqual(action.action, "")

    def test_invalid_ref_rejected(self) -> None:
        with self.assertRaises(ActionParseError):
            parse_tool_call("click", {"ref": "invalid"})

    def test_unknown_tool_passes_through(self) -> None:
        # With native function calling the LLM can only call declared tools,
        # so unknown names shouldn't occur.  The mapper passes them through
        # as-is (CLI will reject them at runtime).
        action = parse_tool_call("rm_rf", {"path": "/"})
        self.assertIn("rm_rf", action.action)

    def test_drag_validates_both_refs(self) -> None:
        action = parse_tool_call("drag", {"source_ref": "e1", "target_ref": "e2"})
        self.assertEqual(action.command, "drag")

    def test_draw_circle_maps_to_guarded_run_code(self) -> None:
        action = parse_tool_call("draw_circle", {"radius": "180", "steps": "24"})
        self.assertEqual(action.command, "run-code")
        self.assertIn("Drew circle", action.action)
        self.assertIn("requestedRadius = 180", action.action)
        self.assertIn("steps = 24", action.action)

    def test_format_selection_maps_to_exec_command(self) -> None:
        action = parse_tool_call("format_selection", {"format": "bold"})
        self.assertEqual(action.command, "run-code")
        self.assertIn("execCommand", action.action)
        self.assertIn("bold", action.action)

    def test_drag_rejects_bad_ref(self) -> None:
        with self.assertRaises(ActionParseError):
            parse_tool_call("drag", {"source_ref": "e1", "target_ref": "bad"})

    def test_preserves_tool_name_and_args(self) -> None:
        action = parse_tool_call("select", {"ref": "e3", "value": "option1"})
        self.assertEqual(action.tool_name, "select")
        self.assertEqual(action.tool_args, {"ref": "e3", "value": "option1"})


if __name__ == "__main__":
    unittest.main()

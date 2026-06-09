import json
import shlex
import tempfile
import unittest
from pathlib import Path

from browser_agent.decision_loop import DecisionLoop, RunResult
from browser_agent.logger import RunPaths, append_jsonl
from browser_agent.memory import Lesson, MemoryStore
from browser_agent.planner import ToolCallResult
from browser_agent.playwright_executor import CommandResult
from browser_agent.snapshot_parser import ElementRef


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class AriaComboboxExecutor:
    def __init__(self) -> None:
        self.menu_open = False
        self.priority_high = False
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli select e6 High":
            return CommandResult(
                command,
                1,
                "",
                "Error: locator.selectOption: Error: Element is not a <select> element",
            )
        if command == "playwright-cli click e6":
            self.menu_open = True
            return CommandResult(command, 0, "Clicked Priority combobox", "")
        if command == "playwright-cli click e11":
            self.menu_open = False
            self.priority_high = True
            return CommandResult(command, 0, "Clicked High option", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult("playwright-cli snapshot", 0, self._snapshot_text(), "")

    def _snapshot_text(self) -> str:
        if self.priority_high:
            status = "Task complete: Priority is High for Workflow B."
            control = 'combobox "Priority" : "Priority dropdown: High" [ref=e6]'
            return _workflow_snapshot([control, f'text "{status}"'])
        if self.menu_open:
            return _workflow_snapshot(
                [
                    'combobox "Priority" [expanded] [active]: "Priority dropdown: choose one" [ref=e6]',
                    'listbox "Priority options" [ref=e8]:',
                    '  - option "Normal" [ref=e9]',
                    '  - option "Urgent" [ref=e10]',
                    '  - option "High" [ref=e11]',
                ]
            )
        return _workflow_snapshot(
            ['combobox "Priority" : "Priority dropdown: choose one" [ref=e6]']
        )

    def _visible_text(self) -> str:
        if self.priority_high:
            return (
                "Workflow B Escalation Console\n"
                "Priority\n"
                "Priority dropdown: High\n"
                "Task complete: Priority is High for Workflow B."
            )
        if self.menu_open:
            return (
                "Workflow B Escalation Console\n"
                "Task: set the escalation priority to High using the Priority dropdown.\n"
                "Priority\n"
                "Priority dropdown: choose one\n"
                "Normal\nUrgent\nHigh\n"
                "Priority menu is open. Choose High."
            )
        return (
            "Workflow B Escalation Console\n"
            "Task: set the escalation priority to High using the Priority dropdown.\n"
            "Priority\n"
            "Priority dropdown: choose one"
        )


def _workflow_snapshot(lines: list[str]) -> str:
    body = "\n".join(f"  - {line}" for line in lines)
    return (
        "Page URL: http://127.0.0.1:8766/workflow-b.html\n"
        "Page Title: Workflow B - Priority Console\n"
        "Snapshot\n"
        f"{body}\n"
    )


class AriaComboboxPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            self.testcase.assertIn('e6: [action] input - combobox "Priority"', message)
            self.testcase.assertNotIn('e11: [action] option - option "High"', message)
            return _tool("select", {"ref": "e6", "value": "High"}, "Try the requested dropdown/select control.")
        if step == 2:
            self.testcase.assertIn("IMPORTANT - Last action failed:", message)
            self.testcase.assertIn("Tips from previous experience:", message)
            return _tool("click", {"ref": "e6"}, "Memory says the custom control needs a click.")
        if step == 3:
            self.testcase.assertIn('e11: [action] option - option "High"', message)
            return _tool("click", {"ref": "e11"}, "Click the visible High option ref.")
        if step == 4:
            self.testcase.assertIn("Task complete: Priority is High for Workflow B.", message)
            return _tool(
                "finish",
                {"reason": "Task complete: Priority is High for Workflow B."},
                "The completion text is visible.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class OverlayBlockingExecutor:
    def __init__(self) -> None:
        self.overlay_open = True
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli click e1":
            return CommandResult(
                command,
                1,
                "",
                (
                    "TimeoutError: locator.click: Timeout 5000ms exceeded.\n"
                    "<div id=\"headlessui-dialog-overlay\" class=\"modal overlay\"></div> "
                    "intercepts pointer events"
                ),
            )
        if command == "playwright-cli press Escape":
            self.overlay_open = False
            return CommandResult(command, 0, "Pressed Escape", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult("playwright-cli snapshot", 0, self._snapshot_text(), "")

    def _snapshot_text(self) -> str:
        lines = ['button "Book Now" [ref=e1]']
        if self.overlay_open:
            lines.extend(
                [
                    'dialog "Price chart" [ref=e2]:',
                    '  - img "cross" [ref=e3] [cursor=pointer]',
                ]
            )
        else:
            lines.append('text "Overlay dismissed; booking controls are visible."')
        return _workflow_snapshot(lines)

    def _visible_text(self) -> str:
        if self.overlay_open:
            return "Venue page\nBook Now\nPrice chart"
        return "Venue page\nBook Now\nOverlay dismissed; booking controls are visible."


class OverlayRecoveryPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            self.testcase.assertIn('e1: [action] button - button "Book Now"', message)
            return _tool("click", {"ref": "e1"}, "Click the visible booking button.")
        if step == 2:
            self.testcase.assertIn("Escape was pressed to dismiss it", message)
            self.testcase.assertIn("Overlay dismissed; booking controls are visible.", message)
            return _tool("finish", {"reason": "Overlay recovery completed."}, "Recovered from the overlay.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class PriceComparisonExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _workflow_snapshot(
                [
                    'heading "Shopping results" [level=1] [ref=e1]',
                    'generic [ref=e2]: Alpha Laptop',
                    'generic [ref=e3]: $499',
                    'generic [ref=e4]: Beta Laptop',
                    'generic [ref=e5]: $699',
                ]
            ),
            "",
        )

    def _visible_text(self) -> str:
        return "Shopping results\nAlpha Laptop\n$499\nBeta Laptop\n$699"


class PriceComparisonPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            self.testcase.assertIn("Best price evidence so far:", message)
            return _tool(
                "finish",
                {"reason": "Cheapest option is Beta Laptop for $699."},
                "Incorrectly finish with the current preferred item.",
            )
        if step == 2:
            self.testcase.assertIn("Finish rejected", message)
            return _tool(
                "finish",
                {"reason": "Cheapest checked option is Alpha Laptop for $499."},
                "Use the cheaper evidence from the ledger.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class MultiSiteSearchExecutor:
    def __init__(self) -> None:
        self.url = "https://www.google.com/search?q=compare+websites"
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command.startswith("playwright-cli goto "):
            destination = command.split(" ", 2)[2]
            self.url = destination.strip()
            return CommandResult(command, 0, f"Opened {self.url}", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(
                command,
                0,
                f"### Result\n\"Visible content from {self.url}\"",
                "",
            )
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if "google.com/search" in self.url:
            return CommandResult(
                "playwright-cli snapshot",
                0,
                (
                    "Page URL: https://www.google.com/search?q=compare+websites\n"
                    "Page Title: Compare websites — Google Search\n"
                    "Snapshot\n"
                    '  - heading "Best options from many websites"\n'
                    '  - text "Result snippets for example sites."\n'
                ),
                "",
            )
        if "site-a.example" in self.url:
            return CommandResult(
                "playwright-cli snapshot",
                0,
                (
                    "Page URL: https://site-a.example/results\n"
                    "Page Title: Site A Listings\n"
                    "Snapshot\n"
                    '  - heading "Site A offer"\n'
                ),
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            (
                "Page URL: https://site-b.example/listings\n"
                "Page Title: Site B Listings\n"
                "Snapshot\n"
                '  - heading "Site B offer"\n'
            ),
            "",
        )


class MultiSiteSearchPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            self.testcase.assertIn("Task requires inspecting multiple websites", message)
            return _tool(
                "finish",
                {"reason": "The top snippet says site A is best."},
                "Attempting to finish from search snippets.",
            )
        if step == 2:
            self.testcase.assertIn("Finish rejected", message)
            return _tool(
                "goto",
                {"url": "site-a.example/results"},
                "Visit source website A.",
            )
        if step == 3:
            return _tool(
                "goto",
                {"url": "site-b.example/listings"},
                "Visit source website B.",
            )
        if step == 4:
            return _tool(
                "finish",
                {"output": "Site A and Site B were reviewed; Site B offer is cheapest."},
                "Finish after reading source pages.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class DragFallbackExecutor:
    def __init__(self) -> None:
        self.combined = False
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli drag e100 e104":
            return CommandResult(
                command,
                1,
                "",
                (
                    '[{"path":["startElement"],"message":"Invalid input"},'
                    '{"path":["endElement"],"message":"Invalid input"}]'
                ),
            )
        if command.startswith("playwright-cli run-code "):
            self.combined = True
            return CommandResult(command, 0, "### Result\nDragged Water to Earth", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        lines = [
            "generic [ref=e100]:",
            "  - generic: 💧",
            "  - generic: Water",
            "generic [ref=e104]:",
            "  - generic: 🌍",
            "  - generic: Earth",
        ]
        if self.combined:
            lines.append('text "Created Plant."')
        return CommandResult("playwright-cli snapshot", 0, _workflow_snapshot(lines), "")

    def _visible_text(self) -> str:
        if self.combined:
            return "Infinite Craft\nWater\nEarth\nCreated Plant."
        return "Infinite Craft\nWater\nEarth"


class DragFallbackPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "drag",
                {"source_ref": "e100", "target_ref": "e104"},
                "Combine the placed elements.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "ok")
            self.testcase.assertIn("Dragged Water to Earth", self.tool_results[-1]["output"])
            return _tool("finish", {"reason": "Drag fallback completed."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class CaptchaExecutor:
    def __init__(self) -> None:
        self.password = "Aaaaa!799juneVIIpepsi"
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if shlex.split(command) == ["playwright-cli", "fill", "e15", "Aaaaa!799juneVIIpepsiABCD"]:
            self.password = "Aaaaa!799juneVIIpepsiABCD"
            return CommandResult(command, 0, "Filled password", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.password.endswith("ABCD"):
            return CommandResult(
                "playwright-cli snapshot",
                0,
                _workflow_snapshot(
                    [
                        f'textbox "Please choose a password" : "{self.password}" [ref=e15]',
                        'text "Rule 10 passed"',
                    ]
                ),
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _workflow_snapshot(
                [
                    f'textbox "Please choose a password" : "{self.password}" [ref=e15]',
                    'text "Rule 10"',
                    'text "Your password must include this CAPTCHA"',
                ]
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.password.endswith("ABCD"):
            return "The Password Game\nPlease choose a password\nRule 10 passed"
        return (
            "The Password Game\nPlease choose a password\nRule 10\n"
            "Your password must include this CAPTCHA"
        )


class CaptchaPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "ask_human",
                {
                    "question": "What CAPTCHA text is shown?",
                    "reason": "Rule 10 requires visual CAPTCHA text not present in the DOM.",
                },
                "Ask the operator for the visual CAPTCHA.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["tool_name"], "ask_human")
            self.testcase.assertEqual(self.tool_results[-1]["answer"], "ABCD")
            return _tool(
                "fill",
                {"ref": "e15", "value": "Aaaaa!799juneVIIpepsiABCD"},
                "Use the human-provided CAPTCHA while preserving previous rules.",
            )
        if step == 3:
            self.testcase.assertIn("Rule 10 passed", message)
            return _tool("finish", {"reason": "CAPTCHA rule passed."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class PasswordElementsExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, "### Result\n\"Rule 18 active\"", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _workflow_snapshot(
                [
                    'textbox "Please choose a password" : "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚" [ref=e15]',
                    "text \"Rule 18\"",
                    'text "The elements in your password must have atomic numbers that add up to 200."',
                ]
            ),
            "",
        )


class PasswordElementsPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, object]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        password = "Aaaaa37!maypepsiV-VII4dgf7wharfHe🌘KenyaNf4+🥚"
        if step == 1:
            return _tool(
                "password_game_elements",
                {"password": password},
                "Compute the Rule 18 element sum before editing.",
            )
        if step == 2:
            self.testcase.assertEqual(
                self.tool_results[-1]["tool_name"], "password_game_elements"
            )
            self.testcase.assertEqual(self.tool_results[-1]["current_sum"], 180)
            self.testcase.assertEqual(self.tool_results[-1]["suggested_suffix"], "HK")
            return _tool("finish", {"reason": "Helper returned HK."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, object]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


def _tool(tool_name: str, args: dict[str, str], reasoning: str) -> ToolCallResult:
    return ToolCallResult(
        tool_name=tool_name,
        tool_args=args,
        latency_seconds=0.01,
        attempts=1,
        rate_limited=False,
        reasoning_text=reasoning,
    )


class DecisionLoopMetadataTests(unittest.TestCase):
    def test_planner_log_payload_includes_metrics_and_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths(Path(tmp))
            loop = DecisionLoop(
                task="Test task",
                mode="auto",
                planner=object(),
                config={},
                paths=paths,
                executor=object(),
                open_url=None,
                open_args=[],
                debug=True,
            )
            loop.step = 3
            result = ToolCallResult(
                tool_name="click",
                tool_args={"ref": "e1"},
                latency_seconds=1.25,
                attempts=2,
                rate_limited=False,
                reasoning_text="Click the relevant link.",
                raw_response='{"tool_name": "click"}',
                prompt_text="full planner prompt",
            )

            payload = loop._planner_log_payload("current page prompt", tool_result=result)

            self.assertEqual(payload["prompt_chars"], len("current page prompt"))
            self.assertEqual(payload["planner_latency_seconds"], 1.25)
            self.assertEqual(payload["planner_attempts"], 2)
            self.assertEqual(payload["debug_artifacts"]["enabled"], True)
            self.assertEqual(payload["debug_artifacts"]["debug_log"], str(paths.debug_log))
            self.assertEqual(payload["debug_artifacts"]["traces_dir"], str(paths.traces))
            self.assertEqual(payload["debug_artifacts"]["video"], str(paths.root / "session.webm"))
            self.assertEqual(payload["planner_prompt"], "full planner prompt")
            self.assertEqual(payload["raw_model_response"], '{"tool_name": "click"}')

    def test_planner_error_payload_marks_non_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths(Path(tmp))
            loop = DecisionLoop(
                task="Test task",
                mode="auto",
                planner=object(),
                config={},
                paths=paths,
                executor=object(),
                open_url=None,
                open_args=[],
                debug=False,
            )
            loop.step = 1

            payload = loop._planner_log_payload(
                "prompt",
                error="401 unauthenticated",
                non_retryable=True,
            )

            self.assertEqual(payload["error"], "401 unauthenticated")
            self.assertEqual(payload["tool_name"], "")
            self.assertTrue(payload["non_retryable"])

    def test_click_route_entry_uses_element_label_and_href(self) -> None:
        element = ElementRef(
            ref="e42",
            description='link "Japanese cuisine"',
            url="/wiki/Japanese_cuisine",
        )

        entry = DecisionLoop._click_route_entry(element)

        self.assertEqual(entry["ref"], "e42")
        self.assertEqual(entry["label"], "Japanese cuisine")
        self.assertEqual(entry["href"], "/wiki/Japanese_cuisine")

    def test_run_result_exposes_finish_output_steps_and_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths(Path(tmp))
            loop = DecisionLoop(
                task="Test task",
                mode="auto",
                planner=object(),
                config={},
                paths=paths,
                executor=object(),
                open_url=None,
                open_args=[],
                debug=False,
            )
            loop.stop_reason = "completed"
            loop.finish_output = "Found the target page."
            loop.step = 4
            loop.clicked_route = [{"ref": "e1", "label": "Manga", "description": 'link "Manga"', "href": "/wiki/Manga"}]

            result = loop._run_result()

            self.assertIsInstance(result, RunResult)
            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(result.finish_output, "Found the target page.")
            self.assertEqual(result.steps_used, 4)
            self.assertEqual(result.grounded_route[0]["label"], "Manga")

    def test_reasoning_log_payload_records_tool_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths(Path(tmp))
            loop = DecisionLoop(
                task="Test task",
                mode="auto",
                planner=object(),
                config={},
                paths=paths,
                executor=object(),
                open_url=None,
                open_args=[],
                debug=False,
            )
            loop.step = 2
            result = ToolCallResult(
                tool_name="click",
                tool_args={"ref": "e1"},
                latency_seconds=0.2,
                attempts=1,
                rate_limited=False,
                reasoning_text="The target link is visible.",
            )

            payload = loop._reasoning_log_payload(result)

            self.assertEqual(payload["step"], 2)
            self.assertEqual(payload["tool_name"], "click")
            self.assertEqual(payload["tool_args"], {"ref": "e1"})
            self.assertEqual(payload["reasoning"], "The target link is visible.")
            self.assertEqual(payload["planner_latency_seconds"], 0.2)
            self.assertEqual(payload["planner_attempts"], 1)

    def test_debug_log_records_planner_prompt_response_and_selected_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths(Path(tmp))
            loop = DecisionLoop(
                task="Test task",
                mode="auto",
                planner=object(),
                config={},
                paths=paths,
                executor=object(),
                open_url=None,
                open_args=[],
                debug=True,
            )
            loop.step = 5
            result = ToolCallResult(
                tool_name="click",
                tool_args={"ref": "e50"},
                latency_seconds=0.3,
                attempts=1,
                rate_limited=False,
                reasoning_text="Click the visible result card.",
                raw_response='{"reasoning": "Click the visible result card.", "tool_name": "click"}',
                prompt_text="full prompt with card refs",
            )

            loop._log_planner_debug_io(
                "page message",
                tool_result=result,
                planner_state={
                    "selected_clickables": [{"ref": "e50", "type": "card"}],
                    "prioritized_card_refs": ["e50"],
                    "evidence_snippets": ["Padel Arena HSR Layout"],
                    "cursor_pointer_refs_excluded": [{"ref": "e99"}],
                },
            )

            payload = _read_jsonl(paths.debug_log)[0]
            self.assertEqual(payload["event"], "planner-io")
            self.assertEqual(payload["planner_prompt"], "full prompt with card refs")
            self.assertIn("Click the visible result card", payload["raw_model_response"])
            self.assertEqual(payload["selected_clickables"][0]["ref"], "e50")
            self.assertEqual(payload["prioritized_card_refs"], ["e50"])
            self.assertEqual(payload["cursor_pointer_refs_excluded"][0]["ref"], "e99")

    def test_click_blocked_by_overlay_presses_escape_before_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = OverlayRecoveryPlanner(self)
            executor = OverlayBlockingExecutor()
            loop = DecisionLoop(
                task="Recover when a modal blocks a click.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/modal.html",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(loop.errors, 0)
            self.assertEqual(
                [command for command in executor.commands if not command.startswith("playwright-cli eval")],
                [
                    "playwright-cli open http://127.0.0.1:8766/modal.html",
                    "playwright-cli click e1",
                    "playwright-cli press Escape",
                ],
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "error")
            self.assertEqual(actions[0]["recovery"]["command"], "playwright-cli press Escape")
            self.assertEqual(actions[1]["approval_status"], "auto_recovery")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_drag_schema_error_retries_with_mouse_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = DragFallbackPlanner(self)
            executor = DragFallbackExecutor()
            loop = DecisionLoop(
                task="Combine two Infinite Craft cards.",
                mode="auto",
                planner=planner,
                config={"max_steps": 3, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/infinite-craft.html",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue(any(command.startswith("playwright-cli run-code ") for command in executor.commands))
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "playwright-cli drag e100 e104")
            self.assertEqual(actions[0]["execution_result"], "ok")
            self.assertEqual(actions[0]["recovery"]["kind"], "drag_mouse_fallback")
            self.assertTrue(actions[1]["command"].startswith("playwright-cli run-code "))
            self.assertEqual(actions[-1]["command"], "finish")

    def test_ask_human_result_can_drive_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = CaptchaPlanner(self)
            executor = CaptchaExecutor()
            loop = DecisionLoop(
                task="Pass the CAPTCHA rule.",
                mode="auto",
                planner=planner,
                config={
                    "max_steps": 4,
                    "max_errors": 1,
                    "min_visible_text": 0,
                    "allow_human_input": True,
                },
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/password-game.html",
                open_args=[],
                debug=False,
                human_input=lambda prompt: "ABCD",
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(
                [
                    shlex.split(command)
                    for command in executor.commands
                    if not command.startswith("playwright-cli eval")
                ],
                [
                    ["playwright-cli", "open", "http://127.0.0.1:8766/password-game.html"],
                    ["playwright-cli", "fill", "e15", "Aaaaa!799juneVIIpepsiABCD"],
                ],
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "ask_human")
            self.assertEqual(actions[0]["execution_result"], "ok")
            self.assertTrue(actions[0]["response_provided"])
            self.assertNotIn("ABCD", json.dumps(actions[0]))
            self.assertEqual(
                shlex.split(actions[1]["command"]),
                ["playwright-cli", "fill", "e15", "Aaaaa!799juneVIIpepsiABCD"],
            )
            self.assertEqual(actions[-1]["command"], "finish")

    def test_password_game_elements_returns_planner_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = PasswordElementsPlanner(self)
            executor = PasswordElementsExecutor()
            loop = DecisionLoop(
                task="Use the Password Game element helper.",
                mode="auto",
                planner=planner,
                config={"max_steps": 3, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/password-game.html",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "password_game_elements")
            self.assertEqual(actions[0]["current_sum"], 180)
            self.assertEqual(actions[0]["suggested_suffix"], "HK")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_finish_validation_rejects_higher_price_than_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = PriceComparisonPlanner(self)
            executor = PriceComparisonExecutor()
            loop = DecisionLoop(
                task="Find the cheapest laptop in these shopping results.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/shop.html",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(result.finish_output, "Cheapest checked option is Alpha Laptop for $499.")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "rejected")
            self.assertEqual(actions[0]["command"], "finish")
            self.assertIn("higher price", actions[0]["validation_error"])
            self.assertEqual(actions[1]["execution_result"], "completed")

    def test_multi_site_task_finishing_from_google_snippets_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = MultiSiteSearchPlanner(self)
            executor = MultiSiteSearchExecutor()
            loop = DecisionLoop(
                task="Compare service plans across different websites.",
                mode="auto",
                planner=planner,
                config={"max_steps": 6, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://www.google.com/search?q=compare+websites",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "rejected")
            self.assertEqual(actions[0]["command"], "finish")
            self.assertIn("search pages", actions[0]["validation_error"])
            self.assertEqual(actions[-1]["execution_result"], "completed")
            self.assertEqual(actions[-1]["command"], "finish")


class DecisionLoopMemoryRegressionTests(unittest.TestCase):
    def test_aria_combobox_recovery_asserts_memory_recall_and_full_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            memory = MemoryStore(
                path=root / "memory.json",
                on_event=lambda evt: append_jsonl(paths.memory_events_log, evt),
            )
            memory.load()
            memory.record_lesson(
                Lesson(
                    lesson=(
                        "When select fails with 'Element is not a <select>', click "
                        "the custom combobox, then click the matching visible option."
                    ),
                    category="error_recovery",
                    failed_command="select",
                    error_pattern="element is not a <select>",
                )
            )
            planner = AriaComboboxPlanner(self)
            executor = AriaComboboxExecutor()
            loop = DecisionLoop(
                task=(
                    "Use the Priority dropdown/select control to set the escalation "
                    "priority to High. Finish only when the page says the task is complete."
                ),
                mode="auto",
                planner=planner,
                config={"max_steps": 8, "max_errors": 3, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/workflow-b.html",
                open_args=[],
                debug=False,
                memory=memory,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(result.steps_used, 4)
            self.assertEqual(
                [command for command in executor.commands if not command.startswith("playwright-cli eval")],
                [
                    "playwright-cli open http://127.0.0.1:8766/workflow-b.html",
                    "playwright-cli select e6 High",
                    "playwright-cli click e6",
                    "playwright-cli click e11",
                ],
            )
            self.assertEqual(
                [item["ref"] for item in result.grounded_route],
                ["e6", "e11"],
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(
                [(item["command"], item["execution_result"]) for item in actions],
                [
                    ("playwright-cli select e6 High", "error"),
                    ("playwright-cli click e6", "ok"),
                    ("playwright-cli click e11", "ok"),
                    ("finish", "completed"),
                ],
            )
            memory_events = _read_jsonl(paths.memory_events_log)
            recalls = [event for event in memory_events if event["event"] == "error_recall"]
            self.assertEqual(recalls[-1]["command"], "select")
            self.assertEqual(recalls[-1]["matched"], 1)
            interpreter_states = _read_jsonl(paths.interpreter_state_log)
            opened_menu = interpreter_states[2]["clickable_elements"]
            self.assertIn(
                {"id": "e11", "type": "option", "text": 'option "High"', "href": "", "area": "action"},
                opened_menu,
            )


if __name__ == "__main__":
    unittest.main()

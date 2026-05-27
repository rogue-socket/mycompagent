import json
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
            )

            payload = loop._planner_log_payload("current page prompt", tool_result=result)

            self.assertEqual(payload["prompt_chars"], len("current page prompt"))
            self.assertEqual(payload["planner_latency_seconds"], 1.25)
            self.assertEqual(payload["planner_attempts"], 2)
            self.assertEqual(payload["debug_artifacts"]["enabled"], True)
            self.assertEqual(payload["debug_artifacts"]["debug_log"], str(paths.debug_log))
            self.assertEqual(payload["debug_artifacts"]["traces_dir"], str(paths.traces))
            self.assertEqual(payload["debug_artifacts"]["video"], str(paths.root / "session.webm"))

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

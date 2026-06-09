import json
import shlex
import tempfile
import unittest
from pathlib import Path

from browser_agent.decision_loop import (
    DecisionLoop,
    RunResult,
    _html_to_readable_text,
    _looks_html_text,
)
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
    return _snapshot_for_url(
        "http://127.0.0.1:8766/workflow-b.html",
        "Workflow B - Priority Console",
        lines,
    )


def _snapshot_for_url(url: str, title: str, lines: list[str]) -> str:
    body = "\n".join(f"  - {line}" for line in lines)
    return (
        f"Page URL: {url}\n"
        f"Page Title: {title}\n"
        "Snapshot\n"
        f"{body}\n"
    )


class TransientInterstitialExecutor:
    supports_dom_evidence = True

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.snapshot_count = 0

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command.startswith("playwright-cli run-code "):
            return CommandResult(command, 0, "### Result\n[]", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        self.snapshot_count += 1
        if self.snapshot_count <= 2:
            return CommandResult(
                "playwright-cli snapshot",
                0,
                _snapshot_for_url(
                    "https://example.com/task",
                    "Just a moment...",
                    ['text "Checking your browser before accessing the site."'],
                ),
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                ['button "Done" [ref=e1]', 'text "Ready to continue."'],
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.snapshot_count <= 2:
            return "Just a moment...\nChecking your browser before accessing the site."
        return "Ready to continue."


class InterstitialWaitPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        self.testcase.assertNotIn("Just a moment", message)
        self.testcase.assertIn("Ready to continue", message)
        return _tool(
            "finish",
            {"reason": "Ready page reached."},
            "The transient interstitial cleared.",
        )

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        pass


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


class VisualCodeExecutor:
    def __init__(self) -> None:
        self.value = "initial"
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if shlex.split(command) == ["playwright-cli", "fill", "e15", "initialABCD"]:
            self.value = "initialABCD"
            return CommandResult(command, 0, "Filled value", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.value.endswith("ABCD"):
            return CommandResult(
                "playwright-cli snapshot",
                0,
                _workflow_snapshot(
                    [
                        f'textbox "Enter code" : "{self.value}" [ref=e15]',
                        'text "Visual challenge passed"',
                    ]
                ),
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _workflow_snapshot(
                [
                    f'textbox "Enter code" : "{self.value}" [ref=e15]',
                    'text "Visual challenge"',
                    'text "Enter the code shown in the image"',
                ]
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.value.endswith("ABCD"):
            return "Visual challenge passed"
        return "Visual challenge\nEnter the code shown in the image"


class SourceTokenVisualExecutor(VisualCodeExecutor):
    supports_dom_evidence = True

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command.startswith("playwright-cli run-code "):
            items = [
                {
                    "kind": "image",
                    "src": "https://assets.example/visual/a1b2c.png",
                    "src_token": "a1b2c",
                    "nearby": "Enter the code shown in the image",
                }
            ]
            return CommandResult(command, 0, f"### Result\n{json.dumps(items)}", "")
        if shlex.split(command) == ["playwright-cli", "fill", "e15", "initiala1b2c"]:
            self.value = "initiala1b2c"
            return CommandResult(command, 0, "Filled value", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.value.endswith("a1b2c"):
            return CommandResult(
                "playwright-cli snapshot",
                0,
                _workflow_snapshot(
                    [
                        f'textbox "Enter code" : "{self.value}" [ref=e15]',
                        'text "Visual challenge passed"',
                    ]
                ),
                "",
            )
        return super().snapshot()

    def _visible_text(self) -> str:
        if self.value.endswith("a1b2c"):
            return "Visual challenge passed"
        return super()._visible_text()


class VisualCodePlanner:
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
                    "question": "What short visual code is shown?",
                    "reason": "The required short visual code is not present in the DOM.",
                },
                "Ask the operator for the visual code.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["tool_name"], "ask_human")
            self.testcase.assertEqual(self.tool_results[-1]["answer"], "ABCD")
            return _tool(
                "fill",
                {"ref": "e15", "value": "initialABCD"},
                "Use the human-provided code.",
            )
        if step == 3:
            self.testcase.assertIn("Visual challenge passed", message)
            return _tool("finish", {"reason": "Visual challenge passed."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class SourceTokenVisualPlanner:
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
                    "question": "What short visual code is shown?",
                    "reason": "The required short visual code is not present in visible text.",
                },
                "Ask the operator for the visual code.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["tool_name"], "ask_human")
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            self.testcase.assertIn("short source token", self.tool_results[-1]["error"])
            self.testcase.assertIn("a1b2c", self.tool_results[-1]["error"])
            return _tool(
                "fill",
                {"ref": "e15", "value": "initiala1b2c"},
                "Use the page-evidence source token before asking the operator.",
            )
        if step == 3:
            self.testcase.assertIn("Visual challenge passed", message)
            return _tool("finish", {"reason": "Visual challenge passed."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class HumanInputUnavailablePlanner:
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
                    "question": "What short visual code is shown?",
                    "reason": "The required short visual code is not present in the DOM.",
                },
                "Ask the operator for the visual code.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["tool_name"], "ask_human")
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            self.testcase.assertIn("stdin is not available", self.tool_results[-1]["error"])
            self.testcase.assertIn("Human input was requested", message)
            return _tool(
                "finish",
                {"reason": "Unavailable human input was handled without crashing."},
                "The loop surfaced the missing input as an error.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class SnapshotTimeoutRecoveryExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.bad_navigation = False

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps('Ready')}", "")
        if command == "playwright-cli goto https://example.com/detail-page":
            self.bad_navigation = True
            return CommandResult(
                command,
                1,
                "",
                "TimeoutError: page._snapshotForAI: Timeout 5000ms exceeded.",
            )
        if command == "playwright-cli go-back":
            self.bad_navigation = False
            return CommandResult(command, 0, "Went back", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.bad_navigation:
            return CommandResult(
                "playwright-cli snapshot",
                1,
                "",
                "TimeoutError: page._snapshotForAI: Timeout 5000ms exceeded.",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _workflow_snapshot(['textbox "Value" : "Ready" [ref=e15]']),
            "",
        )


class SnapshotTimeoutRecoveryPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict[str, str]] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "goto",
                {"url": "https://example.com/detail-page"},
                "Try inspecting the detail page directly.",
            )
        if step == 2:
            self.testcase.assertIn("went back once", message)
            self.testcase.assertIn("avoid repeating the same failed navigation", message)
            return _tool(
                "finish",
                {"reason": "Recovered to the task page."},
                "The loop recovered from the bad navigation.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class StatefulTaskNavigationGuardExecutor:
    def __init__(self) -> None:
        self.current_tab = 0
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            self.current_tab = 0
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli tab-new":
            self.current_tab = 1
            return CommandResult(command, 0, "### Result\n- 0: [Task](https://example.com/task)\n- 1: (current) [](about:blank)", "")
        if command == "playwright-cli goto https://lookup.example/data":
            self.current_tab = 1
            return CommandResult(command, 0, "Loaded lookup data", "")
        if command == "playwright-cli tab-select 0":
            self.current_tab = 0
            return CommandResult(command, 0, "### Result\n- 0: (current) [Task](https://example.com/task)\n- 1: [](https://lookup.example/data)", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.current_tab == 1:
            return CommandResult(
                "playwright-cli snapshot",
                0,
                _snapshot_for_url(
                    "https://lookup.example/data",
                    "Lookup",
                    ['text "Lookup value: alpha"'],
                ),
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                ['textbox "Answer" : "in-progress" [ref=e15]'],
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.current_tab == 1:
            return "Lookup value: alpha"
        return "Task form\nAnswer\nin-progress"


class StatefulTaskNavigationGuardPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool("tab_new", {}, "Open a lookup tab.")
        if step == 2:
            return _tool("goto", {"url": "https://lookup.example/data"}, "Load lookup data.")
        if step == 3:
            return _tool("tab_select", {"index": "0"}, "Return to the task form.")
        if step == 4:
            return _tool(
                "goto",
                {"url": "https://example.com/task/asset.svg"},
                "Incorrectly try a same-site lookup on the task tab.",
            )
        if step == 5:
            self.testcase.assertIn("Stateful task-tab navigation guard", message)
            self.testcase.assertIn("Switch to an existing lookup tab", message)
            return _tool(
                "finish",
                {"reason": "The risky navigation was rejected."},
                "Guard behavior verified.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        pass


class CurrentTabNoopExecutor:
    def __init__(self) -> None:
        self.current_tab = 1
        self.loaded_lookup = False
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli goto https://lookup.example/data":
            self.loaded_lookup = True
            return CommandResult(command, 0, "Loaded lookup data", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        lines = [
            "### Open tabs",
            "- 0: [Task](https://example.com/task)",
            "- 1: (current) [](about:blank)",
        ]
        if self.loaded_lookup:
            lines[2] = "- 1: (current) [Lookup](https://lookup.example/data)"
            lines.append('  - text "Lookup loaded"')
        return CommandResult(
            "playwright-cli snapshot",
            0,
            "\n".join(lines) + "\n",
            "",
        )

    def _visible_text(self) -> str:
        if self.loaded_lookup:
            return "Lookup loaded"
        return ""


class CurrentTabNoopPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "tab_select",
                {"index": "1"},
                "Mistakenly re-select the already current lookup tab.",
            )
        if step == 2:
            self.testcase.assertIn("already the current tab", message)
            self.testcase.assertIn("Selecting it again does not change page state", message)
            return _tool(
                "goto",
                {"url": "https://lookup.example/data"},
                "Use a state-changing lookup action.",
            )
        if step == 3:
            return _tool("finish", {"reason": "Lookup tab loaded."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        pass


class StaleRefGuardExecutor:
    def __init__(self) -> None:
        self.current_tab = 1
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps(self._visible_text())}", "")
        if command == "playwright-cli tab-select 0":
            self.current_tab = 0
            return CommandResult(
                command,
                0,
                "### Result\n- 0: (current) [Task](https://example.com/task)\n"
                "- 1: [Lookup](https://lookup.example/data)",
                "",
            )
        if command == "playwright-cli fill e15 baseX":
            return CommandResult(command, 0, "Filled", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        if self.current_tab == 1:
            return CommandResult(
                "playwright-cli snapshot",
                0,
                "\n".join(
                    [
                        "### Open tabs",
                        "- 0: [Task](https://example.com/task)",
                        "- 1: (current) [Lookup](https://lookup.example/data)",
                        "Page URL: https://lookup.example/data",
                        "Page Title: Lookup",
                        "Snapshot",
                        '  - text "Lookup value: baseX"',
                    ]
                )
                + "\n",
                "",
            )
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                ['textbox "Value" : "base" [ref=e15]'],
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.current_tab == 1:
            return "Lookup value: baseX"
        return "Task value base"


class StaleRefGuardPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseX"},
                "Mistakenly use a task-page ref while the lookup tab is current.",
            )
        if step == 2:
            self.testcase.assertIn("Stale element-ref guard", message)
            self.testcase.assertIn("not present in the current page snapshot", message)
            return _tool(
                "tab_select",
                {"index": "0"},
                "Return to the task page before using task refs.",
            )
        if step == 3:
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseX"},
                "Use the fresh task-page ref.",
            )
        if step == 4:
            return _tool("finish", {"reason": "Stale ref was corrected."}, "Done.")
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict[str, str]) -> None:
        pass


class FetchUrlExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(command, 0, f"### Result\n{json.dumps('Asset task')}", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                [
                    'textbox "Answer" : "in-progress" [ref=e15]',
                    'iframe title="visual clue" src="https://assets.example/data.svg"',
                ],
            ),
            "",
        )


class FetchUrlPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            self.testcase.assertIn("https://assets.example/data.svg", message)
            return _tool(
                "fetch_url",
                {"url": "https://assets.example/data.svg", "max_chars": "4000"},
                "Read the visible text asset without navigating away.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "ok")
            self.testcase.assertIn("A B C", self.tool_results[-1]["text"])
            return _tool(
                "finish",
                {"reason": "Fetched the visible asset evidence."},
                "The fetch returned enough evidence.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class TextAssetNavigationGuardPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "goto",
                {"url": "https://assets.example/data.svg"},
                "Try to inspect the visible text-like asset in the current tab.",
            )
        if step == 2:
            self.testcase.assertIn("Text-asset navigation guard", message)
            self.testcase.assertIn("Use fetch_url for the asset", message)
            return _tool(
                "fetch_url",
                {"url": "https://assets.example/data.svg", "max_chars": "4000"},
                "Fetch the text-like asset without navigating the task tab.",
            )
        if step == 3:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "ok")
            return _tool(
                "finish",
                {"reason": "Fetched the visible asset evidence."},
                "The fetch returned enough evidence.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class RejectFetchSchemePlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "file:///tmp/secret.txt"},
                "Try an unsupported local URL.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            self.testcase.assertIn("HTTP/HTTPS", self.tool_results[-1]["error"])
            return _tool(
                "finish",
                {"reason": "Unsupported fetch scheme was rejected."},
                "The fetch guard rejected the unsupported scheme.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class FailedUrlRetryPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "https://offline.example/api/one", "max_chars": "12000"},
                "Try the public source.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            return _tool(
                "fetch_url",
                {"url": "https://offline.example/api/two", "max_chars": "12000"},
                "Try the same host with another endpoint.",
            )
        if step == 3:
            self.testcase.assertIn("Recent URL failure guard", message)
            return _tool(
                "goto",
                {"url": "https://offline.example/search"},
                "Try browser navigation to the same failed host.",
            )
        if step == 4:
            self.testcase.assertIn("unusable host", message)
            return _tool(
                "finish",
                {"reason": "Failed URL retry guard was verified."},
                "The loop rejected repeated failed-source attempts.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class ClientErrorHostRetryPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "https://api.example/data?shape=one", "max_chars": "12000"},
                "Try the first public endpoint shape.",
            )
        if step == 2:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            return _tool(
                "fetch_url",
                {"url": "https://api.example/data?shape=two", "max_chars": "12000"},
                "Try a second endpoint shape on the same source.",
            )
        if step == 3:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "error")
            return _tool(
                "fetch_url",
                {"url": "https://api.example/data?shape=three", "max_chars": "12000"},
                "Try another parameter variant on the same source.",
            )
        if step == 4:
            self.testcase.assertIn("Recent URL failure guard", message)
            self.testcase.assertIn("unusable host", message)
            return _tool(
                "finish",
                {"reason": "Client-error host retry guard was verified."},
                "The loop rejected repeated malformed-source attempts.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class SpeculativeVariantExecutor:
    supports_dom_evidence = True

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.value = "base"

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            text = (
                f"Current value\n{self.value}\n"
                "Requirement: include the exact public value."
            )
            return CommandResult(command, 0, f"### Result\n{json.dumps(text)}", "")
        if command.startswith("playwright-cli run-code "):
            items = [
                {
                    "kind": "active_editable",
                    "tag": "DIV",
                    "role": "textbox",
                    "text": self.value,
                    "html": f"<p>{self.value}</p>",
                },
                {
                    "kind": "image",
                    "src": "https://example.com/error.svg",
                    "nearby": "Requirement: include the exact public value.",
                },
            ]
            return CommandResult(command, 0, f"### Result\n{json.dumps(items)}", "")
        if command == "playwright-cli fill e15 baseX":
            self.value = "baseX"
            return CommandResult(command, 0, "Filled", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                [
                    f'textbox "Value" : "{self.value}" [ref=e15]',
                    'generic "Requirement: include the exact public value."',
                ],
            ),
            "",
        )


class SpeculativeVariantPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "https://data.example/missing", "max_chars": "12000"},
                "Try a public evidence source first.",
            )
        if step == 2:
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseX"},
                "The lookup failed, so I will try the most likely candidate suffix.",
            )
        if step == 3:
            self.testcase.assertIn("Speculative variant edit guard", message)
            self.testcase.assertIn("Gather stronger evidence", message)
            return _tool(
                "finish",
                {"reason": "Speculative edit was blocked."},
                "Guard behavior verified.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        pass


class SpeculativeBulkTypePlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "https://data.example/missing", "max_chars": "12000"},
                "Try a public evidence source first.",
            )
        if step == 2:
            return _tool(
                "type",
                {"text": "ABCDEF"},
                "The lookup failed, so I will add all possible candidate tokens.",
            )
        if step == 3:
            self.testcase.assertIn("Speculative variant edit guard", message)
            self.testcase.assertIn("bulk-insert possible answers", message)
            return _tool(
                "finish",
                {"reason": "Speculative bulk type was blocked."},
                "Guard behavior verified.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        pass


class EvidenceRecoveredVariantPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []
        self.tool_results: list[dict] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fetch_url",
                {"url": "https://data.example/missing", "max_chars": "12000"},
                "Try a public evidence source first.",
            )
        if step == 2:
            return _tool(
                "fetch_url",
                {"url": "https://data.example/value", "max_chars": "12000"},
                "Use a different public evidence source.",
            )
        if step == 3:
            self.testcase.assertEqual(self.tool_results[-1]["status"], "ok")
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseX"},
                "The latest lookup found likely evidence, so append the short value.",
            )
        if step == 4:
            return _tool(
                "finish",
                {"reason": "Evidence-backed fill was allowed."},
                "Guard behavior verified.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        self.tool_results.append({"tool_name": tool_name, **result})


class StatusRegressionExecutor:
    supports_dom_evidence = True

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.value = "base"

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        self.commands.append(command)
        if command.startswith("playwright-cli open"):
            return CommandResult(command, 0, "", "")
        if command == 'playwright-cli eval "document.body.innerText"':
            return CommandResult(
                command,
                0,
                f"### Result\n{json.dumps(self._visible_text())}",
                "",
            )
        if command.startswith("playwright-cli run-code "):
            return CommandResult(
                command,
                0,
                f"### Result\n{json.dumps(self._dom_items())}",
                "",
            )
        if command == "playwright-cli fill e15 baseX":
            self.value = "baseX"
            return CommandResult(command, 0, "Filled", "")
        if command == "playwright-cli fill e15 baseY":
            self.value = "baseY"
            return CommandResult(command, 0, "Filled", "")
        return CommandResult(command, 1, "", f"Unexpected command: {command}")

    def snapshot(self) -> CommandResult:
        return CommandResult(
            "playwright-cli snapshot",
            0,
            _snapshot_for_url(
                "https://example.com/task",
                "Task",
                [
                    f'textbox "Value" : "{self.value}" [ref=e15]',
                    'text "Requirement A has a stable token."',
                    'text "Requirement B has the new token."',
                ],
            ),
            "",
        )

    def _visible_text(self) -> str:
        if self.value == "baseY":
            return (
                "Requirement A has a stable token.\n"
                "Requirement B has the new token.\n"
                "All requirements accepted."
            )
        return "Requirement A has a stable token.\nRequirement B has the new token."

    def _dom_items(self) -> list[dict[str, str]]:
        if self.value == "baseX":
            statuses = [
                ("error", "Requirement A has a stable token."),
                ("success", "Requirement B has the new token."),
            ]
        elif self.value == "baseY":
            statuses = [
                ("success", "Requirement A has a stable token."),
                ("success", "Requirement B has the new token."),
            ]
        else:
            statuses = [
                ("success", "Requirement A has a stable token."),
                ("error", "Requirement B has the new token."),
            ]
        items = [
            {
                "kind": "active_editable",
                "tag": "DIV",
                "role": "textbox",
                "text": self.value,
                "html": f"<p>{self.value}</p>",
            }
        ]
        items.extend(
            {
                "kind": "status",
                "status": status,
                "nearby": label,
            }
            for status, label in statuses
        )
        return items


class StatusRegressionPlanner:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.messages: list[str] = []

    def plan(self, message: str, max_retries: int = 4) -> ToolCallResult:
        self.messages.append(message)
        step = len(self.messages)
        if step == 1:
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseX"},
                "Add the token that satisfies the currently failing requirement.",
            )
        if step == 2:
            self.testcase.assertIn("Status regression guard", message)
            self.testcase.assertIn("previously satisfied but are now failing", message)
            self.testcase.assertIn("Requirement A has a stable token.", message)
            self.testcase.assertIn(
                "Observed change after last successful action:",
                message,
            )
            self.testcase.assertIn(
                "Editable value changed from 'base' to 'baseX'.",
                message,
            )
            self.testcase.assertIn(
                "Newly satisfied statuses: Requirement B has the new token.",
                message,
            )
            self.testcase.assertIn(
                "Regressed statuses: Requirement A has a stable token.",
                message,
            )
            return _tool(
                "fill",
                {"ref": "e15", "value": "baseY"},
                "Revise the value to satisfy the new requirement and preserve prior statuses.",
            )
        if step == 3:
            self.testcase.assertIn("All requirements accepted.", message)
            self.testcase.assertIn(
                "Editable value changed from 'baseX' to 'baseY'.",
                message,
            )
            self.testcase.assertIn(
                "Newly satisfied statuses: Requirement A has a stable token.",
                message,
            )
            return _tool(
                "finish",
                {"reason": "All requirements accepted."},
                "All statuses are successful.",
            )
        raise AssertionError(f"Unexpected planner step {step}")

    def send_tool_result(self, tool_name: str, result: dict) -> None:
        pass


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
    def test_transient_interstitial_waits_without_planner_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = InterstitialWaitPlanner(self)
            executor = TransientInterstitialExecutor()
            loop = DecisionLoop(
                task="Complete the task once the page is ready.",
                mode="auto",
                planner=planner,
                config={
                    "max_steps": 5,
                    "max_errors": 1,
                    "min_visible_text": 0,
                    "interstitial_wait_seconds": 0,
                },
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(len(planner.messages), 1)
            self.assertNotIn("playwright-cli reload", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "auto-wait interstitial")
            self.assertEqual(actions[0]["reason"], "transient_interstitial")
            self.assertEqual(actions[1]["command"], "auto-wait interstitial")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_recent_failed_url_host_retries_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = FailedUrlRetryPlanner(self)
            executor = FetchUrlExecutor()
            loop = DecisionLoop(
                task="Gather public evidence from a working source.",
                mode="auto",
                planner=planner,
                config={"max_steps": 5, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=lambda url, max_chars: {
                    "status": "error",
                    "url": url,
                    "error": "nodename nor servname provided",
                },
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli goto https://offline.example/search", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "fetch_url")
            self.assertEqual(actions[0]["execution_result"], "error")
            self.assertEqual(actions[1]["command"], "fetch_url")
            self.assertEqual(actions[1]["execution_result"], "skipped")
            self.assertEqual(actions[1]["reason"], "recent_failed_url")
            self.assertEqual(actions[2]["execution_result"], "skipped")
            self.assertEqual(actions[2]["reason"], "recent_failed_url")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_repeated_client_error_host_retries_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = ClientErrorHostRetryPlanner(self)
            executor = FetchUrlExecutor()
            fetched_urls: list[str] = []

            def url_fetcher(url: str, max_chars: int) -> dict:
                fetched_urls.append(url)
                error = "HTTP Error 400: Bad Request"
                if "shape=two" in url:
                    error = "HTTP Error 404: Not Found"
                return {"status": "error", "url": url, "error": error}

            loop = DecisionLoop(
                task="Gather public evidence from a working source.",
                mode="auto",
                planner=planner,
                config={"max_steps": 5, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=url_fetcher,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(
                fetched_urls,
                [
                    "https://api.example/data?shape=one",
                    "https://api.example/data?shape=two",
                ],
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "error")
            self.assertEqual(actions[1]["execution_result"], "error")
            self.assertEqual(actions[2]["execution_result"], "skipped")
            self.assertEqual(actions[2]["reason"], "recent_failed_url")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_fetch_html_text_is_normalized_for_planner_evidence(self) -> None:
        raw_html = """
        <html>
          <head>
            <title>Example Fact Page</title>
            <style>.hidden { display: none }</style>
            <script>window.noise = true;</script>
          </head>
          <body>
            <h1>Example Fact Page</h1>
            <p>Current value: Waxing Crescent.</p>
          </body>
        </html>
        """

        text = _html_to_readable_text(raw_html)

        self.assertTrue(_looks_html_text(raw_html, "text/html"))
        self.assertIn("Example Fact Page", text)
        self.assertIn("Current value: Waxing Crescent.", text)
        self.assertNotIn("window.noise", text)
        self.assertNotIn("<p>", text)

    def test_speculative_variant_fill_after_failed_lookup_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = SpeculativeVariantPlanner(self)
            executor = SpeculativeVariantExecutor()
            loop = DecisionLoop(
                task="Complete a stateful requirement form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=lambda url, max_chars: {
                    "status": "error",
                    "url": url,
                    "error": "not found",
                },
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli fill e15 baseX", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "fetch_url")
            self.assertEqual(actions[1]["execution_result"], "skipped")
            self.assertEqual(actions[1]["reason"], "speculative_variant_edit")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_speculative_bulk_type_after_failed_lookup_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = SpeculativeBulkTypePlanner(self)
            executor = SpeculativeVariantExecutor()
            loop = DecisionLoop(
                task="Complete a stateful requirement form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=lambda url, max_chars: {
                    "status": "error",
                    "url": url,
                    "error": "not found",
                },
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli type ABCDEF", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "fetch_url")
            self.assertEqual(actions[1]["execution_result"], "skipped")
            self.assertEqual(actions[1]["reason"], "speculative_variant_edit")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_short_variant_fill_after_successful_latest_lookup_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = EvidenceRecoveredVariantPlanner(self)
            executor = SpeculativeVariantExecutor()

            def url_fetcher(url: str, max_chars: int) -> dict:
                if url.endswith("/missing"):
                    return {"status": "error", "url": url, "error": "not found"}
                return {
                    "status": "ok",
                    "url": url,
                    "content_type": "text/plain",
                    "text": "value=X",
                    "chars": 7,
                    "truncated": False,
                }

            loop = DecisionLoop(
                task="Complete a stateful requirement form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 5, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=url_fetcher,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertIn("playwright-cli fill e15 baseX", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[2]["execution_result"], "ok")
            self.assertNotEqual(actions[2].get("reason"), "speculative_variant_edit")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_status_regression_after_successful_action_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = StatusRegressionPlanner(self)
            executor = StatusRegressionExecutor()
            loop = DecisionLoop(
                task="Complete a multi-requirement stateful form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertIn("playwright-cli fill e15 baseX", executor.commands)
            self.assertIn("playwright-cli fill e15 baseY", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "playwright-cli fill e15 baseX")
            self.assertEqual(actions[0]["execution_result"], "ok")
            self.assertEqual(actions[1]["command"], "playwright-cli fill e15 baseY")
            self.assertEqual(actions[1]["execution_result"], "ok")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_fetch_url_returns_text_without_browser_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = FetchUrlPlanner(self)
            executor = FetchUrlExecutor()
            loop = DecisionLoop(
                task="Inspect a visible public text asset.",
                mode="auto",
                planner=planner,
                config={"max_steps": 3, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=lambda url, max_chars: {
                    "status": "ok",
                    "url": url,
                    "content_type": "image/svg+xml",
                    "text": "<svg><desc><pre>A B C</pre></desc></svg>"[:max_chars],
                    "chars": 43,
                    "truncated": False,
                },
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli goto https://assets.example/data.svg", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "fetch_url")
            self.assertEqual(actions[0]["execution_result"], "ok")
            self.assertEqual(actions[0]["content_type"], "image/svg+xml")
            self.assertNotIn("A B C", json.dumps(actions[0]))
            self.assertEqual(actions[-1]["command"], "finish")

    def test_text_asset_goto_on_stateful_page_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = TextAssetNavigationGuardPlanner(self)
            executor = FetchUrlExecutor()
            loop = DecisionLoop(
                task="Inspect a visible public text asset.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
                url_fetcher=lambda url, max_chars: {
                    "status": "ok",
                    "url": url,
                    "content_type": "image/svg+xml",
                    "text": "<svg><desc><pre>A B C</pre></desc></svg>"[:max_chars],
                    "chars": 43,
                    "truncated": False,
                },
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli goto https://assets.example/data.svg", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "skipped")
            self.assertEqual(actions[0]["reason"], "text_asset_navigation")
            self.assertEqual(actions[1]["command"], "fetch_url")
            self.assertEqual(actions[1]["execution_result"], "ok")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_fetch_url_rejects_non_http_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = RejectFetchSchemePlanner(self)
            executor = FetchUrlExecutor()
            loop = DecisionLoop(
                task="Inspect a visible public text asset.",
                mode="auto",
                planner=planner,
                config={"max_steps": 3, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "fetch_url")
            self.assertEqual(actions[0]["execution_result"], "error")
            self.assertIn("HTTP/HTTPS", actions[0]["error"])
            self.assertEqual(actions[-1]["command"], "finish")

    def test_selecting_current_tab_is_skipped_with_recovery_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = CurrentTabNoopPlanner(self)
            executor = CurrentTabNoopExecutor()
            loop = DecisionLoop(
                task="Use the lookup tab.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn("playwright-cli tab-select 1", executor.commands)
            self.assertIn("playwright-cli goto https://lookup.example/data", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "skipped")
            self.assertEqual(actions[0]["reason"], "tab_already_current")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_stale_element_ref_is_skipped_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = StaleRefGuardPlanner(self)
            executor = StaleRefGuardExecutor()
            loop = DecisionLoop(
                task="Use lookup evidence to update the task form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 5, "max_errors": 1, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertEqual(
                executor.commands.count("playwright-cli fill e15 baseX"),
                1,
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "skipped")
            self.assertEqual(actions[0]["reason"], "ref_not_in_current_snapshot")
            self.assertEqual(actions[1]["command"], "playwright-cli tab-select 0")
            self.assertEqual(actions[2]["command"], "playwright-cli fill e15 baseX")
            self.assertEqual(actions[-1]["command"], "finish")

    def test_stateful_task_tab_rejects_lookup_goto_away_from_task_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = StatefulTaskNavigationGuardPlanner(self)
            executor = StatefulTaskNavigationGuardExecutor()
            loop = DecisionLoop(
                task="Complete the stateful task form.",
                mode="auto",
                planner=planner,
                config={"max_steps": 6, "max_errors": 2, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="https://example.com/task",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertNotIn(
                "playwright-cli goto https://example.com/task/asset.svg",
                executor.commands,
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[3]["execution_result"], "skipped")
            self.assertEqual(
                actions[3]["reason"],
                "stateful_task_tab_lookup_navigation",
            )
            self.assertEqual(actions[-1]["command"], "finish")

    def test_snapshot_timeout_after_failed_navigation_goes_back_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = SnapshotTimeoutRecoveryPlanner(self)
            executor = SnapshotTimeoutRecoveryExecutor()
            loop = DecisionLoop(
                task="Inspect an asset without losing the original task page.",
                mode="auto",
                planner=planner,
                config={"max_steps": 4, "max_errors": 3, "min_visible_text": 0},
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/task.html",
                open_args=[],
                debug=False,
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            self.assertIn("playwright-cli go-back", executor.commands)
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["execution_result"], "error")
            self.assertEqual(actions[1]["approval_status"], "auto_recovery")
            self.assertEqual(actions[1]["command"], "playwright-cli go-back")
            self.assertEqual(
                actions[1]["trigger"],
                "snapshot_timeout_after_failed_navigation",
            )
            self.assertEqual(actions[-1]["command"], "finish")

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
            planner = VisualCodePlanner(self)
            executor = VisualCodeExecutor()
            loop = DecisionLoop(
                task="Pass the visual challenge.",
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
                open_url="http://127.0.0.1:8766/visual-challenge.html",
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
                    ["playwright-cli", "open", "http://127.0.0.1:8766/visual-challenge.html"],
                    ["playwright-cli", "fill", "e15", "initialABCD"],
                ],
            )
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "ask_human")
            self.assertEqual(actions[0]["execution_result"], "ok")
            self.assertTrue(actions[0]["response_provided"])
            self.assertNotIn("ABCD", json.dumps(actions[0]))
            self.assertEqual(
                shlex.split(actions[1]["command"]),
                ["playwright-cli", "fill", "e15", "initialABCD"],
            )
            self.assertEqual(actions[-1]["command"], "finish")

    def test_ask_human_skipped_when_source_token_evidence_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = SourceTokenVisualPlanner(self)
            executor = SourceTokenVisualExecutor()
            loop = DecisionLoop(
                task="Pass the visual challenge.",
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
                open_url="http://127.0.0.1:8766/visual-challenge.html",
                open_args=[],
                debug=False,
                human_input=lambda prompt: (_ for _ in ()).throw(
                    AssertionError("human input should not be called")
                ),
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "ask_human")
            self.assertEqual(actions[0]["execution_result"], "skipped")
            self.assertEqual(actions[0]["reason"], "source_token_available")
            self.assertEqual(
                shlex.split(actions[1]["command"]),
                ["playwright-cli", "fill", "e15", "initiala1b2c"],
            )
            self.assertEqual(actions[-1]["command"], "finish")

    def test_ask_human_eof_is_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = RunPaths(root / "run")
            paths.snapshots.mkdir(parents=True)
            planner = HumanInputUnavailablePlanner(self)
            executor = VisualCodeExecutor()
            loop = DecisionLoop(
                task="Handle visual challenge input.",
                mode="auto",
                planner=planner,
                config={
                    "max_steps": 3,
                    "max_errors": 1,
                    "min_visible_text": 0,
                    "allow_human_input": True,
                },
                paths=paths,
                executor=executor,
                open_url="http://127.0.0.1:8766/visual-challenge.html",
                open_args=[],
                debug=False,
                human_input=lambda prompt: (_ for _ in ()).throw(EOFError()),
            )

            result = loop.run()

            self.assertEqual(result.stop_reason, "completed")
            actions = _read_jsonl(paths.actions_log)
            self.assertEqual(actions[0]["command"], "ask_human")
            self.assertEqual(actions[0]["execution_result"], "skipped")
            self.assertEqual(actions[0]["approval_status"], "stdin_unavailable")
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

"""Main decision loop for the browser agent."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any

from browser_agent.action_parser import ActionParseError, ParsedAction, parse_action
from browser_agent.approval_system import ask_approval, requires_approval
from browser_agent.guardrails import detect_no_change, detect_repeated_action
from browser_agent.interpreter import interpret_page
from browser_agent.interpreter_state import to_dict as interpreter_to_dict
from browser_agent.logger import append_jsonl, write_run_meta, write_snapshot
from browser_agent.playwright_executor import PlaywrightExecutionError, PlaywrightExecutor
from browser_agent.planner import GeminiPlanner, PlannerError, parse_json_response
from browser_agent.prompt_builder import build_prompt
from browser_agent.snapshot_parser import compact_elements, load_snapshot_text, parse_snapshot


class DecisionLoop:
    """Coordinates snapshot, planning, approvals, and execution."""

    def __init__(
        self,
        *,
        task: str,
        mode: str,
        planner: GeminiPlanner,
        config: dict[str, Any],
        paths: Any,
        executor: PlaywrightExecutor,
        open_url: str | None,
        open_args: list[str],
        debug: bool,
        skill_text: str,
    ) -> None:
        self.task = task
        self.mode = mode
        self.planner = planner
        self.config = config
        self.paths = paths
        self.executor = executor
        self.open_url = open_url
        self.open_args = open_args
        self.debug = debug
        self.skill_text = skill_text
        self.step = 0
        self.errors = 0
        self.stop_reason = "unknown"
        self.action_history: list[str] = []
        self.last_snapshot_hash = ""
        self.snapshot_repeat_count = 0
        self.consecutive_planner_failures = 0
        self.last_action_ok = False
        self.short_text_retries = 0

    def run(self) -> str:
        start = time.monotonic()
        self._log(f"Run started | mode={self.mode} model={self.config.get('model')}")

        try:
            if self.debug:
                self._log("Debug mode enabled: tracing + video")
                self.executor.run("playwright-cli tracing-start")
                self.executor.run("playwright-cli video-start")

            self._open_browser()

            while self.step < int(self.config.get("max_steps", 50)):
                self.step += 1
                self._log(f"Step {self.step}: snapshot -> interpret -> plan -> execute")

                snapshot_result = self.executor.snapshot()
                if snapshot_result.returncode != 0:
                    error_text = snapshot_result.stderr or "snapshot failed"
                    if "install-browser" in error_text.lower():
                        self.stop_reason = "browser_not_installed"
                        self._log(f"Step {self.step}: {error_text}")
                        break
                    raise PlaywrightExecutionError(error_text)
                self._log(self._format_command_result("snapshot", snapshot_result))

                snapshot_text, snapshot_path = load_snapshot_text(snapshot_result.stdout)
                snapshot_file = write_snapshot(self.paths.snapshots, self.step, snapshot_text)
                self._log(f"Step {self.step}: snapshot saved {snapshot_file}")

                snapshot_state = parse_snapshot(snapshot_text)
                snapshot_state.source_path = snapshot_path
                snapshot_state.elements = compact_elements(
                    snapshot_state.elements,
                    int(self.config.get("max_elements", 60)),
                )

                snapshot_hash = _hash_text(snapshot_text)
                if snapshot_hash == self.last_snapshot_hash and self.last_action_ok:
                    self.snapshot_repeat_count += 1
                else:
                    self.snapshot_repeat_count = 0
                self.last_snapshot_hash = snapshot_hash

                interpreter_state = interpret_page(
                    snapshot_state,
                    self.executor,
                    max_clickables=int(self.config.get("max_elements", 60)),
                    max_visible_chars=int(self.config.get("max_visible_chars", 2000)),
                )
                interpreter_dict = interpreter_to_dict(interpreter_state)

                append_jsonl(
                    self.paths.browser_state_log,
                    {
                        "step": self.step,
                        "url": snapshot_state.url,
                        "title": snapshot_state.title,
                        "snapshot_path": snapshot_state.source_path,
                        "snapshot_file": str(snapshot_file),
                    },
                )
                append_jsonl(
                    self.paths.interpreter_state_log,
                    {
                        "step": self.step,
                        **interpreter_dict,
                    },
                )

                min_text = int(self.config.get("min_visible_text", 200))
                if interpreter_state.url.startswith("about:") or interpreter_state.url == "":
                    min_text = 0

                if len(interpreter_state.visible_text) < min_text:
                    self._log(
                        f"Step {self.step}: visible_text too short ({len(interpreter_state.visible_text)} chars), retrying"
                    )
                    self.short_text_retries += 1
                    if self.short_text_retries < 2 and self.step < int(self.config.get("max_steps", 50)):
                        time.sleep(0.5)
                        continue
                else:
                    self.short_text_retries = 0

                prompt = build_prompt(
                    self.task,
                    interpreter_state,
                    self.action_history,
                    max_elements=int(self.config.get("max_elements", 60)),
                    skill_text=self.skill_text,
                )
                self._log(f"Step {self.step}: prompt length={len(prompt)} chars")

                try:
                    result = self.planner.plan(prompt, max_retries=int(self.config.get("max_retries", 3)))
                    self.consecutive_planner_failures = 0
                    payload = parse_json_response(result.content)
                    self._log(
                        f"Step {self.step}: planner latency={result.latency_seconds:.2f}s "
                        f"attempts={result.attempts} rate_limited={result.rate_limited}"
                    )
                    self._log(f"Step {self.step}: planner raw response:\n{result.content}")
                except PlannerError as exc:
                    self.consecutive_planner_failures += 1
                    self.errors += 1
                    self._log(f"Step {self.step}: planner error: {exc}")
                    append_jsonl(
                        self.paths.llm_log,
                        {
                            "step": self.step,
                            "error": str(exc),
                            "thought": "",
                            "action": "",
                            "reasoning_summary": "planner_error",
                        },
                    )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    if self.consecutive_planner_failures >= 3 and "429" in str(exc):
                        self.stop_reason = "quota_exceeded"
                        break
                    time.sleep(1.0)
                    continue

                append_jsonl(
                    self.paths.llm_log,
                    {
                        "step": self.step,
                        "thought": payload.get("thought", ""),
                        "action": payload.get("action", ""),
                        "reasoning_summary": payload.get("reasoning_summary", ""),
                        "final": bool(payload.get("final", False)),
                    },
                )
                append_jsonl(
                    self.paths.reasoning_log,
                    {
                        "step": self.step,
                        "thought": payload.get("thought", ""),
                        "action": payload.get("action", ""),
                        "reasoning_summary": payload.get("reasoning_summary", ""),
                        "final": bool(payload.get("final", False)),
                    },
                )

                try:
                    parsed_action = parse_action(payload)
                except ActionParseError as exc:
                    self.errors += 1
                    self._log(f"Step {self.step}: action parse error: {exc}")
                    self._log(f"Step {self.step}: bad payload: {payload}")
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": "",
                            "approval_status": "n/a",
                            "execution_result": "parse_error",
                            "error": str(exc),
                        },
                    )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    continue

                completed_signal = _is_completion_payload(payload)

                if parsed_action.command == "open":
                    self.errors += 1
                    self._log(f"Step {self.step}: open command is not allowed inside the loop")
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "rejected_open",
                            "error": "open_not_allowed_in_loop",
                        },
                    )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    continue

                if detect_repeated_action(self.action_history, parsed_action.action):
                    self.stop_reason = "repeated_action"
                    break

                if detect_no_change(self.last_snapshot_hash, snapshot_hash, self.snapshot_repeat_count):
                    self.stop_reason = "completed" if completed_signal else "no_page_change"
                    break

                approved = True
                if requires_approval(self.mode, parsed_action, snapshot_state.elements):
                    self._log(f"Step {self.step}: awaiting approval for {parsed_action.action}")
                    approved = ask_approval(parsed_action)
                    self._log(
                        f"Step {self.step}: approval={'granted' if approved else 'rejected'}"
                    )

                if not approved:
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "rejected",
                            "execution_result": "skipped",
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    continue

                exec_result = self.executor.run(parsed_action.action)
                exec_status = "ok" if exec_result.returncode == 0 else "error"
                self._log(self._format_command_result(parsed_action.action, exec_result))
                self.last_action_ok = exec_status == "ok"

                append_jsonl(
                    self.paths.actions_log,
                    {
                        "step": self.step,
                        "command": parsed_action.action,
                        "approval_status": "approved",
                        "execution_result": exec_status,
                        "stdout": exec_result.stdout,
                        "stderr": exec_result.stderr,
                    },
                )

                self.action_history.append(parsed_action.action)

                if completed_signal:
                    self.stop_reason = "completed"
                    break

                if parsed_action.command in {"close", "close-all", "kill-all"}:
                    self.stop_reason = "closed"
                    break

                if exec_status != "ok":
                    self.errors += 1
                    if "install-browser" in exec_result.stderr.lower():
                        self.stop_reason = "browser_not_installed"
                        break
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break

            if self.stop_reason == "unknown":
                self.stop_reason = "max_steps"
        finally:
            if self.debug:
                self.executor.run("playwright-cli tracing-stop")
                self.executor.run(f"playwright-cli video-stop {self.paths.root / 'session.webm'}")
            runtime = time.monotonic() - start
            write_run_meta(
                self.paths.run_meta,
                {
                    "task": self.task,
                    "total_steps": self.step,
                    "stop_reason": self.stop_reason,
                    "runtime_seconds": round(runtime, 2),
                },
            )
            self._log(f"Run stopped | reason={self.stop_reason} steps={self.step}")

        return self.stop_reason

    def _open_browser(self) -> None:
        open_command = "playwright-cli open"
        if self.open_url:
            open_command += f" {self.open_url}"
        if self.open_args:
            open_command += " " + " ".join(self.open_args)
        result = self.executor.run(open_command)
        if result.returncode != 0:
            raise PlaywrightExecutionError(result.stderr or "open failed")

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[BrowserAgent {ts}] {message}", flush=True)

    @staticmethod
    def _format_command_result(label: str, result: Any) -> str:
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return (
            f"Command result ({label}): rc={result.returncode} "
            f"stdout_len={len(stdout)} stderr_len={len(stderr)}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_completion_payload(payload: dict[str, Any]) -> bool:
    if bool(payload.get("final")) is True:
        return True
    summary = str(payload.get("reasoning_summary", "")).lower()
    return any(token in summary for token in ("task complete", "task completed", "done", "completed"))

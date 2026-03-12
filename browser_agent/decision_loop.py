"""Main decision loop for the browser agent."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any

from browser_agent.action_parser import ActionParseError, parse_tool_call
from browser_agent.approval_system import ask_approval, requires_approval
from browser_agent.guardrails import detect_no_change, detect_repeated_action
from browser_agent.interpreter import interpret_page
from browser_agent.interpreter_state import to_dict as interpreter_to_dict
from browser_agent.logger import append_jsonl, write_run_meta, write_snapshot
from browser_agent.memory import MemoryStore, _domain_from_url, extract_lessons_from_run
from browser_agent.playwright_executor import PlaywrightExecutionError, PlaywrightExecutor
from browser_agent.planner import ChatPlanner, PlannerError
from browser_agent.prompt_builder import build_page_message
from browser_agent.snapshot_parser import compact_elements, load_snapshot_text, parse_snapshot


class DecisionLoop:
    """Coordinates snapshot, planning, approvals, and execution."""

    def __init__(
        self,
        *,
        task: str,
        mode: str,
        planner: ChatPlanner,
        config: dict[str, Any],
        paths: Any,
        executor: PlaywrightExecutor,
        open_url: str | None,
        open_args: list[str],
        debug: bool,
        memory: MemoryStore | None = None,
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
        self.memory = memory
        self.step = 0
        self.errors = 0
        self.stop_reason = "unknown"
        self.action_history: list[str] = []
        self.last_snapshot_hash = ""
        self.snapshot_repeat_count = 0
        self.consecutive_planner_failures = 0
        self.last_action_ok = False
        self.short_text_retries = 0
        self.last_step_error: str | None = None
        self.last_domain: str | None = None
        self._domain_context: str | None = None

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

                # ---- Snapshot ----
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

                # ---- Interpret ----
                interpreter_state = interpret_page(
                    snapshot_state,
                    self.executor,
                    max_clickables=int(self.config.get("max_elements", 60)),
                    max_visible_chars=int(self.config.get("max_visible_chars", 2000)),
                )
                interpreter_dict = interpreter_to_dict(interpreter_state)

                # ---- Memory: domain recall (Trigger B) ----
                if self.memory:
                    current_domain = _domain_from_url(interpreter_state.url or "")
                    if current_domain and current_domain != self.last_domain:
                        site_lessons = self.memory.recall_on_domain(current_domain)
                        if site_lessons:
                            tips = "\n".join(f"- {ls.lesson}" for ls in site_lessons)
                            self._domain_context = tips
                            for ls in site_lessons:
                                self.memory.increment_use(ls, current_domain)
                        else:
                            self._domain_context = None
                        self.last_domain = current_domain

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
                    {"step": self.step, **interpreter_dict},
                )

                # ---- Short text guard ----
                min_text = int(self.config.get("min_visible_text", 200))
                if interpreter_state.url.startswith("about:") or interpreter_state.url == "":
                    min_text = 0

                if len(interpreter_state.visible_text) < min_text:
                    self._log(
                        f"Step {self.step}: visible_text too short "
                        f"({len(interpreter_state.visible_text)} chars), retrying"
                    )
                    self.short_text_retries += 1
                    if self.short_text_retries < 2 and self.step < int(
                        self.config.get("max_steps", 50)
                    ):
                        time.sleep(0.5)
                        continue
                else:
                    self.short_text_retries = 0

                # ---- Plan (send page state, get tool call) ----
                message = build_page_message(
                    interpreter_state,
                    self.action_history,
                    max_elements=int(self.config.get("max_elements", 60)),
                    last_error=self.last_step_error,
                    domain_context=self._domain_context,
                )
                self.last_step_error = None
                self._log(f"Step {self.step}: message length={len(message)} chars")

                try:
                    tool_result = self.planner.plan(
                        message,
                        max_retries=int(self.config.get("max_retries", 3)),
                    )
                    self.consecutive_planner_failures = 0
                    self._log(
                        f"Step {self.step}: planner latency={tool_result.latency_seconds:.2f}s "
                        f"attempts={tool_result.attempts} rate_limited={tool_result.rate_limited}"
                    )
                    if tool_result.reasoning_text:
                        self._log(
                            f"Step {self.step}: reasoning: {tool_result.reasoning_text[:500]}"
                        )
                    self._log(
                        f"Step {self.step}: tool_call={tool_result.tool_name}"
                        f"({tool_result.tool_args})"
                    )
                except PlannerError as exc:
                    self.consecutive_planner_failures += 1
                    self.errors += 1
                    self._log(f"Step {self.step}: planner error: {exc}")
                    append_jsonl(
                        self.paths.llm_log,
                        {
                            "step": self.step,
                            "error": str(exc),
                            "tool_name": "",
                            "tool_args": {},
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
                        "tool_name": tool_result.tool_name,
                        "tool_args": tool_result.tool_args,
                        "reasoning": tool_result.reasoning_text,
                    },
                )

                # ---- Handle finish tool ----
                if tool_result.tool_name == "finish":
                    self.stop_reason = "completed"
                    reason = tool_result.tool_args.get("reason", "")
                    self._log(f"Step {self.step}: task completed — {reason}")
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": "finish",
                            "approval_status": "n/a",
                            "execution_result": "completed",
                            "reason": reason,
                        },
                    )
                    break

                # ---- Parse tool call into CLI command ----
                try:
                    parsed_action = parse_tool_call(
                        tool_result.tool_name, tool_result.tool_args
                    )
                except ActionParseError as exc:
                    self.errors += 1
                    self.last_step_error = (
                        f"Invalid tool call: {exc} "
                        f"(tool={tool_result.tool_name}, args={tool_result.tool_args})"
                    )
                    self._log(f"Step {self.step}: action parse error: {exc}")
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": tool_result.tool_name,
                            "approval_status": "n/a",
                            "execution_result": "parse_error",
                            "error": str(exc),
                        },
                    )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    continue

                # ---- Guardrails ----
                if detect_repeated_action(self.action_history, parsed_action.action):
                    self.stop_reason = "repeated_action"
                    break

                if detect_no_change(
                    self.last_snapshot_hash, snapshot_hash, self.snapshot_repeat_count
                ):
                    self.stop_reason = "no_page_change"
                    break

                # ---- Approval ----
                approved = True
                if requires_approval(self.mode, parsed_action, snapshot_state.elements):
                    self._log(
                        f"Step {self.step}: awaiting approval for {parsed_action.action}"
                    )
                    approved = ask_approval(parsed_action)
                    self._log(
                        f"Step {self.step}: approval="
                        f"{'granted' if approved else 'rejected'}"
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

                # ---- Execute ----
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

                # Send execution result back to chat so the LLM knows what happened.
                result_payload = {"status": exec_status}
                if exec_status == "ok" and exec_result.stdout:
                    result_payload["output"] = exec_result.stdout[:2000]
                elif exec_status != "ok":
                    result_payload["error"] = (exec_result.stderr or "command failed")[:500]
                    self.last_step_error = result_payload["error"]

                try:
                    self.planner.send_tool_result(
                        tool_result.tool_name, result_payload
                    )
                except Exception:  # noqa: BLE001
                    pass  # Non-critical; chat may still work without the result.

                if parsed_action.command in {"close", "close-all", "kill-all"}:
                    self.stop_reason = "closed"
                    break

                if exec_status != "ok":
                    self.errors += 1
                    if "install-browser" in exec_result.stderr.lower():
                        self.stop_reason = "browser_not_installed"
                        break
                    # ---- Memory: error recall (Trigger A) ----
                    if self.memory and self.last_step_error:
                        cmd_name = parsed_action.command or ""
                        tips = self.memory.recall_on_error(
                            cmd_name, self.last_step_error
                        )
                        if tips:
                            hint = "\n".join(f"- {t.lesson}" for t in tips)
                            self.last_step_error += (
                                f"\n\nTips from previous experience:\n{hint}"
                            )
                            domain = _domain_from_url(
                                interpreter_state.url or ""
                            )
                            for t in tips:
                                self.memory.increment_use(t, domain)
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break

            if self.stop_reason == "unknown":
                self.stop_reason = "max_steps"
        finally:
            # ---- Post-run learning ----
            if self.memory:
                try:
                    extract_lessons_from_run(self.paths.actions_log, self.memory)
                except Exception:  # noqa: BLE001
                    pass  # Non-critical; don't crash the agent.

            if self.debug:
                self.executor.run("playwright-cli tracing-stop")
                self.executor.run(
                    f"playwright-cli video-stop {self.paths.root / 'session.webm'}"
                )
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

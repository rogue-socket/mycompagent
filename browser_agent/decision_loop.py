"""Main decision loop for the browser agent."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib import error as urlerror
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from browser_agent.action_parser import ActionParseError, parse_tool_call
from browser_agent.approval_system import ask_approval, requires_approval
from browser_agent.guardrails import (
    detect_no_change,
    detect_repeated_action,
    redundant_same_page_anchor_click,
)
from browser_agent.interpreter import interpret_page
from browser_agent.interpreter_state import to_dict as interpreter_to_dict
from browser_agent.logger import append_jsonl, write_run_meta, write_snapshot
from browser_agent.memory import MemoryStore, _domain_from_url, extract_lessons_from_run
from browser_agent.playwright_executor import PlaywrightExecutionError, PlaywrightExecutor
from browser_agent.planner import ChatPlanner, PlannerConfigurationError, PlannerError
from browser_agent.prompt_builder import build_page_message, planner_state_debug_payload
from browser_agent.snapshot_parser import load_snapshot_text, parse_snapshot
from browser_agent.task_state import EvidenceLedger, build_task_contract


@dataclass(slots=True)
class RunResult:
    stop_reason: str
    finish_output: str | None
    steps_used: int
    grounded_route: list[dict[str, str]]


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
        human_input: Callable[[str], str] | None = None,
        url_fetcher: Callable[[str, int], dict[str, Any]] | None = None,
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
        self.human_input = human_input or input
        self.url_fetcher = url_fetcher or fetch_public_text_url
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
        self._debug_started_at = 0.0
        self.clicked_route: list[dict[str, str]] = []
        self.finish_output: str | None = None
        self.task_contract = build_task_contract(task)
        self.evidence_ledger = EvidenceLedger()

    def run(self) -> RunResult:
        start = time.monotonic()
        provider = self.config.get("llm_provider", "gemini")
        model = self.config.get("active_model") or self.config.get("model")
        self._log(f"Run started | mode={self.mode} provider={provider} model={model}")

        try:
            self._open_browser()

            if self.debug:
                self._log("Debug mode enabled: tracing + video")
                self._debug_started_at = time.time()
                self._run_debug_command("tracing-start", "playwright-cli tracing-start")
                self._run_debug_command("video-start", "playwright-cli video-start")

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
                    if self._recover_snapshot_timeout_after_failed_navigation(error_text):
                        continue
                    raise PlaywrightExecutionError(error_text)
                self._log(self._format_command_result("snapshot", snapshot_result))

                snapshot_text, snapshot_path = load_snapshot_text(snapshot_result.stdout)
                snapshot_file = write_snapshot(self.paths.snapshots, self.step, snapshot_text)
                self._log(f"Step {self.step}: snapshot saved {snapshot_file}")

                snapshot_state = parse_snapshot(snapshot_text)
                snapshot_state.source_path = snapshot_path

                snapshot_hash = _hash_text(snapshot_text)
                if snapshot_hash == self.last_snapshot_hash and self.last_action_ok:
                    self.snapshot_repeat_count += 1
                else:
                    self.snapshot_repeat_count = 0
                self.last_snapshot_hash = snapshot_hash

                # ---- Interpret ----
                max_elements = int(self.config.get("max_elements", 60))
                interpreter_state = interpret_page(
                    snapshot_state,
                    self.executor,
                    max_clickables=int(
                        self.config.get(
                            "max_interpreter_elements",
                            max(max_elements, 1200),
                        )
                    ),
                    max_visible_chars=int(self.config.get("max_visible_chars", 2000)),
                )
                interpreter_dict = interpreter_to_dict(interpreter_state)
                self.evidence_ledger.add_page(
                    step=self.step,
                    url=interpreter_state.url,
                    title=interpreter_state.title,
                    text="\n".join(
                        part
                        for part in (
                            snapshot_state.raw_text,
                            interpreter_state.visible_text,
                            interpreter_state.dom_evidence,
                        )
                        if part
                    ),
                    contract=self.task_contract,
                )
                task_context = self.evidence_ledger.summary(self.task_contract)

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
                            self._log(
                                f"Step {self.step}: Memory: domain recall for "
                                f"{current_domain} -> {len(site_lessons)} tips injected"
                            )
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
                if interpreter_state.clickable_elements:
                    min_text = min(min_text, 80)

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

                if detect_no_change(
                    self.last_snapshot_hash,
                    snapshot_hash,
                    self.snapshot_repeat_count,
                ):
                    warning = (
                        "The page snapshot did not change after the last action. "
                        "Choose an action that changes the page or advances to a new link; "
                        "do not request another snapshot unless the user asked for one."
                    )
                    self.last_step_error = (
                        f"{self.last_step_error}\n\n{warning}"
                        if self.last_step_error
                        else warning
                    )

                # ---- Plan (send page state, get tool call) ----
                message = build_page_message(
                    interpreter_state,
                    self.action_history,
                    max_elements=max_elements,
                    last_error=self.last_step_error,
                    domain_context=self._domain_context,
                    task=self.task,
                    evidence_text=snapshot_state.raw_text,
                    task_context=task_context,
                )
                self.last_step_error = None
                self._log(f"Step {self.step}: message length={len(message)} chars")
                planner_debug_payload = (
                    self._planner_state_debug_payload(
                        interpreter_state,
                        snapshot_state,
                        max_elements,
                    )
                    if self.debug
                    else None
                )

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
                        append_jsonl(
                            self.paths.reasoning_log,
                            self._reasoning_log_payload(tool_result),
                        )
                    if self.debug:
                        self._log_planner_debug_io(
                            message,
                            tool_result=tool_result,
                            planner_state=planner_debug_payload,
                        )
                    self._log(
                        f"Step {self.step}: tool_call={tool_result.tool_name}"
                        f"({tool_result.tool_args})"
                    )
                except PlannerConfigurationError as exc:
                    self.errors += 1
                    self.stop_reason = "configuration_error"
                    self._log(f"Step {self.step}: planner configuration error: {exc}")
                    append_jsonl(
                        self.paths.llm_log,
                        self._planner_log_payload(
                            message,
                            error=str(exc),
                            non_retryable=True,
                        ),
                    )
                    if self.debug:
                        self._log_planner_debug_io(
                            message,
                            error=str(exc),
                            planner_state=planner_debug_payload,
                        )
                    break
                except PlannerError as exc:
                    self.consecutive_planner_failures += 1
                    self.errors += 1
                    self._log(f"Step {self.step}: planner error: {exc}")
                    append_jsonl(
                        self.paths.llm_log,
                        self._planner_log_payload(message, error=str(exc)),
                    )
                    if self.debug:
                        self._log_planner_debug_io(
                            message,
                            error=str(exc),
                            planner_state=planner_debug_payload,
                        )
                    if _planner_error_is_quota(str(exc)):
                        self.stop_reason = "quota_exceeded"
                        break
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    time.sleep(1.0)
                    continue

                append_jsonl(
                    self.paths.llm_log,
                    self._planner_log_payload(message, tool_result=tool_result),
                )

                # ---- Handle finish tool ----
                if tool_result.tool_name == "finish":
                    reason = tool_result.tool_args.get("reason", "")
                    finish_text = tool_result.tool_args.get("output") or reason
                    validation = self.evidence_ledger.validate_finish(
                        finish_text,
                        self.task_contract,
                        current_url=snapshot_state.url,
                    )
                    if not validation.accepted:
                        self.last_step_error = validation.message
                        self._log(f"Step {self.step}: finish rejected — {validation.message}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "finish",
                                "approval_status": "n/a",
                                "execution_result": "rejected",
                                "reason": reason,
                                "validation_error": validation.message,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("finish rejected: " + validation.message)
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": validation.message},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    self.stop_reason = "completed"
                    self.finish_output = tool_result.tool_args.get("output") or reason
                    self._log(f"Step {self.step}: task completed — {reason}")
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": "finish",
                            "approval_status": "n/a",
                            "execution_result": "completed",
                            "reason": reason,
                            "grounded_route": self.clicked_route,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    break

                # ---- Handle human input tool ----
                if tool_result.tool_name == "ask_human":
                    question = tool_result.tool_args.get("question", "").strip()
                    reason = tool_result.tool_args.get("reason", "").strip()
                    if not question:
                        question = "Please provide the missing information needed to continue."
                    if not self._human_input_allowed():
                        error = (
                            "Human input was requested, but it is disabled for this run. "
                            "Run with --ask-human in auto mode, or use safe/hybrid mode."
                        )
                        self.last_step_error = error
                        self._log(f"Step {self.step}: ask_human skipped — {error}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "ask_human",
                                "approval_status": "disabled",
                                "execution_result": "skipped",
                                "question": question,
                                "reason": reason,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("ask_human skipped: " + question)
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": error},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue

                    self._log(f"Step {self.step}: awaiting human input — {question}")
                    answer = self.human_input(f"\n[BrowserAgent] {question}\n> ").strip()
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": "ask_human",
                            "approval_status": "human_input",
                            "execution_result": "ok",
                            "question": question,
                            "reason": reason,
                            "response_provided": bool(answer),
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append("ask_human answered: " + question)
                    try:
                        self.planner.send_tool_result(
                            tool_result.tool_name,
                            {"status": "ok", "answer": answer},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    continue

                # ---- Handle public text fetch tool ----
                if tool_result.tool_name == "fetch_url":
                    url = tool_result.tool_args.get("url", "").strip()
                    max_chars = _fetch_max_chars(tool_result.tool_args.get("max_chars"))
                    fetch_result = self.url_fetcher(url, max_chars)
                    status = str(fetch_result.get("status", "error"))
                    self.last_action_ok = status == "ok"
                    if status != "ok":
                        self.last_step_error = str(
                            fetch_result.get("error") or "fetch_url failed"
                        )
                    self._log(
                        "Step "
                        f"{self.step}: fetch_url {status} - "
                        f"{url or '<missing url>'}"
                    )
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": "fetch_url",
                            "approval_status": "n/a",
                            "execution_result": status,
                            "url": url,
                            "content_type": fetch_result.get("content_type", ""),
                            "chars": fetch_result.get("chars", 0),
                            "truncated": fetch_result.get("truncated", False),
                            "error": fetch_result.get("error", ""),
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append("fetch_url " + status + ": " + url)
                    try:
                        self.planner.send_tool_result(tool_result.tool_name, fetch_result)
                    except Exception:  # noqa: BLE001
                        pass
                    continue

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
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break
                    continue

                # ---- Guardrails ----
                redundant_anchor = redundant_same_page_anchor_click(
                    parsed_action,
                    snapshot_state.elements,
                    interpreter_state.url,
                )
                if redundant_anchor:
                    self.last_step_error = (
                        f"Element {redundant_anchor.ref} points to the section already "
                        "shown in the current URL. Choose a different link or use the "
                        "current page content instead."
                    )
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "same_page_anchor_already_current",
                            "href": redundant_anchor.url,
                            "current_url": interpreter_state.url,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                if detect_repeated_action(self.action_history, parsed_action.action):
                    self.stop_reason = "repeated_action"
                    break

                if parsed_action.command == "snapshot":
                    self.last_step_error = (
                        "A fresh snapshot is already captured at the start of every "
                        "step. Choose click, scroll, press, or another action that "
                        "changes the page state."
                    )
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "snapshot_already_available",
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                current_tab_noop = _current_tab_selection_noop(
                    parsed_action,
                    snapshot_state.raw_text,
                )
                if current_tab_noop:
                    self.last_step_error = current_tab_noop
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "tab_already_current",
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                risky_navigation = self._stateful_task_tab_navigation_warning(
                    parsed_action,
                    interpreter_state,
                )
                if risky_navigation:
                    self.last_step_error = risky_navigation
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "stateful_task_tab_lookup_navigation",
                            "current_url": interpreter_state.url,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

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
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    continue

                # ---- Execute ----
                target_element = self._target_element_for_click(
                    parsed_action,
                    snapshot_state.elements,
                )
                exec_result = self.executor.run(parsed_action.action)
                exec_status = "ok" if exec_result.returncode == 0 else "error"
                self._log(self._format_command_result(parsed_action.action, exec_result))
                self.last_action_ok = exec_status == "ok"
                recovery_result = None
                recovery_status = ""
                recovery_command = ""
                recovery_kind = ""
                if exec_status != "ok" and self._drag_failed_by_cli_schema(
                    parsed_action, exec_result
                ):
                    recovery_command = self._drag_mouse_fallback_command(
                        parsed_action,
                        snapshot_state.elements,
                    )
                    if recovery_command:
                        recovery_kind = "drag_mouse_fallback"
                        self._log(
                            f"Step {self.step}: drag failed in CLI schema; "
                            "retrying with mouse fallback"
                        )
                        recovery_result = self.executor.run(recovery_command)
                        recovery_status = (
                            "ok" if recovery_result.returncode == 0 else "error"
                        )
                        self._log(
                            self._format_command_result(
                                "auto-recovery drag mouse fallback",
                                recovery_result,
                            )
                        )
                        if recovery_status == "ok":
                            exec_result = recovery_result
                            exec_status = "ok"
                            self.last_action_ok = True
                if exec_status != "ok" and self._click_blocked_by_overlay(
                    parsed_action, exec_result
                ):
                    recovery_command = "playwright-cli press Escape"
                    recovery_kind = "overlay_escape"
                    self._log(
                        f"Step {self.step}: click blocked by overlay; pressing Escape"
                    )
                    recovery_result = self.executor.run(recovery_command)
                    recovery_status = (
                        "ok" if recovery_result.returncode == 0 else "error"
                    )
                    self._log(
                        self._format_command_result(
                            "auto-recovery press Escape", recovery_result
                        )
                    )
                    self.last_action_ok = recovery_status == "ok"

                action_payload: dict[str, Any] = {
                    "step": self.step,
                    "command": parsed_action.action,
                    "approval_status": "approved",
                    "execution_result": exec_status,
                    "stdout": exec_result.stdout,
                    "stderr": exec_result.stderr,
                    "planner_latency_seconds": tool_result.latency_seconds,
                }
                if target_element:
                    route_entry = self._click_route_entry(target_element)
                    action_payload["target"] = route_entry
                    if exec_status == "ok":
                        self.clicked_route.append(route_entry)
                if recovery_result is not None:
                    action_payload["recovery"] = {
                        "kind": recovery_kind,
                        "command": recovery_command,
                        "execution_result": recovery_status,
                        "stdout": recovery_result.stdout,
                        "stderr": recovery_result.stderr,
                    }

                append_jsonl(
                    self.paths.actions_log,
                    action_payload,
                )

                self.action_history.append(parsed_action.action)
                if recovery_result is not None:
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": recovery_command,
                            "approval_status": "auto_recovery",
                            "execution_result": recovery_status,
                            "trigger": parsed_action.action,
                            "stdout": recovery_result.stdout,
                            "stderr": recovery_result.stderr,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(recovery_command)

                # Send execution result back to chat so the LLM knows what happened.
                result_payload = {"status": exec_status}
                if exec_status == "ok" and exec_result.stdout:
                    result_payload["output"] = exec_result.stdout[:2000]
                elif exec_status != "ok":
                    result_payload["error"] = (exec_result.stderr or "command failed")[:500]
                    self.last_step_error = result_payload["error"]
                    if recovery_status == "ok":
                        if recovery_kind == "overlay_escape":
                            result_payload["recovery"] = "pressed Escape to dismiss blocking overlay"
                            self.last_step_error = (
                                "The click was blocked by a modal or overlay, so Escape was "
                                "pressed to dismiss it. Use the updated page state instead of "
                                "repeating the blocked click."
                            )
                        elif recovery_kind == "drag_mouse_fallback":
                            result_payload["recovery"] = "retried drag with mouse fallback"

                try:
                    self.planner.send_tool_result(
                        tool_result.tool_name, result_payload
                    )
                except Exception:  # noqa: BLE001
                    pass  # Non-critical; chat may still work without the result.

                if parsed_action.command in {"close", "close-all", "kill-all"}:
                    self.stop_reason = "closed"
                    break

                if recovery_status == "ok":
                    continue

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
                            self._log(
                                f"Step {self.step}: Memory: error recall for "
                                f"'{cmd_name}' -> {len(tips)} tips found"
                            )
                    if self.errors >= int(self.config.get("max_errors", 5)):
                        self.stop_reason = "max_errors"
                        break

            if self.stop_reason == "unknown":
                self.stop_reason = "max_steps"
        finally:
            # ---- Post-run learning ----
            if self.memory:
                lesson_count_before = len(self.memory.lessons)
                try:
                    extract_lessons_from_run(self.paths.actions_log, self.memory)
                    new_lessons = len(self.memory.lessons) - lesson_count_before
                    if new_lessons:
                        self._log(f"Memory: post-run learning -> {new_lessons} new lesson(s)")
                except Exception as exc:  # noqa: BLE001
                    self._log(f"Memory: post-run learning failed: {exc}")
                try:
                    self.memory.save()
                    self._log(
                        f"Memory: saved {len(self.memory.lessons)} lessons to {self.memory.path}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log(f"Memory: save failed: {exc}")

            if self.debug:
                self._run_debug_command("tracing-stop", "playwright-cli tracing-stop")
                copied_traces = self._copy_debug_traces()
                append_jsonl(
                    self.paths.debug_log,
                    {
                        "event": "trace-copy",
                        "copied_files": [str(path) for path in copied_traces],
                    },
                )
                video_path = self.paths.root / "session.webm"
                self._run_debug_command(
                    "video-stop",
                    f"playwright-cli video-stop --filename {shlex.quote(str(video_path))}",
                )
                append_jsonl(
                    self.paths.debug_log,
                    {
                        "event": "video-artifact",
                        "path": str(video_path),
                        "exists": video_path.exists(),
                    },
                )
            runtime = time.monotonic() - start
            write_run_meta(
                self.paths.run_meta,
                {
                    "task": self.task,
                    "total_steps": self.step,
                    "stop_reason": self.stop_reason,
                    "finish_output": self.finish_output,
                    "grounded_route": self.clicked_route,
                    "runtime_seconds": round(runtime, 2),
                },
            )
            self._log(f"Run stopped | reason={self.stop_reason} steps={self.step}")

        return self._run_result()

    def _open_browser(self) -> None:
        open_command = "playwright-cli open"
        if self.open_url:
            open_command += f" {self.open_url}"
        if self.open_args:
            open_command += " " + " ".join(self.open_args)
        result = self.executor.run(open_command)
        if result.returncode != 0:
            raise PlaywrightExecutionError(result.stderr or "open failed")

    def _recover_snapshot_timeout_after_failed_navigation(self, error_text: str) -> bool:
        lowered = error_text.lower()
        if "timeout" not in lowered or "snapshot" not in lowered:
            return False
        if (
            not self.action_history
            or not self.action_history[-1].startswith("playwright-cli goto ")
        ):
            return False
        if self.last_action_ok:
            return False

        recovery_command = "playwright-cli go-back"
        self._log(
            f"Step {self.step}: snapshot timed out after failed navigation; "
            "going back once before retrying"
        )
        recovery_result = self.executor.run(recovery_command)
        recovery_status = "ok" if recovery_result.returncode == 0 else "error"
        self._log(
            self._format_command_result(
                "auto-recovery go back after snapshot timeout",
                recovery_result,
            )
        )
        append_jsonl(
            self.paths.actions_log,
            {
                "step": self.step,
                "command": recovery_command,
                "approval_status": "auto_recovery",
                "execution_result": recovery_status,
                "trigger": "snapshot_timeout_after_failed_navigation",
                "stdout": recovery_result.stdout,
                "stderr": recovery_result.stderr,
            },
        )
        self.action_history.append(recovery_command)
        self.last_action_ok = recovery_status == "ok"
        self.last_step_error = (
            "The fresh page snapshot timed out after the previous navigation failed. "
            "The browser went back once; use the updated page state and avoid repeating "
            "the same failed navigation."
        )
        if recovery_status == "ok":
            return True

        self.errors += 1
        return False

    def _stateful_task_tab_navigation_warning(
        self,
        parsed_action: Any,
        interpreter_state: Any,
    ) -> str:
        if parsed_action.command != "goto" or not parsed_action.args:
            return ""
        if not self.open_url or not _same_location(interpreter_state.url, self.open_url):
            return ""
        if not _navigates_away_from_location(interpreter_state.url, parsed_action.args[0]):
            return ""
        if not self._has_stateful_task_controls(interpreter_state):
            return ""
        if not self._recent_lookup_workflow():
            return ""
        return (
            "Stateful task-tab navigation guard: the browser is back on the original "
            "task page with active form/editable state. Do not use goto here for more "
            "lookup, including same-site asset or detail URLs, because it can discard "
            "task progress. Switch to an existing lookup tab or open a new blank tab, "
            "load the lookup URL there, extract the value, then return to this task tab "
            "before editing visible task controls."
        )

    def _has_stateful_task_controls(self, interpreter_state: Any) -> bool:
        if "active_editable" in (getattr(interpreter_state, "dom_evidence", "") or ""):
            return True
        return any(
            getattr(element, "element_type", "") == "input"
            for element in getattr(interpreter_state, "clickable_elements", [])
        )

    def _recent_lookup_workflow(self) -> bool:
        recent = self.action_history[-10:]
        has_tab_action = any(
            action.startswith(("playwright-cli tab-new", "playwright-cli tab-select"))
            for action in recent
        )
        has_navigation = any(action.startswith("playwright-cli goto ") for action in recent)
        return has_tab_action and has_navigation

    def _human_input_allowed(self) -> bool:
        return bool(self.config.get("allow_human_input", self.mode != "auto"))

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[BrowserAgent {ts}] {message}", flush=True)

    def _run_debug_command(self, label: str, command: str) -> None:
        result = self.executor.run(command)
        append_jsonl(
            self.paths.debug_log,
            {
                "event": label,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        self._log(
            f"Debug {label}: rc={result.returncode} "
            f"stdout_len={len(result.stdout or '')} stderr_len={len(result.stderr or '')}"
        )

    def _planner_state_debug_payload(
        self,
        interpreter_state: Any,
        snapshot_state: Any,
        max_elements: int,
    ) -> dict[str, Any]:
        payload = planner_state_debug_payload(
            interpreter_state,
            max_elements=max_elements,
            task=self.task,
            evidence_text=snapshot_state.raw_text,
        )
        selected_refs = {
            item["ref"] for item in payload.get("selected_clickables", []) if item.get("ref")
        }
        payload["cursor_pointer_refs_excluded"] = [
            {
                "ref": elem.ref,
                "description": elem.description,
                "metadata": list(getattr(elem, "metadata", ())),
                "child_text": getattr(elem, "child_text", "")[:300],
            }
            for elem in snapshot_state.elements
            if "cursor=pointer" in {item.lower() for item in getattr(elem, "metadata", ())}
            and elem.ref not in selected_refs
        ]
        return payload

    def _log_planner_debug_io(
        self,
        message: str,
        *,
        tool_result: Any | None = None,
        error: str | None = None,
        planner_state: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "planner-io",
            "step": self.step,
            "planner_prompt": (
                getattr(tool_result, "prompt_text", "") if tool_result is not None else ""
            )
            or message,
            "raw_model_response": (
                getattr(tool_result, "raw_response", "") if tool_result is not None else ""
            ),
        }
        if error:
            payload["error"] = error
        if tool_result is not None:
            payload.update(
                {
                    "tool_name": tool_result.tool_name,
                    "tool_args": tool_result.tool_args,
                    "planner_latency_seconds": tool_result.latency_seconds,
                    "planner_attempts": tool_result.attempts,
                    "planner_rate_limited": tool_result.rate_limited,
                }
            )
        if planner_state:
            payload.update(planner_state)
        append_jsonl(self.paths.debug_log, payload)

    def _planner_log_payload(
        self,
        message: str,
        *,
        tool_result: Any | None = None,
        error: str | None = None,
        non_retryable: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "prompt_chars": len(message),
            "debug_artifacts": self._debug_artifact_paths(),
        }
        if tool_result is not None:
            payload.update(
                {
                    "tool_name": tool_result.tool_name,
                    "tool_args": tool_result.tool_args,
                    "reasoning": tool_result.reasoning_text,
                    "planner_latency_seconds": tool_result.latency_seconds,
                    "planner_attempts": tool_result.attempts,
                    "planner_rate_limited": tool_result.rate_limited,
                }
            )
            if self.debug:
                payload["planner_prompt"] = tool_result.prompt_text or message
                payload["raw_model_response"] = tool_result.raw_response
        else:
            payload.update({"error": error or "", "tool_name": "", "tool_args": {}})
            if non_retryable:
                payload["non_retryable"] = True
            if self.debug:
                payload["planner_prompt"] = message
        return payload

    def _debug_artifact_paths(self) -> dict[str, Any]:
        return {
            "enabled": self.debug,
            "debug_log": str(self.paths.debug_log),
            "traces_dir": str(self.paths.traces),
            "video": str(self.paths.root / "session.webm"),
        }

    def _run_result(self) -> RunResult:
        return RunResult(
            stop_reason=self.stop_reason,
            finish_output=self.finish_output,
            steps_used=self.step,
            grounded_route=list(self.clicked_route),
        )

    def _reasoning_log_payload(self, tool_result: Any) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool_name": tool_result.tool_name,
            "tool_args": tool_result.tool_args,
            "reasoning": tool_result.reasoning_text,
            "planner_latency_seconds": tool_result.latency_seconds,
            "planner_attempts": tool_result.attempts,
        }

    @staticmethod
    def _target_element_for_click(action: Any, elements: list[Any]) -> Any | None:
        if action.command != "click" or not action.args:
            return None
        target_ref = action.args[0]
        for elem in elements:
            if elem.ref == target_ref:
                return elem
        return None

    @staticmethod
    def _click_blocked_by_overlay(action: Any, result: Any) -> bool:
        if action.command not in {"click", "dblclick", "check", "uncheck"}:
            return False
        stderr = (getattr(result, "stderr", "") or "").lower()
        if "intercepts pointer events" not in stderr:
            return False
        return any(token in stderr for token in ("overlay", "dialog", "modal"))

    @staticmethod
    def _drag_failed_by_cli_schema(action: Any, result: Any) -> bool:
        if action.command != "drag":
            return False
        stderr = getattr(result, "stderr", "") or ""
        return "startElement" in stderr and "endElement" in stderr

    @staticmethod
    def _drag_mouse_fallback_command(action: Any, elements: list[Any]) -> str | None:
        if action.command != "drag" or len(action.args) < 2:
            return None

        source = _element_by_ref(elements, action.args[0])
        target = _element_by_ref(elements, action.args[1])
        source_label = _drag_label(source)
        target_label = _drag_label(target)
        if not source_label or not target_label:
            return None

        code = f"""async page => {{
  const sourceLabel = {json.dumps(source_label)};
  const targetLabel = {json.dumps(target_label)};
  const sourcePreferLast = {json.dumps(_prefer_last_matching_dom_node(source))};
  const targetPreferLast = {json.dumps(_prefer_last_matching_dom_node(target))};
  const {{ source, target }} = await page.evaluate((args) => {{
    const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
    const key = value => normalize(value).replace(/\\s/g, '');
    const candidatesFor = (label) => {{
      const wanted = key(label);
      return Array.from(document.querySelectorAll('body *'))
        .map(el => {{
          const rect = el.getBoundingClientRect();
          return {{
            rect: {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }},
            text: normalize(el.textContent),
          }};
        }})
        .filter(item =>
          item.text &&
          item.rect.width > 5 &&
          item.rect.height > 5 &&
          key(item.text) === wanted
        );
    }};
    const pick = (candidates, label, preferLast, offset = 0) => {{
      if (!candidates.length) {{
        throw new Error(`No visible element found for ${{label}}`);
      }}
      const baseIndex = preferLast ? candidates.length - 1 : 0;
      const index = Math.max(0, Math.min(candidates.length - 1, baseIndex + offset));
      return candidates[index].rect;
    }};
    const sourceCandidates = candidatesFor(args.sourceLabel);
    const targetCandidates = candidatesFor(args.targetLabel);
    const sameLabel = key(args.sourceLabel) === key(args.targetLabel);
    return {{
      source: pick(
        sourceCandidates,
        args.sourceLabel,
        args.sourcePreferLast,
        sameLabel && sourceCandidates.length > 1 && args.sourcePreferLast ? -1 : 0
      ),
      target: pick(targetCandidates, args.targetLabel, args.targetPreferLast),
    }};
  }}, {{ sourceLabel, targetLabel, sourcePreferLast, targetPreferLast }});
  const sx = source.x + source.width / 2;
  const sy = source.y + source.height / 2;
  const tx = target.x + target.width / 2;
  const ty = target.y + target.height / 2;
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(tx, ty, {{ steps: 20 }});
  await page.mouse.up();
  await page.waitForTimeout(800);
  return `Dragged ${{sourceLabel}} to ${{targetLabel}}`;
}}"""
        return "playwright-cli run-code " + shlex.quote(code)

    @staticmethod
    def _click_route_entry(element: Any) -> dict[str, str]:
        return {
            "ref": element.ref,
            "label": _label_from_description(element.description),
            "description": element.description,
            "href": element.url,
        }

    def _copy_debug_traces(self) -> list[Path]:
        source_root = Path(".playwright-cli/traces")
        if not source_root.exists():
            return []

        copied: list[Path] = []
        threshold = max(self._debug_started_at - 1.0, 0.0)
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            try:
                if source.stat().st_mtime < threshold:
                    continue
            except OSError:
                continue
            relative = source.relative_to(source_root)
            destination = self.paths.traces / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(destination)
        return copied

    @staticmethod
    def _format_command_result(label: str, result: Any) -> str:
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return (
            f"Command result ({label}): rc={result.returncode} "
            f"stdout_len={len(stdout)} stderr_len={len(stderr)}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )


def _label_from_description(description: str) -> str:
    match = re.search(r'"([^"]+)"', description or "")
    return match.group(1) if match else (description or "")


def _element_by_ref(elements: list[Any], ref: str) -> Any | None:
    for element in elements:
        if element.ref == ref:
            return element
    return None


def _drag_label(element: Any | None) -> str:
    if element is None:
        return ""
    child_text = (getattr(element, "child_text", "") or "").strip()
    if child_text:
        return re.sub(r"\s*\|\s*", " ", child_text).strip()

    description = (getattr(element, "description", "") or "").strip()
    if ":" in description:
        return description.rsplit(":", 1)[1].strip()
    return _label_from_description(description).strip()


def _prefer_last_matching_dom_node(element: Any | None) -> bool:
    if element is None:
        return False
    description = (getattr(element, "description", "") or "").strip().lower()
    child_text = (getattr(element, "child_text", "") or "").strip()
    return bool(child_text) and description in {"generic", "generic [cursor=pointer]"}


def _current_tab_selection_noop(parsed_action: Any, snapshot_text: str) -> str:
    if parsed_action.command != "tab-select" or not parsed_action.args:
        return ""
    current_index = _current_tab_index(snapshot_text)
    if current_index is None or str(current_index) != str(parsed_action.args[0]):
        return ""
    return (
        f"Tab {current_index} is already the current tab. Selecting it again does not "
        "change page state. Choose a state-changing action instead: load the needed "
        "lookup URL in this tab with goto, switch to a different useful tab, or return "
        "to the task tab only when ready to edit visible task controls."
    )


def _current_tab_index(snapshot_text: str) -> int | None:
    for line in (snapshot_text or "").splitlines():
        match = re.search(r"^\s*-\s*(\d+):\s*\(current\)", line)
        if match:
            return int(match.group(1))
    return None


def _same_location(current_url: str, expected_url: str) -> bool:
    current = urlparse(current_url or "")
    expected = urlparse(expected_url or "")
    return (
        current.scheme in {"http", "https"}
        and expected.scheme in {"http", "https"}
        and current.netloc == expected.netloc
        and _normalized_path(current.path) == _normalized_path(expected.path)
    )


def _navigates_away_from_location(current_url: str, target_url: str) -> bool:
    current = urlparse(current_url or "")
    target = urlparse(target_url or "")
    if current.scheme not in {"http", "https"}:
        return False
    if target.scheme not in {"http", "https"}:
        return False
    return bool(target.netloc and not _same_location(current_url, target_url))


def _normalized_path(path: str) -> str:
    cleaned = path or "/"
    return cleaned.rstrip("/") or "/"


def fetch_public_text_url(url: str, max_chars: int = 12000) -> dict[str, Any]:
    """Fetch bounded text from an HTTP(S) URL for planner evidence."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "status": "error",
            "url": url,
            "error": "fetch_url only supports absolute HTTP/HTTPS URLs.",
        }
    if parsed.username or parsed.password:
        return {
            "status": "error",
            "url": url,
            "error": "fetch_url does not support URLs with embedded credentials.",
        }

    limit = _fetch_max_chars(max_chars)
    byte_limit = min(max(limit * 4, 4096), 200_000)
    request = Request(
        url,
        headers={
            "User-Agent": "browser-agent/1.0",
            "Accept": "text/*, application/json, application/xml, image/svg+xml, */*;q=0.5",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - user-directed URL fetch
            raw = response.read(byte_limit + 1)
            content_type = response.headers.get("content-type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
            status_code = getattr(response, "status", 0)
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        return {"status": "error", "url": url, "error": str(exc)}

    raw_truncated = len(raw) > byte_limit
    raw = raw[:byte_limit]
    if _looks_binary(raw, content_type):
        return {
            "status": "error",
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "status_code": status_code,
            "error": "Fetched response does not look like text.",
        }

    text = raw.decode(charset, errors="replace")
    text_truncated = len(text) > limit
    text = text[:limit]
    return {
        "status": "ok",
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "status_code": status_code,
        "text": text,
        "chars": len(text),
        "truncated": raw_truncated or text_truncated,
    }


def _fetch_max_chars(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 12000
    return min(max(parsed, 1000), 20000)


def _looks_binary(raw: bytes, content_type: str) -> bool:
    lowered = (content_type or "").lower()
    if any(token in lowered for token in ("text/", "json", "xml", "svg", "javascript")):
        return False
    if b"\x00" in raw[:1024]:
        return True
    if not raw:
        return False
    control = sum(1 for byte in raw[:1024] if byte < 9 or 13 < byte < 32)
    return control / min(len(raw), 1024) > 0.1


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _planner_error_is_quota(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("429", "quota", "rate limit", "usage limit", "try again at")
    )

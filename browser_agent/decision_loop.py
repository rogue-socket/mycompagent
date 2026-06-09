"""Main decision loop for the browser agent."""

from __future__ import annotations

import ast
import hashlib
import html
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


@dataclass(slots=True)
class FailedUrlAttempt:
    url: str
    error: str


@dataclass(slots=True)
class SuccessfulFetchAttempt:
    url: str
    max_chars: int
    truncated: bool


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
        self.last_status_indicators: dict[str, str] = {}
        self.last_active_editable_text: str | None = None
        self.last_observation: str | None = None
        self.short_text_retries = 0
        self.interstitial_wait_retries = 0
        self.last_step_error: str | None = None
        self.last_domain: str | None = None
        self._domain_context: str | None = None
        self._debug_started_at = 0.0
        self.clicked_route: list[dict[str, str]] = []
        self.finish_output: str | None = None
        self.task_contract = build_task_contract(task)
        self.evidence_ledger = EvidenceLedger()
        self.failed_url_attempts: list[FailedUrlAttempt] = []
        self.successful_fetch_attempts: list[SuccessfulFetchAttempt] = []
        self.recent_text_asset_urls: list[str] = []
        self.unavailable_human_input_questions: set[str] = set()

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
                self._record_text_asset_urls(interpreter_state.dom_evidence)
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

                interstitial_warning = _transient_interstitial_warning(interpreter_state)
                if interstitial_warning:
                    max_waits = int(self.config.get("max_interstitial_waits", 5))
                    if self.interstitial_wait_retries < max_waits and self.step < int(
                        self.config.get("max_steps", 50)
                    ):
                        self.interstitial_wait_retries += 1
                        wait_seconds = float(
                            self.config.get("interstitial_wait_seconds", 2.0)
                        )
                        self._log(
                            "Step "
                            f"{self.step}: {interstitial_warning}; waiting "
                            f"{wait_seconds:.1f}s before replanning"
                        )
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "auto-wait interstitial",
                                "approval_status": "auto_recovery",
                                "execution_result": "ok",
                                "reason": "transient_interstitial",
                                "wait_seconds": wait_seconds,
                            },
                        )
                        self.action_history.append("auto-wait interstitial")
                        time.sleep(wait_seconds)
                        continue
                else:
                    self.interstitial_wait_retries = 0

                current_active_editable_text = _active_editable_text_from_dom_evidence(
                    interpreter_state.dom_evidence
                )
                current_status_indicators = _status_indicators_from_dom_evidence(
                    interpreter_state.dom_evidence
                )
                self.last_observation = _post_action_observation_note(
                    self.last_status_indicators,
                    current_status_indicators,
                    self.last_active_editable_text,
                    current_active_editable_text,
                    self.last_action_ok,
                )
                status_regression_warning = _status_regression_warning(
                    self.last_status_indicators,
                    current_status_indicators,
                    self.last_action_ok,
                )
                self.last_status_indicators = current_status_indicators
                self.last_active_editable_text = current_active_editable_text
                if status_regression_warning:
                    self.last_step_error = (
                        f"{self.last_step_error}\n\n{status_regression_warning}"
                        if self.last_step_error
                        else status_regression_warning
                    )

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
                    last_observation=self.last_observation,
                    domain_context=self._domain_context,
                    task=self.task,
                    evidence_text=snapshot_state.raw_text,
                    task_context=task_context,
                )
                self.last_step_error = None
                self.last_observation = None
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
                    source_token_warning = _source_token_human_input_warning(
                        question,
                        reason,
                        getattr(interpreter_state, "dom_evidence", "") or "",
                    )
                    if source_token_warning:
                        self.last_step_error = source_token_warning
                        self._log(
                            "Step "
                            f"{self.step}: ask_human skipped — source token evidence available"
                        )
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "ask_human",
                                "approval_status": "n/a",
                                "execution_result": "skipped",
                                "reason": "source_token_available",
                                "question": question,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("ask_human skipped: " + question)
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": source_token_warning},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    repeated_human_input_warning = (
                        self._repeated_unavailable_human_input_warning(question)
                    )
                    if repeated_human_input_warning:
                        self.last_step_error = repeated_human_input_warning
                        self._log(
                            f"Step {self.step}: ask_human skipped — "
                            "same unavailable question already failed"
                        )
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "ask_human",
                                "approval_status": "stdin_unavailable",
                                "execution_result": "skipped",
                                "reason": "human_input_unavailable_repeated",
                                "question": question,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("ask_human skipped: " + question)
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {
                                    "status": "error",
                                    "error": repeated_human_input_warning,
                                },
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    if not self._human_input_allowed():
                        error = (
                            "Human input was requested, but it is disabled for this run. "
                            "Run with --ask-human in auto mode, use safe/hybrid mode, "
                            "or continue from page evidence/public lookup without asking "
                            "the same question again in this run."
                        )
                        self._record_unavailable_human_question(question)
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

                    operator_question = _human_question_with_visual_context(
                        question,
                        reason,
                        getattr(interpreter_state, "dom_evidence", "") or "",
                    )
                    self._log(f"Step {self.step}: awaiting human input — {operator_question}")
                    try:
                        answer = self.human_input(
                            f"\n[BrowserAgent] {operator_question}\n> "
                        ).strip()
                    except EOFError:
                        error = (
                            "Human input was requested, but stdin is not available. "
                            "Do not ask the same question again in this run; use "
                            "available page evidence, public lookup, or finish with a "
                            "precise blocked reason. Run in an interactive terminal for "
                            "operator-visible values."
                        )
                        self._record_unavailable_human_question(question)
                        self.last_step_error = error
                        self._log(f"Step {self.step}: ask_human skipped — {error}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "ask_human",
                                "approval_status": "stdin_unavailable",
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
                    source_token_fetch_warning = _source_token_fetch_warning(
                        url,
                        getattr(interpreter_state, "dom_evidence", "") or "",
                    )
                    if source_token_fetch_warning:
                        self.last_step_error = source_token_fetch_warning
                        self._log(f"Step {self.step}: fetch_url skipped - {url}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "fetch_url",
                                "approval_status": "n/a",
                                "execution_result": "skipped",
                                "reason": "source_token_available",
                                "url": url,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("fetch_url skipped: " + url)
                        self.last_action_ok = False
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": source_token_fetch_warning},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    failed_url_warning = self._failed_url_retry_warning(url)
                    if failed_url_warning:
                        self.last_step_error = failed_url_warning
                        self._log(f"Step {self.step}: fetch_url skipped - {url}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "fetch_url",
                                "approval_status": "n/a",
                                "execution_result": "skipped",
                                "reason": "recent_failed_url",
                                "url": url,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("fetch_url skipped: " + url)
                        self.last_action_ok = False
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": failed_url_warning},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    max_chars = _fetch_max_chars(tool_result.tool_args.get("max_chars"))
                    repeated_truncated_fetch = self._repeated_truncated_fetch_warning(
                        url,
                        max_chars,
                    )
                    if repeated_truncated_fetch:
                        self.last_step_error = repeated_truncated_fetch
                        self._log(f"Step {self.step}: fetch_url skipped - {url}")
                        append_jsonl(
                            self.paths.actions_log,
                            {
                                "step": self.step,
                                "command": "fetch_url",
                                "approval_status": "n/a",
                                "execution_result": "skipped",
                                "reason": "repeated_truncated_fetch",
                                "url": url,
                                "planner_latency_seconds": tool_result.latency_seconds,
                            },
                        )
                        self.action_history.append("fetch_url skipped: " + url)
                        self.last_action_ok = False
                        try:
                            self.planner.send_tool_result(
                                tool_result.tool_name,
                                {"status": "error", "error": repeated_truncated_fetch},
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    fetch_result = self.url_fetcher(url, max_chars)
                    status = str(fetch_result.get("status", "error"))
                    self.last_action_ok = status == "ok"
                    if status != "ok":
                        error = str(fetch_result.get("error") or "fetch_url failed")
                        self.last_step_error = error
                        self._record_failed_url(url, error)
                    else:
                        self._clear_failed_url_attempts(url)
                        self._record_successful_fetch(
                            url,
                            max_chars,
                            bool(fetch_result.get("truncated", False)),
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
                            "text_format": fetch_result.get("text_format", ""),
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

                stale_ref_warning = _missing_action_ref_warning(
                    parsed_action,
                    snapshot_state.elements,
                    interpreter_state.url,
                )
                if stale_ref_warning:
                    self.last_step_error = stale_ref_warning
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "ref_not_in_current_snapshot",
                            "current_url": interpreter_state.url,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                same_value_fill = _same_value_fill_warning(
                    parsed_action,
                    snapshot_state.elements,
                    getattr(interpreter_state, "dom_evidence", "") or "",
                )
                if same_value_fill:
                    self.last_step_error = same_value_fill
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "fill_value_already_current",
                            "current_url": interpreter_state.url,
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

                redundant_blank_tab = _existing_blank_tab_warning(
                    parsed_action,
                    snapshot_state.raw_text,
                )
                if redundant_blank_tab:
                    self.last_step_error = redundant_blank_tab
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "blank_tab_already_available",
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                interstitial_reload = _interstitial_reload_warning(
                    parsed_action,
                    interpreter_state,
                )
                if interstitial_reload:
                    self.last_step_error = interstitial_reload
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "transient_interstitial_reload",
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                missing_text_target = self._ungrounded_text_input_warning(
                    parsed_action,
                    interpreter_state,
                )
                if missing_text_target:
                    self.last_step_error = missing_text_target
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "no_editable_text_target",
                            "current_url": interpreter_state.url,
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

                text_asset_navigation = self._text_asset_navigation_warning(
                    parsed_action,
                    interpreter_state,
                )
                if text_asset_navigation:
                    self.last_step_error = text_asset_navigation
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "text_asset_navigation",
                            "current_url": interpreter_state.url,
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                failed_url_navigation = self._failed_url_navigation_warning(parsed_action)
                if failed_url_navigation:
                    self.last_step_error = failed_url_navigation
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "recent_failed_url",
                            "planner_latency_seconds": tool_result.latency_seconds,
                        },
                    )
                    self.action_history.append(parsed_action.action)
                    self.last_action_ok = False
                    continue

                speculative_edit = self._speculative_variant_edit_warning(
                    parsed_action,
                    interpreter_state,
                    tool_result.reasoning_text,
                )
                if speculative_edit:
                    self.last_step_error = speculative_edit
                    append_jsonl(
                        self.paths.actions_log,
                        {
                            "step": self.step,
                            "command": parsed_action.action,
                            "approval_status": "n/a",
                            "execution_result": "skipped",
                            "reason": "speculative_variant_edit",
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
                if exec_status == "ok":
                    if parsed_action.command == "goto" and parsed_action.args:
                        self._clear_failed_url_attempts(parsed_action.args[0])
                    if exec_result.stdout:
                        result_payload["output"] = exec_result.stdout[:2000]
                elif exec_status != "ok":
                    result_payload["error"] = (exec_result.stderr or "command failed")[:500]
                    self.last_step_error = result_payload["error"]
                    if parsed_action.command == "goto" and parsed_action.args:
                        self._record_failed_url(parsed_action.args[0], result_payload["error"])
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

    def _text_asset_navigation_warning(
        self,
        parsed_action: Any,
        interpreter_state: Any,
    ) -> str:
        if parsed_action.command != "goto" or not parsed_action.args:
            return ""
        target_url = parsed_action.args[0]
        if not _looks_like_public_text_asset_url(target_url):
            return ""
        was_recently_observed = (
            _normalized_url_for_retry(target_url) in self.recent_text_asset_urls
        )
        if not was_recently_observed and not (
            _navigates_away_from_location(interpreter_state.url, target_url)
            and self._has_stateful_task_controls(interpreter_state)
        ):
            return ""
        return (
            "Text-asset navigation guard: the current page has active form/editable "
            "state or recently exposed this target URL, and the target URL looks "
            "like a public text-like asset. Do not navigate a browser tab to inspect "
            "that asset because it can lose task context or waste a browser step. "
            "Use fetch_url for the asset, or open/switch to a lookup tab before "
            "navigating, then return to the task tab before editing visible controls."
        )

    def _has_stateful_task_controls(self, interpreter_state: Any) -> bool:
        if "active_editable" in (getattr(interpreter_state, "dom_evidence", "") or ""):
            return True
        return any(
            getattr(element, "element_type", "") == "input"
            for element in getattr(interpreter_state, "clickable_elements", [])
        )

    def _ungrounded_text_input_warning(
        self,
        parsed_action: Any,
        interpreter_state: Any,
    ) -> str:
        if parsed_action.command != "type" or not parsed_action.args:
            return ""
        text = str(parsed_action.args[0] or "")
        if not text.strip():
            return ""
        if self._has_stateful_task_controls(interpreter_state):
            return ""
        return (
            "Text input target guard: the current page state does not expose an "
            "active editable or input control. Do not type text into the active page. "
            "Switch back to the tab that contains the task form, click or focus a "
            "visible textbox, or use fill with a current visible ref before entering "
            "text."
        )

    def _recent_lookup_workflow(self) -> bool:
        recent = self.action_history[-10:]
        has_tab_action = any(
            action.startswith(("playwright-cli tab-new", "playwright-cli tab-select"))
            for action in recent
        )
        has_navigation = any(action.startswith("playwright-cli goto ") for action in recent)
        return has_tab_action and has_navigation

    def _record_failed_url(self, url: str, error: str) -> None:
        if not url:
            return
        self.failed_url_attempts.append(FailedUrlAttempt(url=url, error=error))
        self.failed_url_attempts = self.failed_url_attempts[-20:]

    def _clear_failed_url_attempts(self, url: str) -> None:
        parsed = urlparse(url or "")
        if not parsed.netloc:
            return
        self.failed_url_attempts = [
            failed
            for failed in self.failed_url_attempts
            if urlparse(failed.url or "").netloc != parsed.netloc
        ]

    def _record_successful_fetch(
        self,
        url: str,
        max_chars: int,
        truncated: bool,
    ) -> None:
        if not url:
            return
        self.successful_fetch_attempts.append(
            SuccessfulFetchAttempt(
                url=url,
                max_chars=max_chars,
                truncated=truncated,
            )
        )
        self.successful_fetch_attempts = self.successful_fetch_attempts[-40:]

    def _repeated_truncated_fetch_warning(self, url: str, max_chars: int) -> str:
        normalized = _normalized_url_for_retry(url)
        if not normalized:
            return ""
        for attempt in reversed(self.successful_fetch_attempts):
            if _normalized_url_for_retry(attempt.url) != normalized:
                continue
            if not attempt.truncated or max_chars > attempt.max_chars:
                return ""
            return (
                "Repeated truncated fetch guard: this URL already returned truncated "
                f"content with max_chars={attempt.max_chars}. Re-fetching the same "
                "URL with the same or smaller character budget will not add evidence. "
                "Increase max_chars, choose a narrower/fetchable text source, use a "
                "browser lookup page in a helper tab, or act on the evidence already "
                "returned."
            )
        return ""

    def _record_text_asset_urls(self, dom_evidence: str) -> None:
        for url in _text_asset_urls_from_dom_evidence(dom_evidence):
            normalized = _normalized_url_for_retry(url)
            if normalized in self.recent_text_asset_urls:
                continue
            self.recent_text_asset_urls.append(normalized)
        self.recent_text_asset_urls = self.recent_text_asset_urls[-20:]

    def _failed_url_retry_warning(self, url: str) -> str:
        failed = _matching_failed_url(url, self.failed_url_attempts)
        if failed is None:
            return ""
        return (
            "Recent URL failure guard: this URL or host already failed during this run "
            f"({failed.error}). Do not retry the same failed source or navigate to that "
            "unusable host. Use a different source, a search/result page, visible page "
            "evidence, official documentation for the source, or a fetchable public text "
            "page instead."
        )

    def _failed_url_navigation_warning(self, parsed_action: Any) -> str:
        if parsed_action.command != "goto" or not parsed_action.args:
            return ""
        return self._failed_url_retry_warning(parsed_action.args[0])

    def _speculative_variant_edit_warning(
        self,
        parsed_action: Any,
        interpreter_state: Any,
        reasoning_text: str,
    ) -> str:
        if parsed_action.command not in {"fill", "type"}:
            return ""
        if not _has_unresolved_status(interpreter_state):
            return ""
        recent = self.action_history[-6:]
        if _latest_fetch_url_status(recent) != "error":
            return ""
        if not _looks_speculative(reasoning_text):
            return ""
        return (
            "Speculative variant edit guard: the page still shows an unresolved "
            "requirement and recent evidence lookup failed. Do not try another guessed "
            "candidate value, swap a suffix, or bulk-insert possible answers. Gather "
            "stronger evidence first: use browser lookup in a separate tab, fetch a "
            "visible/public text source, inspect page evidence, or ask the human only "
            "if the value is operator-visible and cannot be recovered."
        )

    def _human_input_allowed(self) -> bool:
        return bool(self.config.get("allow_human_input", self.mode != "auto"))

    def _record_unavailable_human_question(self, question: str) -> None:
        normalized = _normalize_human_input_question(question)
        if normalized:
            self.unavailable_human_input_questions.add(normalized)

    def _repeated_unavailable_human_input_warning(self, question: str) -> str:
        normalized = _normalize_human_input_question(question)
        if not normalized or normalized not in self.unavailable_human_input_questions:
            return ""
        return (
            "Human input target guard: this exact human-input request already failed "
            "because human input is unavailable in this run. Do not ask it again. "
            "Use current page evidence, fetchable public text evidence, browser lookup "
            "in a separate tab, or finish with a precise blocked reason naming the "
            "missing operator-visible value."
        )

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


def _missing_action_ref_warning(
    parsed_action: Any,
    elements: list[Any],
    current_url: str,
) -> str:
    missing_refs = [
        ref for ref in _action_refs(parsed_action) if _element_by_ref(elements, ref) is None
    ]
    if not missing_refs:
        return ""
    refs = ", ".join(missing_refs)
    return (
        f"Stale element-ref guard: ref(s) {refs} are not present in the current "
        f"page snapshot at {current_url or 'the current page'}. Do not execute this "
        "action. Use only refs visible in the current page state; if the intended "
        "control is on another tab or page, switch back or navigate there first, "
        "then use the fresh refs from that page."
    )


def _same_value_fill_warning(
    parsed_action: Any,
    elements: list[Any],
    dom_evidence: str,
) -> str:
    if parsed_action.command != "fill" or len(parsed_action.args) < 2:
        return ""
    target = _element_by_ref(elements, parsed_action.args[0])
    if target is None:
        return ""
    current_value = _editable_value_from_element(target)
    if current_value is None and _element_is_active(target):
        current_value = _active_editable_text_from_dom_evidence(dom_evidence)
    if current_value is None:
        return ""
    intended_value = _normalize_observed_value(str(parsed_action.args[1]))
    if not intended_value or intended_value != current_value:
        return ""
    return (
        "No-op fill guard: the target editable already contains the exact value "
        "you are trying to fill. Do not repeat the same fill. Make a specific "
        "value-changing edit, gather missing evidence, or finish if the current "
        "value already satisfies the task."
    )


def _element_is_active(element: Any) -> bool:
    metadata = tuple(str(item).lower() for item in getattr(element, "metadata", ()) or ())
    if "active" in metadata or "focused" in metadata:
        return True
    description = (getattr(element, "description", "") or "").lower()
    return "[active]" in description or "[focused]" in description


def _editable_value_from_element(element: Any) -> str | None:
    description = (getattr(element, "description", "") or "").strip()
    if not re.search(r"\b(textbox|input|combobox|textarea)\b", description, re.I):
        return None
    match = re.search(r":\s*(['\"])(.*?)\1\s*$", description)
    if not match:
        return None
    value = _normalize_observed_value(match.group(2))
    return value or None


def _action_refs(parsed_action: Any) -> list[str]:
    if parsed_action.command in {
        "click",
        "dblclick",
        "hover",
        "check",
        "uncheck",
        "fill",
        "select",
        "upload",
    }:
        return parsed_action.args[:1]
    if parsed_action.command == "drag":
        return parsed_action.args[:2]
    return []


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


def _existing_blank_tab_warning(parsed_action: Any, snapshot_text: str) -> str:
    if parsed_action.command != "tab-new":
        return ""
    blank_index = _first_blank_tab_index(snapshot_text)
    if blank_index is None:
        return ""
    current_index = _current_tab_index(snapshot_text)
    if current_index == blank_index:
        return (
            f"Blank tab {blank_index} is already the current tab. Creating another "
            "blank tab only adds tab clutter. Load the needed lookup URL in this "
            "current blank tab with goto, or switch back to the task tab if lookup "
            "work is complete."
        )
    return (
        f"Blank tab {blank_index} already exists. Do not open another blank tab. "
        f"Select tab {blank_index} for lookup work, or use an existing loaded "
        "lookup tab before returning to task controls."
    )


def _first_blank_tab_index(snapshot_text: str) -> int | None:
    for line in (snapshot_text or "").splitlines():
        match = re.search(r"^\s*-\s*(\d+):.*\]\(about:blank\)", line)
        if match:
            return int(match.group(1))
    return None


def _current_tab_index(snapshot_text: str) -> int | None:
    for line in (snapshot_text or "").splitlines():
        match = re.search(r"^\s*-\s*(\d+):\s*\(current\)", line)
        if match:
            return int(match.group(1))
    return None


def _has_unresolved_status(interpreter_state: Any) -> bool:
    haystack = (
        f"{getattr(interpreter_state, 'visible_text', '')[:1600]}\n"
        f"{getattr(interpreter_state, 'dom_evidence', '')[:1600]}"
    ).lower()
    return any(
        marker in haystack
        for marker in (
            "status='error'",
            'status="error"',
            "invalid",
            "must",
            "required",
            "requires",
            "requirement",
            "rule",
        )
    )


def _status_indicators_from_dom_evidence(dom_evidence: str) -> dict[str, str]:
    indicators: dict[str, str] = {}
    for line in (dom_evidence or "").splitlines():
        status = _quoted_dom_field(line, "status")
        if status not in {"success", "error"}:
            continue
        label = _normalize_status_label(_quoted_dom_field(line, "nearby"))
        if label:
            indicators[label] = status
    return indicators


def _normalize_status_label(label: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(label or "")).strip()[:240]


def _active_editable_text_from_dom_evidence(dom_evidence: str) -> str | None:
    for line in (dom_evidence or "").splitlines():
        if "active_editable:" not in line:
            continue
        text = _quoted_dom_field(line, "text")
        if text:
            return _normalize_observed_value(text)
    return None


def _quoted_dom_field(line: str, field: str) -> str:
    match = re.search(
        rf"\b{re.escape(field)}=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
        line or "",
    )
    if not match:
        return ""
    raw_value = match.group(1)
    try:
        return str(ast.literal_eval(raw_value))
    except (SyntaxError, ValueError):
        return raw_value[1:-1]


def _normalize_observed_value(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()[:240]


def _post_action_observation_note(
    previous_statuses: dict[str, str],
    current_statuses: dict[str, str],
    previous_value: str | None,
    current_value: str | None,
    last_action_ok: bool,
) -> str:
    if not last_action_ok:
        return ""

    lines: list[str] = []
    if previous_value is not None and current_value is not None:
        if previous_value != current_value:
            lines.append(
                "Editable value changed from "
                f"{previous_value!r} to {current_value!r}."
            )

    newly_satisfied = _status_transition_labels(
        previous_statuses,
        current_statuses,
        from_status="error",
        to_status="success",
    )
    regressed = _status_transition_labels(
        previous_statuses,
        current_statuses,
        from_status="success",
        to_status="error",
    )
    newly_visible_failing = [
        label
        for label, status in current_statuses.items()
        if status == "error" and label not in previous_statuses
    ]

    if newly_satisfied:
        lines.append("Newly satisfied statuses: " + "; ".join(newly_satisfied[:5]) + ".")
    if regressed:
        lines.append("Regressed statuses: " + "; ".join(regressed[:5]) + ".")
    if newly_visible_failing:
        lines.append(
            "Newly visible failing statuses: "
            + "; ".join(newly_visible_failing[:5])
            + "."
        )

    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)


def _status_transition_labels(
    previous: dict[str, str],
    current: dict[str, str],
    *,
    from_status: str,
    to_status: str,
) -> list[str]:
    return [
        label
        for label, previous_status in previous.items()
        if previous_status == from_status and current.get(label) == to_status
    ]


def _status_regression_warning(
    previous: dict[str, str],
    current: dict[str, str],
    last_action_ok: bool,
) -> str:
    if not last_action_ok or not previous or not current:
        return ""
    regressed = [
        label
        for label, previous_status in previous.items()
        if previous_status == "success" and current.get(label) == "error"
    ]
    if not regressed:
        return ""
    labels = "; ".join(regressed[:5])
    return (
        "Status regression guard: these requirements were previously satisfied but "
        f"are now failing: {labels}. The last edit likely changed a value that "
        "another visible status also depends on. Undo or revise the last edit, then "
        "choose the smallest candidate that satisfies the new requirement while "
        "preserving the regressed statuses."
    )


def _normalize_human_input_question(question: str) -> str:
    return re.sub(r"\s+", " ", (question or "").strip().lower())


def _transient_interstitial_warning(interpreter_state: Any) -> str:
    if getattr(interpreter_state, "clickable_elements", []):
        return ""
    url = (getattr(interpreter_state, "url", "") or "").lower()
    if url.startswith("about:"):
        return ""
    title = (getattr(interpreter_state, "title", "") or "").lower()
    visible_text = (getattr(interpreter_state, "visible_text", "") or "").lower()
    page_summary = (getattr(interpreter_state, "page_summary", "") or "").lower()
    haystack = f"{title}\n{visible_text[:1200]}\n{page_summary[:600]}"
    markers = (
        "just a moment",
        "checking if the site connection is secure",
        "checking your browser",
        "verify you are human",
        "verifying you are human",
        "please stand by",
        "please wait while",
    )
    if any(marker in haystack for marker in markers):
        return (
            "Transient interstitial/loading page detected. Wait for the page to "
            "settle instead of repeatedly reloading or spending planner calls."
        )
    return ""


def _interstitial_reload_warning(parsed_action: Any, interpreter_state: Any) -> str:
    if parsed_action.command != "reload":
        return ""
    if not _transient_interstitial_warning(interpreter_state):
        return ""
    return (
        "Transient interstitial reload guard: the current page still looks like a "
        "temporary loading or verification interstitial. Reloading usually restarts "
        "the challenge and wastes a browser action. Wait for the page to settle, use "
        "a non-reload readiness check, or finish with a precise blocked reason if the "
        "interstitial does not clear."
    )


def _source_token_human_input_warning(
    question: str,
    reason: str,
    dom_evidence: str,
) -> str:
    if not _looks_like_short_visual_value_request(question, reason):
        return ""
    tokens = _short_source_tokens(dom_evidence)
    if not tokens:
        return ""
    token_list = ", ".join(repr(token) for token in tokens[:3])
    return (
        "Human input guard: current page evidence already exposes short source "
        f"token(s) near image evidence: {token_list}. Before asking the operator, "
        "try the relevant token as page evidence for the short visual value, then "
        "verify the visible requirement/status. Ask the human only if that evidence "
        "is rejected or ambiguous."
    )


def _human_question_with_visual_context(
    question: str,
    reason: str,
    dom_evidence: str,
) -> str:
    if not _looks_like_short_visual_value_request(question, reason):
        return question
    snippets = _visual_evidence_snippets(dom_evidence)
    if not snippets:
        return question
    evidence = "\n".join(f"- {snippet}" for snippet in snippets[:3])
    return f"{question}\n\nRelevant page evidence:\n{evidence}"


def _visual_evidence_snippets(dom_evidence: str) -> list[str]:
    snippets: list[str] = []
    for line in (dom_evidence or "").splitlines():
        if "- image:" not in line and "- iframe:" not in line:
            continue
        kind = "iframe" if "- iframe:" in line else "image"
        parts = [kind]
        for field in ("src", "src_token", "alt", "title", "aria", "nearby"):
            value = _normalize_observed_value(_quoted_dom_field(line, field))
            if value:
                parts.append(f"{field}={value!r}")
        if len(parts) > 1:
            snippet = " ".join(parts)
            if snippet not in snippets:
                snippets.append(snippet[:400])
    return snippets


def _source_token_fetch_warning(url: str, dom_evidence: str) -> str:
    if not _looks_like_binary_image_url(url):
        return ""
    tokens = _short_source_tokens(dom_evidence)
    if not tokens:
        return ""
    filename_token = _short_filename_token(url)
    if filename_token and filename_token in tokens:
        tokens = [filename_token] + [token for token in tokens if token != filename_token]
    token_list = ", ".join(repr(token) for token in tokens[:3])
    return (
        "Binary visual asset guard: fetch_url is for text-like public assets and "
        "cannot read binary images. Current page evidence already exposes short "
        f"source token(s) near image evidence: {token_list}. If the visible "
        "requirement asks for a short visual code/text from that image, try the "
        "relevant token as page evidence and verify the status. Ask the human only "
        "if the token is rejected or ambiguous and human input is enabled."
    )


def _looks_like_short_visual_value_request(question: str, reason: str) -> bool:
    text = f"{question}\n{reason}".lower()
    visual_markers = (
        "visual",
        "image",
        "shown",
        "displayed",
        "appears",
        "visible",
    )
    value_markers = (
        "code",
        "text",
        "characters",
        "chars",
        "letters",
        "value",
        "string",
    )
    return any(marker in text for marker in visual_markers) and any(
        marker in text for marker in value_markers
    )


def _short_source_tokens(dom_evidence: str) -> list[str]:
    tokens: list[str] = []
    for line in (dom_evidence or "").splitlines():
        for token in (
            _valid_short_source_token(_quoted_dom_field(line, "src_token")),
            _short_filename_token(_quoted_dom_field(line, "src")),
        ):
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def _valid_short_source_token(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,16}", token or ""):
        return ""
    if token.lower() in {"error", "checkmark", "refresh", "title", "default"}:
        return ""
    return token


def _looks_like_binary_image_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    return any(
        path.endswith(extension)
        for extension in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp")
    )


def _short_filename_token(url: str) -> str:
    if url and not _looks_like_binary_image_url(url):
        return ""
    stem = Path(urlparse(url or "").path).stem
    return _valid_short_source_token(stem)


def _looks_speculative(reasoning_text: str) -> bool:
    lowered = (reasoning_text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "guess",
            "candidate",
            "most likely",
            "likely",
            "try ",
            "if this fails",
            "swap",
            "next most",
        )
    )


def _latest_fetch_url_status(actions: list[str]) -> str:
    for action in reversed(actions):
        if action.startswith("fetch_url ok:"):
            return "ok"
        if action.startswith("fetch_url error:"):
            return "error"
    return ""


def _matching_failed_url(
    url: str,
    failed_attempts: list[FailedUrlAttempt],
) -> FailedUrlAttempt | None:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    normalized = _normalized_url_for_retry(url)
    same_host_client_failures: list[FailedUrlAttempt] = []
    for failed in reversed(failed_attempts):
        failed_parsed = urlparse(failed.url or "")
        if failed_parsed.scheme not in {"http", "https"} or not failed_parsed.netloc:
            continue
        if _normalized_url_for_retry(failed.url) == normalized:
            return failed
        if parsed.netloc != failed_parsed.netloc:
            continue
        if _host_wide_failure(failed.error):
            return failed
        if _client_error_failure(failed.error):
            same_host_client_failures.append(failed)
            if len(same_host_client_failures) >= 2:
                return failed
    return None


def _normalized_url_for_retry(url: str) -> str:
    parsed = urlparse(url or "")
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}?{parsed.query}"


def _host_wide_failure(error: str) -> bool:
    lowered = (error or "").lower()
    return any(
        marker in lowered
        for marker in (
            "name_not_resolved",
            "nodename nor servname",
            "temporary failure in name resolution",
            "failed to establish a new connection",
            "connection refused",
            "network is unreachable",
        )
    )


def _client_error_failure(error: str) -> bool:
    lowered = (error or "").lower()
    return bool(
        re.search(r"\bhttp error\s+(400|401|403|404|405|410|422|429)\b", lowered)
        or re.search(r"\b(status|status_code|status code)[=: ]+(400|401|403|404|405|410|422|429)\b", lowered)
    )


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


def _looks_like_public_text_asset_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    return any(
        path.endswith(extension)
        for extension in (
            ".svg",
            ".json",
            ".xml",
            ".txt",
            ".csv",
            ".tsv",
            ".md",
            ".yaml",
            ".yml",
        )
    )


def _text_asset_urls_from_dom_evidence(dom_evidence: str) -> list[str]:
    urls: list[str] = []
    for line in (dom_evidence or "").splitlines():
        for field in ("src", "href"):
            url = _quoted_dom_field(line, field)
            if not _looks_like_public_text_asset_url(url):
                continue
            if url not in urls:
                urls.append(url)
    return urls


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
    text_format = "text"
    if _looks_html_text(text, content_type):
        text = _html_to_readable_text(text)
        text_format = "html_text"
    elif _looks_svg_text(text, content_type):
        text = _svg_to_readable_text(text)
        text_format = "svg_text"
    elif _looks_json_text(text, content_type):
        text = _json_to_readable_text(text)
        text_format = "json_text"
    elif _looks_xml_text(text, content_type):
        text = _xml_to_readable_text(text)
        text_format = "xml_text"
    text_truncated = len(text) > limit
    text = text[:limit]
    return {
        "status": "ok",
        "url": url,
        "final_url": final_url,
        "content_type": content_type,
        "status_code": status_code,
        "text_format": text_format,
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


def _looks_html_text(text: str, content_type: str) -> bool:
    lowered_type = (content_type or "").lower()
    if "html" in lowered_type:
        return True
    return bool(re.search(r"<(?:html|head|body|title|script|style|div|span)\b", text[:2000], re.I))


def _looks_svg_text(text: str, content_type: str) -> bool:
    lowered_type = (content_type or "").lower()
    return "svg" in lowered_type or bool(re.search(r"<svg\b", text[:2000], re.I))


def _looks_json_text(text: str, content_type: str) -> bool:
    lowered_type = (content_type or "").lower()
    if "json" in lowered_type:
        return True
    stripped = (text or "").lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _looks_xml_text(text: str, content_type: str) -> bool:
    lowered_type = (content_type or "").lower()
    return "xml" in lowered_type or bool(re.search(r"<\?xml\b|<[A-Za-z][\w:.-]*(?:\s|>)", text[:2000]))


def _html_to_readable_text(text: str) -> str:
    cleaned = re.sub(r"(?is)<script\b.*?</script>", " ", text)
    cleaned = re.sub(r"(?is)<style\b.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?is)<noscript\b.*?</noscript>", " ", cleaned)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(?:p|div|section|article|li|tr|h[1-6])>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def _svg_to_readable_text(text: str) -> str:
    readable = _markup_to_readable_text(
        text,
        preferred_tags=("title", "desc", "text", "tspan"),
        include_attrs=("aria-label", "alt", "title", "id", "href", "xlink:href"),
    )
    lines = [line for line in readable.splitlines() if line.strip()]
    seen = set(lines)
    for summary in _svg_element_summaries(text):
        if summary in seen:
            continue
        seen.add(summary)
        lines.append(summary)
    return "\n".join(lines[:80])


def _xml_to_readable_text(text: str) -> str:
    return _markup_to_readable_text(
        text,
        preferred_tags=("title", "name", "label", "description", "desc", "text"),
        include_attrs=("aria-label", "alt", "title", "id", "name", "href"),
    )


def _json_to_readable_text(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    lines: list[str] = []
    for path, value in _json_leaf_values(data):
        rendered = _render_json_leaf(value)
        if rendered:
            lines.append(f"{path}: {rendered}")
        if len(lines) >= 120:
            break
    if lines:
        return "\n".join(lines)
    return json.dumps(data, ensure_ascii=True, sort_keys=True)[:12000]


def _json_leaf_values(data: Any, path: str = "$") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = f"{path}.{key}" if _json_path_key(str(key)) else f"{path}[{key!r}]"
            leaves.extend(_json_leaf_values(value, child_path))
            if len(leaves) >= 160:
                break
    elif isinstance(data, list):
        for index, value in enumerate(data[:80]):
            leaves.extend(_json_leaf_values(value, f"{path}[{index}]"))
            if len(leaves) >= 160:
                break
    else:
        leaves.append((path, data))
    return leaves


def _json_path_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))


def _render_json_leaf(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    rendered = re.sub(r"\s+", " ", str(value)).strip()
    if not rendered:
        return ""
    return rendered[:500]


def _markup_to_readable_text(
    text: str,
    *,
    preferred_tags: tuple[str, ...],
    include_attrs: tuple[str, ...],
) -> str:
    cleaned = re.sub(r"(?is)<script\b.*?</script>", " ", text)
    cleaned = re.sub(r"(?is)<style\b.*?</style>", " ", cleaned)
    snippets: list[str] = []
    seen: set[str] = set()

    for tag in preferred_tags:
        pattern = rf"(?is)<(?:[\w.-]+:)?{re.escape(tag)}\b[^>]*>(.*?)</(?:[\w.-]+:)?{re.escape(tag)}>"
        for match in re.finditer(pattern, cleaned):
            value = _strip_markup_text(match.group(1))
            if value and value not in seen:
                seen.add(value)
                snippets.append(value)

    for attr in include_attrs:
        pattern = rf"""\b{re.escape(attr)}\s*=\s*(['"])(.*?)\1"""
        for match in re.finditer(pattern, cleaned, re.I | re.S):
            value = html.unescape(match.group(2)).strip()
            value = re.sub(r"\s+", " ", value)
            if not value or len(value) > 240 or value in seen:
                continue
            seen.add(value)
            snippets.append(f"{attr}: {value}")

    if not snippets:
        stripped = _strip_markup_text(cleaned)
        if stripped:
            snippets.append(stripped)
    return "\n".join(snippets[:80])


def _svg_element_summaries(text: str) -> list[str]:
    cleaned = re.sub(r"(?is)<script\b.*?</script>", " ", text)
    cleaned = re.sub(r"(?is)<style\b.*?</style>", " ", cleaned)
    summaries: list[str] = []
    seen: set[str] = set()
    interesting_tags = ("use", "image", "text", "tspan", "symbol")
    useful_attrs = (
        "id",
        "class",
        "href",
        "xlink:href",
        "aria-label",
        "alt",
        "x",
        "y",
        "cx",
        "cy",
        "width",
        "height",
        "transform",
    )
    tag_pattern = re.compile(
        r"(?is)<(?:[\w.-]+:)?("
        + "|".join(re.escape(tag) for tag in interesting_tags)
        + r")\b([^>]*)>(.*?)</(?:[\w.-]+:)?\1>|<(?:[\w.-]+:)?("
        + "|".join(re.escape(tag) for tag in interesting_tags)
        + r")\b([^>]*)/?>"
    )
    for match in tag_pattern.finditer(cleaned):
        tag = (match.group(1) or match.group(4) or "").lower()
        attr_text = match.group(2) or match.group(5) or ""
        attr_values = _markup_attrs(attr_text, useful_attrs)
        inner = _strip_markup_text(match.group(3) or "")
        if inner and len(inner) <= 120:
            attr_values.append(("text", inner))
        if not attr_values:
            continue
        detail = " ".join(f"{name}={value!r}" for name, value in attr_values[:8])
        summary = f"{tag}: {detail}"
        if summary in seen:
            continue
        seen.add(summary)
        summaries.append(summary)
        if len(summaries) >= 60:
            break
    return summaries


def _markup_attrs(attr_text: str, attr_names: tuple[str, ...]) -> list[tuple[str, str]]:
    attrs: list[tuple[str, str]] = []
    for attr in attr_names:
        pattern = rf"""\b{re.escape(attr)}\s*=\s*(['"])(.*?)\1"""
        match = re.search(pattern, attr_text or "", re.I | re.S)
        if not match:
            continue
        value = html.unescape(match.group(2)).strip()
        value = re.sub(r"\s+", " ", value)
        if not value or len(value) > 240:
            continue
        attrs.append((attr, value))
    return attrs


def _strip_markup_text(text: str) -> str:
    stripped = re.sub(r"(?is)<[^>]+>", " ", text)
    stripped = html.unescape(stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _planner_error_is_quota(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("429", "quota", "rate limit", "usage limit", "try again at")
    )

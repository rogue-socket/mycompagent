# Browser Agent Architecture (Playwright CLI)

This document describes the architecture of the DOM-driven browser agent. It uses
Gemini's native function calling with multi-turn chat to control a browser via
Playwright CLI commands.

## Current Implementation Snapshot

Verified on 2026-05-27 in the `mycompagent` Conda environment:

- CLI import and `browser-agent --help` work after installing `requirements-browser-agent.txt` and `pip install -e .`.
- Runtime path is `main.py` -> `ConfigManager` -> skill load -> `MemoryStore` -> `ChatPlanner` -> `PlaywrightExecutor` -> `DecisionLoop`.
- LLM planning can use either Gemini native function calling (`ChatPlanner`) or the local `../codex-agent` wrapper (`CodexPlanner`) selected by `--llm-provider codex`.
- The loop records run artifacts under `runs/<run_id>/`, with snapshots and JSONL logs gitignored by default.
- `tool_definitions.py`, not `constants.py`, is the effective LLM tool surface for the current native function-calling path.

Current gaps found during review:

- Basic task run `runs/run_20260527T072054Z` opened `https://example.com/` and captured snapshots, but never reached a tool call because Gemini returned `API key expired`; current planner error classification now treats non-retryable auth/configuration failures as `stop_reason=configuration_error`.
- Codex-backed task run `runs/run_20260527T073125Z` completed the same task with `stop_reason=completed`; it returned a `finish` tool action after reading the page state.
- Dev test dependencies are declared in `requirements-dev.txt`.
- Full suite result after the Codex/browser, memory, planner configuration-error, article-link grouping, evidence-snippet, planner-logging, anchor-guard, redirect-note, taxonomy-guard, route-helper, grounded-route, route-optimization regression, Codex-history compaction, legacy-command clarification, structured-result, artifact-policy, memory dedupe, custom-combobox recovery, and ARIA combobox route-regression fixes: 112 passed.
- `constants.ALLOWED_COMMANDS` is explicitly documented as a legacy Playwright CLI registry; the active planner action surface is `tool_definitions.py`.
- Planner reasoning is persisted to `agent_reasoning.jsonl`. Screenshots remain explicit/on-demand artifacts under `screenshots/`; debug visual replay uses traces and `session.webm`.
- `DecisionLoop.run()` now returns `RunResult(stop_reason, finish_output, steps_used, grounded_route)` for orchestrator consumption.
- Real memory-flow evaluation is captured in `docs/memory-real-flow-evaluation-2026-05-27.md`. It verified that real Playwright failures can be learned and recalled across separate Codex-backed CLI runs, while also exposing a planner grounding gap after recall.

Memory evaluation findings:

- `runs/run_20260527T124426Z` learned from a real custom-combobox failure: `select e6 High` failed because the target was not a native `<select>`, the agent recovered with clicks, and post-run learning recorded a `select` recovery lesson.
- `runs/run_20260527T124640Z` recalled that lesson (`error_recall matched=1`) but still hit `max_steps` because the planner ignored visible option refs after opening the menu and tried keyboard/typeahead recovery instead.
- `runs/run_20260527T125202Z` recalled the same lesson and completed cleanly: failed `select`, clicked the combobox, clicked the visible High option, then finished.
- `runs/run_20260527T180316Z` validates the follow-up fix: after the same `select` failure and `error_recall matched=1`, the interpreter exposed `option` refs (`e9`, `e10`, `e11`), the planner clicked `e11`, and the run finished in 4 steps.
- `tests/test_decision_loop.py::DecisionLoopMemoryRegressionTests::test_aria_combobox_recovery_asserts_memory_recall_and_full_route` locks the flow into a deterministic regression: it asserts the action log, `error_recall matched=1`, the interpreted `option "High"` target, and the grounded click route.
- The architectural issue was not persistence or retrieval. Memory recall reached the prompt; the weak point was converting a recalled lesson into a grounded multi-step action sequence using the current snapshot. The first fix is to expose ARIA option refs and make the prompt prefer visible matching option/button refs after a non-native-select failure.

Codex wrapper path:

- Install the sibling wrapper into the active environment with `pip install -e ../codex-agent`.
- Run with `--llm-provider codex`; `--model` is passed through to Codex when provided, otherwise `codex_agent.CodexLLM` uses its default model.
- The adapter asks Codex for strict JSON (`reasoning`, `tool_name`, `tool_args`) and then reuses the existing action parser, approvals, guardrails, execution, and logging.
- This path is intentionally an adapter: it does not have Gemini's native function-calling guarantee, so malformed JSON is retried and then surfaced as a planner error.
- The `example.com` smoke run verified that URL/title and visible text now reach the planner correctly after loading Playwright snapshot files.

Real-site run findings:

- `runs/run_20260527T075045Z` completed a Wikipedia search task in 3 steps: fill search, press Enter, finish with `Guido van Rossum` and `1991`.
- `runs/run_20260527T075203Z` completed an MDN search task in 5 steps: open search, fill query, click result, request an extra snapshot, finish with syntax and return description.
- Main bottleneck is planner latency, not browser execution; MDN took 81.69s, with individual Codex planning calls around 8-20s. Codex planner history now stores compact chosen actions instead of prior full page states to reduce prompt growth.
- `snapshot` as a planner action is low value today: it executes a snapshot command, then the next loop iteration immediately snapshots again.
- The interpreter still has weak heuristics: global navigation can make article pages look like login pages, and summaries often start with nav text instead of main content.

Headed link-navigation failure:

- `runs/run_20260527T075741Z` attempted the Earth -> Manga Wikipedia hyperlink puzzle in a headed browser and stopped with `no_page_change` after 5 steps without any valid article-link click.
- `runs/run_20260527T080101Z` retried with a mobile URL, but Wikipedia redirected to desktop and stopped with `no_page_change` after 3 steps.
- Raw snapshots contain main article links after the first navigation/TOC block, but `compact_elements(max_elements=60)` cuts off most body links before the planner sees them.
- The planner tried `snapshot`, `PageDown`, and the skip link instead of selecting a route because its `clickable_elements` list contained navigation/search/TOC links rather than article-body links.
- `no_page_change` currently fires before executing the next chosen recovery action, so the second run stopped before trying the skip-link click.

Headed link-navigation fixes and rerun:

- The decision loop now keeps a larger internal clickable pool, preserves link hrefs from snapshots, demotes same-page article anchors, and prioritizes task-relevant links in the prompt before truncation.
- Article-page prompts now separate main article content links from contents, account, language, navigation, and other clickables.
- Planner-requested `snapshot` actions are skipped with corrective feedback because every loop already starts with a fresh snapshot.
- The `scroll` tool now maps to vertical `playwright-cli mousewheel <dy> 0`; the prior mapping produced horizontal wheel movement.
- Focused regression suite on 2026-05-27: `30 passed` across interpreter, snapshot parsing, prompt building, action parsing, planner, and config tests.
- `runs/run_20260527T082823Z` reached `/wiki/Manga` on step 10 but stopped at `max_steps` before it could emit `finish`.
- `runs/run_20260527T083158Z` completed in 12 steps. Actual clicked route: Earth -> Human impact on the environment -> Pulp and paper industry -> Paper -> Book -> Literature -> Genre fiction -> The Sandman (comic book) -> Manga.
- Residual gaps: Codex route choice is variable and sometimes inefficient; the final `finish` reason omitted intermediate clicks from the actual action log.

Hard-run evaluation:

- `docs/hard-run-evaluation-2026-05-27.md` records four harder headed runs across Wikipedia, MDN, Python docs, and GitHub.
- All four completed, but `runs/run_20260527T091808Z` took 15 steps because long static docs content was present in the raw snapshot but absent from prompt-visible text; current prompts add task-focused snippets from raw snapshot text to reduce this failure mode.
- Debug logging is now run-scoped for new runs: `debug_artifacts.jsonl`, copied Playwright traces, and `session.webm` were verified in `runs/run_20260527T092715Z`.
- `llm_responses.jsonl` now records prompt length, planner latency, retry/rate-limit metadata, and debug artifact paths for each planner event.
- Successful click actions now persist target link metadata, and `finish` actions include a `grounded_route` from the actual clicked links.

## Architecture Shift

**Old (vision-based):**

```
Screenshot -> OCR -> Vision LLM -> Coordinates -> Cursor/Keyboard
```

**V1 (DOM-driven, text-JSON):**

```
Playwright CLI Snapshot -> Interpreter -> ReAct Prompt -> Free-text JSON -> Parse Action -> Playwright CLI
```

**Current (DOM-driven, native function calling):**

```
Playwright CLI Snapshot -> Interpreter -> Multi-turn Chat (reasoning + tool call) -> Playwright CLI -> Result fed back
```

Why native function calling over free-text JSON:

- **No JSON parsing failures.** The LLM returns structured `FunctionCall` objects. No brace-matching, no repair heuristics.
- **No filler word bugs.** With text actions, LLMs would say `click on e12` instead of `click e12`, causing parse errors. Function calling eliminates this entirely.
- **Multi-turn memory.** The chat session maintains conversation history. The LLM sees its own prior reasoning and the results of its tool calls, enabling better recovery from errors.
- **Typed arguments.** Tool parameters are declared with schemas -- the LLM knows exactly what arguments each tool expects.
- **Explicit completion.** The `finish` tool is a structured signal, replacing fragile keyword matching on `"task complete"` / `"done"` in free-text responses.

## 3-Layer Architecture

The agent is split into three layers:

```
Execution Layer -> Interpreter Layer -> Reasoning Layer (Chat + Function Calling)
```

### Layer 1: Execution (Playwright CLI)

Responsibilities:

- Launch browser sessions.
- Execute Playwright CLI commands.
- Produce raw browser state (snapshot output, URL, title, optional screenshots).

Input/Output:

- Input: a Playwright CLI command (`playwright-cli click e12`)
- Output: raw CLI response text (includes snapshot reference)

Implementation:

- `playwright_executor.py` -- wraps CLI subprocess calls, handles sessions and npx fallback.
- All browser actions go through the CLI. No direct Playwright API calls.

### Layer 2: Interpreter (Page Understanding)

Responsibilities:

- Parse the Playwright snapshot output and extract structured page state.
- Filter for actionable elements and compress page context for the LLM.

Inputs:

- Raw snapshot text from the Execution layer.

Outputs:

```
{
  url,
  title,
  page_type,         // heuristic: "search_results", "login_page", "ecommerce", etc.
  clickable_elements: [{id, type, text}],
  visible_text,
  page_summary
}
```

Implementation:

- `snapshot_parser.py` -- extracts element refs (e1, e2) and metadata from CLI output.
- `interpreter.py` -- builds structured `InterpreterState` with page type detection, element filtering, text extraction.

### Layer 3: Reasoning (Multi-turn Chat with Function Calling)

This is where the ReAct loop happens. The LLM receives page state as a user message
and responds with reasoning text + a structured tool call.

**System instruction** (set once when chat starts):
- Role: "You are a browser automation agent."
- ReAct pattern: explicit instructions to Observe -> Think -> Act before every tool call.
- Few-shot examples: 4 worked examples showing correct reasoning (search, navigate, complete, recover from error) + 5 common mistakes to avoid.
- Goal: the user's task.
- Skill reference: Playwright CLI documentation.

**Per-step user message** (sent each iteration):
- Current URL, title, page type.
- Page summary.
- Clickable elements (up to 60).
- Visible text (truncated to 800 chars).
- Previous actions (last 12).
- Error feedback if the last action failed.

**LLM response** (returned by Gemini):
- Reasoning text (chain-of-thought) -- captured and logged for observability.
- One `FunctionCall` (e.g., `click(ref="e12")`, `fill(ref="e5", value="weather")`, `finish(reason="found the answer")`).

**Result feedback:**
After executing the tool, the execution result is sent back to the chat via
`Part.from_function_response()`. This lets the LLM know whether its action succeeded
or failed, enabling self-correction.

Implementation:

- `tool_definitions.py` -- declares ~25 tools as Gemini `FunctionDeclaration` objects + `tool_call_to_cli()` mapper.
- `planner.py` -- `ChatPlanner` manages the multi-turn chat session with `google-genai` SDK.
- `prompt_builder.py` -- `build_system_instruction()` (once) + `build_page_message()` (per step).
- `action_parser.py` -- `parse_tool_call()` validates structured tool calls and maps to CLI commands.

## Tool Definitions

All browser actions are declared as Gemini function declarations in `tool_definitions.py`.
The LLM can only call tools from this set:

**Element interaction:** `click`, `dblclick`, `hover`, `fill`, `type`, `press`, `select`, `check`, `uncheck`, `drag`, `upload`

**Navigation:** `goto`, `go_back`, `go_forward`, `reload`

**Page info:** `snapshot`, `screenshot`

**Tabs:** `tab_list`, `tab_new`, `tab_close`, `tab_select`

**Session:** `state_save`, `state_load`

**Browser control:** `close`

**Completion:** `finish` -- explicit task completion signal with a `reason` parameter.

Each tool has typed parameters with descriptions. For example:

```python
FunctionDeclaration(
    name="fill",
    description="Clear a form field and type new text into it.",
    parameters=Schema(
        type="OBJECT",
        properties={
            "ref": Schema(type="STRING", description="Element ref of the input field"),
            "value": Schema(type="STRING", description="Text to enter"),
        },
        required=["ref", "value"],
    ),
)
```

The `tool_call_to_cli()` function maps function calls to CLI command strings
(e.g., `fill(ref="e5", value="hello")` -> `playwright-cli fill e5 hello`).

## Decision Loop

The main loop in `decision_loop.py` orchestrates each step:

```
open browser
while step < max_steps:
  snapshot = executor.snapshot()
  page_state = interpret(snapshot)
  message = build_page_message(page_state, history, last_error)
  tool_result = planner.plan(message)          # -> reasoning + FunctionCall
  if tool_result.tool_name == "finish":
    stop_reason = "completed"
    break
  parsed_action = parse_tool_call(tool_name, tool_args)
  check guardrails (repeated action, no page change)
  check approval (safe/hybrid/auto)
  exec_result = executor.run(parsed_action.action)
  planner.send_tool_result(tool_name, result)  # feed back to chat
  log step
```

Key design points:

1. **No JSON parsing step.** `planner.plan()` returns a `ToolCallResult` with `tool_name`, `tool_args`, and `reasoning_text` directly.
2. **No `_is_completion_payload`.** The `finish` tool is an explicit, unambiguous completion signal.
3. **Result feedback.** After execution, the result is sent back to the chat. The LLM knows if its action succeeded.
4. **Error recovery.** If the last action failed, `last_error` is included in the next message, AND the failure result was sent back to the chat via `send_tool_result()`.

## Guardrails

- **Element ref validation:** `parse_tool_call()` checks that ref args match `e\d+` pattern.
- **Repeated action detection:** catches 3 identical consecutive actions AND period 2-4 cycles (e.g., A-B-A-B).
- **No page change:** stops if the snapshot hasn't changed for 3+ consecutive steps.
- **Error limits:** `max_steps`, `max_errors`, `max_retries` bound the loop.
- **Rate limiting:** exponential backoff on 429 errors; stops after 3 consecutive quota failures.

## Human-in-the-Loop

- `safe`: approve every action.
- `hybrid`: approve risky actions (navigation, typing, storage changes, purchases).
- `auto`: no approvals.

Risky action detection in `guardrails.py` checks both the command type (`goto`, `fill`, `close`, etc.)
and element content (clicks on buttons containing "buy", "checkout", "purchase").

## Logging and Observability

Every run produces:

```
runs/<run_id>/
  actions.jsonl             # command, approval status, execution result, stdout/stderr
  llm_responses.jsonl       # tool_name, tool_args, reasoning text per step
  browser_state.jsonl       # URL, title, snapshot path per step
  interpreter_state.jsonl   # parsed page state per step
  debug_artifacts.jsonl      # debug command results and artifact status when --debug is used
  snapshots/                # raw snapshot files per step
  screenshots/
  traces/                   # copied Playwright trace files when --debug is used
  session.webm              # video when --debug video capture succeeds
  run_meta.json             # task, stop_reason, total_steps, runtime_seconds
```

Each step logs the LLM's reasoning text alongside the tool call, providing full
observability into the agent's decision-making process.

## Codebase Structure

```
browser_agent/
  main.py                # CLI entrypoint -- builds system instruction, creates ChatPlanner
  decision_loop.py       # Main loop -- snapshot -> interpret -> plan -> approve -> execute -> feedback
  planner.py             # ChatPlanner -- multi-turn Gemini chat with function calling
  prompt_builder.py      # build_system_instruction() + build_page_message()
  tool_definitions.py    # Gemini FunctionDeclarations + tool_call_to_cli() mapper
  action_parser.py       # parse_tool_call() -- validates structured tool calls
  playwright_executor.py # CLI subprocess wrapper
  snapshot_parser.py     # Parse snapshot output into element refs
  interpreter.py         # Build structured InterpreterState from snapshot
  guardrails.py          # Risky action detection, repeat/cycle detection
  approval_system.py     # Human approval flow (safe/hybrid/auto)
  logger.py              # Run artifacts (JSONL logs, snapshots, metadata)
  config_manager.py      # YAML config at ~/.browser_agent/config.yaml
  skill_checker.py       # Validates skills/playwright-cli/SKILL.md exists
  skill_loader.py        # Loads skill text for system instruction
  constants.py           # Legacy Playwright CLI command registry
```

## Design Principles

- Local-first and observable.
- Multi-turn conversation with full history for context.
- Native function calling -- no free-text JSON parsing.
- Chain-of-thought reasoning logged for every step.
- Deterministic validation of structured tool calls.
- LLM never sees raw DOM -- interpreter filters and compresses.
- Execution results fed back to the LLM for self-correction.

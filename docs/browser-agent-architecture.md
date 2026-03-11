# Browser Agent Architecture (Playwright CLI)

This document describes the architecture of the DOM-driven browser agent. It uses
Gemini's native function calling with multi-turn chat to control a browser via
Playwright CLI commands.

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
  snapshots/                # raw snapshot files per step
  screenshots/
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
  constants.py           # ALLOWED_COMMANDS whitelist (legacy)
```

## Design Principles

- Local-first and observable.
- Multi-turn conversation with full history for context.
- Native function calling -- no free-text JSON parsing.
- Chain-of-thought reasoning logged for every step.
- Deterministic validation of structured tool calls.
- LLM never sees raw DOM -- interpreter filters and compresses.
- Execution results fed back to the LLM for self-correction.

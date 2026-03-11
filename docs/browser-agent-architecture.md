# Browser Agent Architecture (Playwright CLI)

This document describes the next‑version architecture for the DOM‑driven browser agent. It is written so an engineer or agent can start implementing immediately.

## Architecture Shift

**Old (vision-based):**

```
Screenshot → OCR → Vision LLM → Coordinates → Cursor/Keyboard
```

**New (DOM-driven with Playwright CLI):**

```
Playwright CLI Snapshot → Interpreter → ReAct Reasoning → Playwright CLI Action
```

Why the shift:

- DOM snapshots expose structured, stable element references (e1, e2, …).
- Lower token usage than vision.
- Faster loop and more reliable interactions.
- Debugging improves because actions and DOM state are explicit artifacts.

## 3‑Layer Architecture

The agent is split into three layers with a strict data boundary:

```
Execution Layer → Interpreter Layer → Reasoning Layer
```

Each layer has one job and one output.

### Layer 1: Execution (Playwright CLI)

Responsibilities:

- Launch browser sessions.
- Execute Playwright CLI commands.
- Produce raw browser state (snapshot output, URL, title, optional screenshots).

Input/Output:

- Input: a Playwright CLI command (`playwright-cli goto https://…`)
- Output: raw CLI response text (includes snapshot reference)

Implementation notes:

- Use the Playwright CLI skill in `skills/playwright-cli/SKILL.md` and `skills/playwright-cli/references/`.
- Always run actions through the CLI wrapper, not direct Playwright APIs.
- Normalize session usage (e.g., `-s=mysession`) and `npx` fallback.

### Layer 2: Interpreter (Page Understanding)

Responsibilities:

- Parse the Playwright snapshot output and extract **structured page state**.
- Filter for actionable elements and compress page context for the LLM.

Inputs:

- Raw snapshot text from the Execution layer.

Outputs:

```
{
  url,
  title,
  page_type,
  clickable_elements: [{id, type, text}],
  visible_text,
  page_summary
}
```

Interpreter tasks:

- Extract element refs (e1, e2) and their labels.
- Identify clickable targets: links, buttons, inputs, selects, etc.
- Extract visible text (e.g., `document.body.innerText` via `eval` if needed).
- Detect page types with simple heuristics:
  - URL contains `/search` → `search_results`
  - Inputs + password fields → `login_page`
  - Many product cards + prices → `ecommerce`

### Layer 3: Reasoning (ReAct)

Responsibilities:

- Decide the next action using the structured state.
- Produce strict JSON with `thought`, `action`, and `reasoning_summary`.

ReAct format:

```
Thought: internal reasoning
Action: playwright-cli click e21
Observation: (next iteration’s interpreted state)
```

Output schema (strict JSON):

```json
{
  "thought": "internal reasoning",
  "action": "playwright-cli <command> [args]",
  "reasoning_summary": "short log summary",
  "final": false
}
```

## Integrating the Playwright CLI Skill

Source of truth:

- `skills/playwright-cli/SKILL.md`
- `skills/playwright-cli/references/*.md`

Integration points:

- **Skill check**: validate `skills/playwright-cli/SKILL.md` exists and declares `playwright-cli`.
- **Executor**: wrap all actions with a CLI runner.
- **Parser**: parse snapshot output and load `.playwright-cli/page-*.yml` if referenced.

Constraints for LLM:

- Actions must start with `playwright-cli`.
- Only use element refs; do not use selector flags like `--selector`.
- Only allow flags that the skill documents (e.g., `open --persistent`, `snapshot --filename`).

## ReAct Loop With Guardrails

Pseudo‑loop:

```
open browser
while not done:
  snapshot = exec.snapshot()
  page_state = interpret(snapshot)
  prompt = build_prompt(task, page_state, history)
  llm = plan(prompt)
  action = parse(llm)
  check guardrails + approvals
  exec action
  log step
```

Guardrails:

- **Whitelist commands**: only those in the skill.
- **Argument validation**: element‑required commands must have valid `eNN`.
- **Action repetition**: stop after 3 identical actions.
- **No page change**: stop if snapshots don’t change after successful actions.
- **Limits**: `max_steps`, `max_errors`, `max_retries`.

Human‑in‑the‑loop:

- `safe`: approve every action.
- `hybrid`: approve risky actions (navigation, typing, storage changes, purchases).
- `auto`: no approvals.

## Failure Handling

- If interpreter produces too little data, re‑snapshot.
- If action fails, retry or adapt (bounded by `max_retries`).
- If planner fails repeatedly (429 quota), stop early and record `quota_exceeded`.

## Logging and Observability

Every run must produce:

```
runs/<run_id>/
  actions.jsonl
  browser_state.jsonl
  interpreter_state.jsonl
  agent_reasoning.jsonl
  snapshots/
  screenshots/
  run_meta.json
```

Each step logs:

- `thought`
- `action`
- `observation` (interpreted state)
- execution result
- approval status

## Codebase Structure

Recommended modules:

```
browser_agent/
  main.py                # CLI entrypoint
  decision_loop.py       # ReAct loop orchestrator
  planner.py             # LLM planner (Gemini)
  prompt_builder.py      # ReAct prompt
  action_parser.py       # Validate/normalize actions
  playwright_executor.py # CLI runner
  snapshot_parser.py     # Parse snapshot output
  interpreter.py         # Build structured page state
  guardrails.py          # Safety policies
  approval_system.py     # Human approval flow
  logger.py              # Run artifacts
  config_manager.py      # Runtime configuration
  skill_checker.py       # Ensure Playwright CLI skill exists
```

## Implementation Order

1. Playwright CLI skill integration + executor wrapper.
2. Snapshot parser and interpreter module.
3. ReAct prompt builder and planner.
4. Decision loop + guardrails + approvals.
5. Logging and artifacts.

## Design Principles

- Local‑first and observable.
- Strict boundaries between layers.
- Deterministic parsing and validation.
- LLM never sees raw DOM without interpreter filtering.

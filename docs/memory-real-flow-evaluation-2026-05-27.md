# Real Memory Flow Evaluation — 2026-05-27

This evaluation tested whether the memory layer helps across actual browser-agent
runs, not just unit tests or scripted loop simulations. The runs used the real
`browser-agent` CLI, real Playwright browser sessions, and the Codex planner.

## Scope

The experiment focused on one repeatable browser-control failure:

- A page exposes a Priority control as an ARIA `button role="combobox"`, not as a
  native `<select>`.
- The planner is asked to use the Priority dropdown/select control to set the
  value to High.
- A reasonable first action is `select(ref, "High")`, but real Playwright fails
  with `Element is not a <select> element`.
- The useful recovery is to click the combobox, then click the visible High
  option.

The goal was to verify the full cycle:

1. A real browser action fails.
2. The agent recovers.
3. Post-run learning records a lesson.
4. A later run recalls the lesson after the same failure.
5. The recalled lesson changes the next-step context enough for recovery.

## Test Setup

The fixture pages were served from:

```text
/private/tmp/mycompagent-memory-real/site/
```

The browser-agent config and memory store were isolated under:

```text
/private/tmp/mycompagent-memory-real/home/.browser_agent/
```

This kept the user's real `~/.browser_agent/memory.json` untouched. The runs used
normal Codex credentials through `CODEX_HOME=/Users/yashagrawal/.codex`.

The local server ran on:

```text
http://127.0.0.1:8766/
```

The server and temporary Playwright sessions were stopped after the experiment.

## Commands Used

Workflow A, no learned select lesson yet:

```bash
env HOME=/private/tmp/mycompagent-memory-real/home \
  CODEX_HOME=/Users/yashagrawal/.codex \
  /opt/miniconda3/bin/conda run -n mycompagent \
  python -m browser_agent.main \
  "Use the Priority dropdown/select control to set the workflow priority to High. Finish only when the page says the task is complete." \
  --auto --llm-provider codex \
  --session realmem-a \
  --start-url http://127.0.0.1:8766/workflow-a.html \
  --max-steps 8
```

Workflow B, same learned lesson available but a harder option surface:

```bash
env HOME=/private/tmp/mycompagent-memory-real/home \
  CODEX_HOME=/Users/yashagrawal/.codex \
  /opt/miniconda3/bin/conda run -n mycompagent \
  python -m browser_agent.main \
  "Use the Priority dropdown/select control to set the escalation priority to High. Finish only when the page says the task is complete." \
  --auto --llm-provider codex \
  --session realmem-b \
  --start-url http://127.0.0.1:8766/workflow-b.html \
  --max-steps 8
```

Workflow C, same learned lesson available with a simpler visible High option:

```bash
env HOME=/private/tmp/mycompagent-memory-real/home \
  CODEX_HOME=/Users/yashagrawal/.codex \
  /opt/miniconda3/bin/conda run -n mycompagent \
  python -m browser_agent.main \
  "Use the Priority dropdown/select control to set the review priority to High. Finish only when the page says the task is complete." \
  --auto --llm-provider codex \
  --session realmem-c \
  --start-url http://127.0.0.1:8766/workflow-c.html \
  --max-steps 8
```

Workflow B replay after the grounding fix:

```bash
env HOME=/private/tmp/mycompagent-memory-real/home \
  CODEX_HOME=/Users/yashagrawal/.codex \
  /opt/miniconda3/bin/conda run -n mycompagent \
  python -m browser_agent.main \
  "Use the Priority dropdown/select control to set the escalation priority to High. Finish only when the page says the task is complete." \
  --auto --llm-provider codex \
  --session realmem-b-fixed \
  --start-url http://127.0.0.1:8766/workflow-b.html \
  --max-steps 8
```

## Run 1: Workflow A Learned From Failure

Artifacts:

- `runs/run_20260527T124426Z/actions.jsonl`
- `runs/run_20260527T124426Z/memory_events.jsonl`

Behavior:

1. The planner saw the Priority combobox at `e6`.
2. It chose `playwright-cli select e6 High`.
3. Real Playwright failed because the target was a button-style ARIA combobox,
   not a native `<select>`.
4. The agent recovered by clicking `e6`, then clicking the visible High option
   `e11`.
5. The run finished successfully with `Task complete: Priority is High for
   Workflow A.`

The memory event log shows the first failure had no learned match:

```json
{"event": "error_recall", "command": "select", "matched": 0}
```

After completion, post-run learning recorded:

```text
When select fails with 'Error: locator.selectOption: Error: Element is not a <select', try click instead.
```

This confirms the memory layer can learn from a real browser-control
failure-to-recovery sequence.

## Run 2: Workflow B Recalled Memory But Did Not Finish

Artifacts:

- `runs/run_20260527T124640Z/actions.jsonl`
- `runs/run_20260527T124640Z/memory_events.jsonl`

Behavior:

1. The planner again chose `playwright-cli select e6 High`.
2. Real Playwright failed with the same `Element is not a <select> element`
   error.
3. Memory recall fired and matched the Workflow A lesson:

```json
{"event": "error_recall", "command": "select", "matched": 1}
```

4. The planner did follow the lesson enough to click the combobox.
5. After the menu opened, the snapshot contained option refs:

```text
option "Normal" [ref=e9]
option "Urgent" [ref=e10]
option "High" [ref=e11]
```

6. Instead of clicking `e11`, the planner tried keyboard navigation and typeahead
   (`ArrowDown`, `ArrowDown`, `type High`, `Enter`), retried `select`, then hit
   `max_steps`.

This run is important because it separates memory retrieval from planner usage.
The memory layer did retrieve the relevant lesson. The planner still failed to
convert the recovered page state into the obvious next browser action.

## Run 3: Workflow C Recalled Memory And Completed

Artifacts:

- `runs/run_20260527T125202Z/actions.jsonl`
- `runs/run_20260527T125202Z/memory_events.jsonl`

Behavior:

1. The planner again chose `playwright-cli select e6 High`.
2. Real Playwright failed with the same non-native-select error.
3. Memory recall matched the learned lesson:

```json
{"event": "error_recall", "command": "select", "matched": 1}
```

4. The planner clicked the combobox, saw a visible High button at `e9`, clicked
   it, and finished.

The successful action route was:

```text
select e6 High -> error
click e6 -> ok
click e9 -> ok
finish -> completed
```

The final isolated memory status showed four lessons total, including the
learned Tier 2 recovery:

```text
[learned] error_recovery | uses=7 | triggered_on=[127.0.0.1]
"When select fails with 'Error: locator.selectOption: Error: Element is not a <select', try click instead."
```

## Run 4: Workflow B Passed After Grounding Fix

Artifacts:

- `runs/run_20260527T180316Z/actions.jsonl`
- `runs/run_20260527T180316Z/interpreter_state.jsonl`
- `runs/run_20260527T180316Z/memory_events.jsonl`

Code changes validated by this replay:

- `browser_agent/interpreter.py` now treats ARIA `option` nodes as actionable
  targets.
- `browser_agent/prompt_builder.py` now adds a custom-control recovery note
  after non-native `selectOption` failures when visible option/button refs are
  available.

Behavior:

1. Step 1 chose `playwright-cli select e6 High`.
2. Playwright failed with `Element is not a <select> element`.
3. Memory recall fired and matched the learned lesson:

```json
{"event": "error_recall", "command": "select", "matched": 1}
```

4. Step 2 clicked the combobox at `e6`.
5. Step 3 interpreted the opened menu with actionable option refs:

```json
{"id": "e9", "type": "option", "text": "option \"Normal\""}
{"id": "e10", "type": "option", "text": "option \"Urgent\""}
{"id": "e11", "type": "option", "text": "option \"High\""}
```

6. The planner clicked `e11`; Playwright resolved it as:

```js
await page.getByRole('option', { name: 'High' }).click();
```

7. Step 4 saw `Priority dropdown: High` and finished with
   `Task complete: Priority is High for Workflow B.`

The successful replay route was:

```text
select e6 High -> error
error_recall matched=1
click e6 -> ok
click e11 -> ok
finish -> completed
```

Focused and full regression checks passed after the fix:

```text
tests/test_interpreter.py tests/test_prompt_builder.py: 15 passed
tests/test_decision_loop.py: 6 passed
tests: 112 passed
```

## Findings

### What Worked

- `MemoryStore` persisted the learned lesson across separate real CLI runs.
- Trigger A, `recall_on_error()`, matched the later real `select` failures.
- The recalled lesson was injected into the next planner message.
- The lesson use count and deduplication path worked; later matching failures
  reinforced the same lesson instead of creating duplicates.
- The memory event log made the experiment auditable. `memory_events.jsonl`
  showed whether recall matched, and `actions.jsonl` showed whether the recovery
  action actually happened.
- After the fix, the opened Workflow B menu is no longer hidden from the planner:
  `interpreter_state.jsonl` exposes `option "High"` as ref `e11`, and the
  planner can ground the recalled advice in that concrete target.

### What Did Not Work Reliably

- Memory recall is advisory. A matched lesson does not force the planner to take
  the recovery action. The new prompt note improves this specific
  custom-combobox case but does not make memory deterministic.
- The lesson learned during this evaluation, `try click instead`, was too
  underspecified after the first click succeeded. It told the planner to click,
  but not how to complete the second half of the recovery: inspect the opened
  menu and click the target option ref. The prompt now supplies that missing
  operational guidance only for the recognized non-native-select failure shape.
- Before the fix, visible option refs existed in Workflow B's raw snapshot but
  were filtered out of the interpreted clickable list. The planner ignored the
  best direct click (`e11`) and chose keyboard navigation instead. After the fix,
  `e11` is exposed as an `option` target and the real replay clicked it.
- At the time of this evaluation, post-run learning treated the adjacent
  `select error -> click combobox ok` pair as the successful recovery. That was
  only a partial recovery; the task was not complete until a later option click
  succeeded.
- Promotion risk exists if a partial lesson is repeatedly reinforced. The lesson
  can get a high `use_count` even though it does not encode the full successful
  action sequence.

## Implications

The memory storage and retrieval layer is functioning. The first
memory-to-action grounding issue was fixed by exposing ARIA option refs and
adding a targeted prompt note after non-native-select failures.

Learning-quality follow-up status:

- Completed: avoid learning a one-step recovery from adjacent action pairs when
  the task only succeeded after a longer sequence.
- Preserve auditability by continuing to report both memory events and browser
  routes in future hard runs.

## Recommended Follow-Up Work

1. Add a regression/evaluation fixture for ARIA combobox recovery.

   Implemented in `tests/test_decision_loop.py`. It verifies the full route, not
   just memory events:

   ```text
   select e6 High -> error
   error_recall matched=1
   click e6 -> ok
   click High option ref -> ok
   finish -> completed
   ```

2. Improve the planner prompt for recovered custom-control failures.

   Implemented in the first slice: when the last error says `Element is not a
   <select> element`, the prompt now prefers click-based option selection over
   keyboard guessing if option/button refs are visible.

3. Completed post-run learning beyond adjacent pairs.

   Implemented in `browser_agent/memory.py` and covered by
   `tests/test_memory.py::TestPostRunLearning::test_learns_short_multi_step_recovery_that_completes`.
   The extractor now keeps a short sequence of successful actions when it reaches
   `finish`, so the combobox case learns `click the combobox, then click the
   matching option` instead of only `try click instead`.

4. Consider storing recovery shape metadata.

   For example, a lesson could distinguish:

   - failed action: `select`
   - failure pattern: `Element is not a <select> element`
   - recovery sequence: `click control`, then `click option label`
   - target label dependency: requested value such as `High`

5. Keep memory recall visible in evaluation output.

   Future hard runs should report both the browser route and the memory events,
   because a completed task can hide whether memory was actually used.

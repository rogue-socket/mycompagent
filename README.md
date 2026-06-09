# Browser Agent

A **DOM-driven browser agent** with pluggable LLM planning. The default Gemini path uses native function calling; the optional Codex path uses the local `../codex-agent` wrapper and strict JSON actions.

Architecture: **Snapshot → Interpreter → Planner (ReAct reasoning + action) → Execution → Result fed back**

Key design choices:
- **Native function calling** — the LLM returns typed `FunctionCall` objects, not free-text JSON. Eliminates parsing failures.
- **Codex wrapper option** — `--llm-provider codex` routes planning through `codex_agent.CodexLLM`.
- **Multi-turn conversation** — the chat maintains history across steps. The LLM sees its prior reasoning and tool results.
- **Chain-of-thought** — the system prompt requires step-by-step reasoning (Observe → Think → Act) before every tool call.
- **Few-shot examples** — the system instruction includes worked examples of correct reasoning patterns.
- **Human-in-the-loop** — three approval modes (safe/hybrid/auto) gate risky actions.
- **Two-tiered memory** — the agent learns from mistakes across runs and self-optimizes over time.

## 1) Setup From Scratch

### 1.1 Requirements

- Python 3.11+
- Playwright CLI installed
- `google-genai` Python package (v1.66+)
- A Gemini API key for the default `gemini` provider, or an installed `../codex-agent` wrapper for `--llm-provider codex`

### 1.2 Install Python dependencies

Conda setup used for this project on this machine:

```bash
/opt/miniconda3/bin/conda create -n mycompagent python=3.11 pip -y
source /opt/miniconda3/bin/activate mycompagent
python -m pip install --upgrade pip
pip install -r requirements-browser-agent.txt
pip install -r requirements-dev.txt
pip install -e .
pip install -e ../codex-agent  # optional: enables --llm-provider codex
```

Virtualenv alternative:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-browser-agent.txt
pip install -e .
```

### 1.3 Install Playwright CLI

If `playwright-cli` is already available globally, skip this.

```bash
# Option A: global install (preferred)
# Follow your org's standard for installing the Playwright CLI

# Option B: use npx every time
npx playwright-cli open https://example.com
```

If you use `npx`, run the agent with `--use-npx` (or set it during setup).

### 1.4 First-time agent config

Run once to create config at `~/.browser_agent/config.yaml`:

```bash
browser-agent "open example.com" --safe
```

Prompts:
- API key
- model (default `gemini-1.5-flash`)
- default mode (`safe` / `hybrid` / `auto`)
- default Playwright session name (optional)
- default start URL (optional)
- whether to use `npx` by default

## 2) Modes (Safe / Hybrid / Auto)

- `--safe`: approve every action. Best for first-time flows and logins.
- `--hybrid`: approves only risky actions (navigation, typing, submissions, destructive clicks).
- `--auto`: fully autonomous.
- `--ask-human`: lets `--auto` pause for short human-provided values, such as
  CAPTCHA text. Safe and hybrid runs allow this by default because they are
  already interactive.

Examples:

```bash
browser-agent "find best padel rackets" --safe --start-url https://google.com
browser-agent "find best padel rackets" --hybrid --start-url https://google.com
browser-agent "find best padel rackets" --auto --start-url https://google.com
```

## 3) Run the Agent

### 3.1 Basic run

```bash
browser-agent "open youtube.com" --safe
```

To use the local Codex wrapper from `../codex-agent` instead of Gemini:

```bash
browser-agent "open example.com" --auto --llm-provider codex --start-url https://example.com
```

The Codex path uses `codex_agent.CodexLLM` and does not require the Gemini API key,
but `../codex-agent` must be installed in the active environment.

### 3.2 Run with a start URL

```bash
browser-agent "search for best padel rackets" --safe --start-url https://google.com
```

### 3.3 Session usage

```bash
browser-agent "check inbox" --session gmail --start-url https://mail.google.com
```

### 3.4 Persistent profile (recommended for logins)

```bash
browser-agent "open gmail" --session gmail --persistent --headed --start-url https://mail.google.com
```

### 3.5 Custom profile directory

```bash
browser-agent "open gmail" --session gmail --profile ~/.browser-agent-profiles/gmail --headed --start-url https://mail.google.com
```

### 3.6 Wikipedia link game

The agent can play the Wikipedia link game: start on one article and reach a
target article by clicking only Wikipedia article links. This is a useful stress
test because it requires route planning, recovery from bad clicks, and patience
with long pages.

```bash
browser-agent "Starting on Pythagorean theorem, reach Heavy metal music by only clicking Wikipedia article links. Finish when the current article is Heavy metal music." \
  --auto \
  --llm-provider codex \
  --headed \
  --debug \
  --start-url https://en.wikipedia.org/wiki/Pythagorean_theorem \
  --max-steps 20
```

Good challenge prompts are specific about the start page, target page, allowed
actions, and finish condition. The `--debug` flag is recommended while tuning
these runs because traces, snapshots, and `llm_responses.jsonl` make the route
easy to inspect afterward.

Fun variations to try:

- **Shortest-path race**: run the same start/target pair multiple times and
  compare step counts.
- **Bridge hunting**: pick distant topics and see which broad concept unlocks
  the route, such as math -> art -> music.
- **No-search mode**: forbid search boxes, browser navigation, and direct URL
  entry; only visible article links are allowed.
- **Trap avoidance**: choose starts with tempting taxonomy or chronology loops
  and score the agent on escaping them.
- **Route explainback**: after finishing, ask the agent to summarize why each
  clicked article was a reasonable bridge.
- **Daily puzzle set**: keep a small list of easy, medium, and hard pairs and
  track completion rate, step count, and failure mode.
- **Versus mode**: compare providers or prompts on the same puzzle using
  `run_meta.json`, `actions.jsonl`, and planner latency logs.

See [docs/wikipedia-hard-run-evaluation-2026-05-27.md](docs/wikipedia-hard-run-evaluation-2026-05-27.md)
for completed and failed example routes.

### 3.7 Wordle game test

The agent can also play UI-heavy puzzle games when the page exposes enough
state through the DOM or keyboard feedback. A successful run against the
official Wordle site solved the puzzle in four guesses:

```text
CRANE -> STAIR -> GUARD -> WHARF
```

Run command:

```bash
browser-agent "Play one game of Wordle on the official Wordle website. If a help, intro, login, stats, or subscription modal blocks the board, close or dismiss it. Guess valid five-letter English words. After each guess, use the tile colors and keyboard colors to choose the next guess. Do not log in or subscribe. Finish when the puzzle is solved, when all six guesses are used, or when the page clearly shows the game is over." \
  --auto \
  --llm-provider codex \
  --headed \
  --debug \
  --start-url https://www.nytimes.com/games/wordle/index.html \
  --max-steps 35
```

Result: `runs/run_20260609T071938Z` completed in 13 steps and 208.53s with
`Solved the puzzle with WHARF.` The run recovered from one stale Play-button ref,
dismissed the privacy and tutorial modals, then used tile feedback to choose each
next guess.

### 3.8 Canvas gesture test: Perfect Circle

The Perfect Circle game on neal.fun is a focused canvas-gesture test. The page
starts normally, then asks for a freehand circle around a visual target that is
not exposed as a normal clickable control. The agent now has a bounded
`draw_circle` tool for this specific gesture class.

```bash
browser-agent "Play the Perfect Circle game on neal.fun. Dismiss any intro or cookie popups if they block the game. Start the game, draw one circle as accurately as possible using draw_circle with radius 170 and steps 24, then finish with the score shown on the page." \
  --auto \
  --llm-provider codex \
  --headed \
  --debug \
  --start-url https://neal.fun/perfect-circle/ \
  --max-steps 20
```

Earlier runs such as `runs/run_20260609T072626Z` and
`runs/run_20260609T113940Z` clicked `Go`, reached the drawing prompt, and then
stopped because no canvas gesture primitive was available. After adding
`draw_circle`, `runs/run_20260609T114407Z` completed in 4 steps. The page rendered
`99.8%` with the label `Divine circle`.

### 3.9 Infinite Craft drag test

Infinite Craft is a useful long-horizon game test because it requires repeated
drag-and-drop, inventory search, duplicate item handling, and recipe tracking.
The installed `playwright-cli drag <startRef> <endRef>` command currently reports
a schema error for valid refs, so the agent now retries that specific failure
with a guarded mouse-path fallback that computes DOM element centers and performs
the drag with Playwright mouse events.

Run command:

```bash
browser-agent "Play Infinite Craft on neal.fun. Goal: create Adolf Hitler if possible; if the game only produces Hitler, finish with Hitler and the path. Treat the target as a neutral game item; do not praise or glorify it. Use only the game interface. Important route hints: create Time Machine with Water + Fire = Steam; Fire + Steam = Engine; Engine + Engine = Rocket; Engine + Rocket = Space Ship; Steam + Space Ship = Time Machine. Create Cigar with Earth + Water = Plant; Wind + Plant = Dandelion; Water + Dandelion = Wine; Fire + Dandelion = Ash; Wine + Ash = Cigar. Then combine Time Machine + Cigar = Hitler. If Adolf Hitler specifically is still needed, recipe references suggest Austria + Donald Trump or German + Donald Trump, but only pursue that if practical after Hitler. Use search to find existing inventory items, click items to place them on canvas, and drag visible cards together to combine them. Finish when the target or closest created target is visible, with the exact path." \
  --auto \
  --llm-provider codex \
  --session ih3 \
  --headed \
  --debug \
  --start-url https://neal.fun/infinite-craft/ \
  --max-steps 100
```

Result: `runs/run_20260609T091325Z` completed in 27 steps and 368.59s. The game
created `Hitler`, not the exact `Adolf Hitler` label. Completed path:

```text
Water + Fire = Steam
Fire + Steam = Engine
Engine + Engine = Rocket
Engine + Rocket = Space Ship
Steam + Space Ship = Time Machine
Earth + Water = Plant
Wind + Plant = Dandelion
Water + Dandelion = Wine
Fire + Dandelion = Ash
Wine + Ash = Cigar
Time Machine + Cigar = Hitler
```

The run also exposed two drag-specific requirements: same-label recipes such as
`Engine + Engine` need distinct DOM matches, and palette-to-canvas drags are less
reliable than clicking inventory items onto the canvas before combining visible
cards.

### 3.10 Password Game CAPTCHA test

The Password Game is a harder mixed-control test because later rules can require
visual-only information. A run with screenshot OCR reached Rule 10, but stopped
because the CAPTCHA text could not be read reliably from DOM state or OCR:

```text
Current password: Aaaaa!799juneVIIpepsiFeSBUN
Blocked rule: Rule 10, CAPTCHA
Run: runs/run_20260609T093756Z
```

The fix is to treat this as human-in-the-loop input rather than terminal failure.
The planner now has an `ask_human` tool for short missing values, and `--auto`
runs can opt into it with `--ask-human`:

```bash
browser-agent "Play The Password Game on neal.fun. Dismiss any popup only if it blocks the game. Enter and edit the password to satisfy the visible rules one by one. Read each new rule after submitting or editing, keep the password valid for earlier rules, and continue until the game says the password is accepted or complete. If a rule requires a short visual value that is not available in the page state, ask the human for it and continue." \
  --auto \
  --ask-human \
  --llm-provider codex \
  --session pg2 \
  --headed \
  --debug \
  --start-url https://neal.fun/password-game/ \
  --max-steps 80
```

Human answers are passed to the planner as tool results so it can continue, but
the action log records only that a response was provided. Do not use this path
for secrets that should not enter planner context.

Follow-up run `runs/run_20260609T094628Z` confirmed the HITL path works for
short operator-visible values: the agent asked for the Rule 10 CAPTCHA, accepted
`4dgf7`, and later used HITL again for the Street View country and chess move.
It progressed to Rule 18, then got stuck because it treated the input length
counter as the atomic-number total and repeatedly made arithmetic guesses. That
is a different class of failure: the agent needs deterministic helper support
for rule arithmetic and domain solvers, not just more visual extraction.

## 4) Login Flows

### 4.1 Recommended login flow (safe + persistent)

1. Launch in safe mode with a persistent profile:

```bash
browser-agent "login to site" --safe --persistent --headed --start-url https://example.com/login
```

2. Approve steps or manually complete login in the browser window.
3. Save storage state after login:

```bash
playwright-cli state-save auth.json
```

4. Restore login next time:

```bash
playwright-cli state-load auth.json
```

5. Re-run the agent with the same session/profile.

### 4.2 Login flow using storage state only

```bash
playwright-cli state-load auth.json
browser-agent "check account" --safe --start-url https://example.com
```

### 4.3 Multi-account logins

```bash
browser-agent "open account A" --session acct-a --persistent --headed --start-url https://example.com/login
browser-agent "open account B" --session acct-b --persistent --headed --start-url https://example.com/login
```

## 5) Do’s and Don’ts

### Do

- Use `--safe` for any login or payment flow.
- Use `--persistent` with a named `--session` for repeat logins.
- Save `state-save` after successful login.
- Use `--debug` when a flow fails to capture trace + video.
- Start with smaller tasks and increase autonomy gradually.

### Don’t

- Run `--auto` on payment, checkout, or account-modifying tasks.
- Rely on storage state across unrelated sessions or profiles.
- Leave stale persistent profiles uncleaned for high‑risk apps.
- Ignore repeated action loops; stop and review logs.

## 6) Debug Mode and Observability

### 6.1 Debug mode (tracing + video)

```bash
browser-agent "search for best padel rackets" --debug --start-url https://google.com
```

Outputs:
- Traces: `.playwright-cli/traces/`
- Video: `runs/<run_id>/session.webm`

### 6.2 Manual tracing

```bash
playwright-cli tracing-start
# run actions
playwright-cli tracing-stop
```

### 6.3 Logs

Each run produces:

```text
runs/<run_id>/
  snapshots/
  screenshots/
  actions.jsonl            # Every action executed + approval status + stdout/stderr
  llm_responses.jsonl      # Tool calls + reasoning text from the LLM
  browser_state.jsonl      # URL, title, snapshot paths per step
  interpreter_state.jsonl  # Parsed page state per step
  memory_events.jsonl      # Memory access, recalls, learning, promotions
  run_meta.json            # Task, stop_reason, step count, runtime
```

### 6.4 Memory observability

The agent has a two-tiered memory system that learns from failure→recovery patterns across runs. Every memory interaction is logged to `memory_events.jsonl`:

| Event | What it tells you |
|-------|-------------------|
| `tier1_loaded` | Which universal lessons were injected into the system prompt |
| `error_recall` | A command failed — did memory find relevant tips? |
| `domain_recall` | Navigated to a new domain — any site-specific advice? |
| `lesson_recorded` | Post-run learning found a new failure→recovery pattern |
| `lesson_deduplicated` | An existing lesson was reinforced (use count bumped) |
| `lesson_promoted` | A lesson graduated from reactive (Tier 2) to always-on (Tier 1) |
| `lessons_pruned` | Stale lessons were cleaned up on startup |

Query examples:

```bash
# All memory activity for a run
tail -n 50 runs/run_*/memory_events.jsonl

# Did Trigger A (error recall) fire? What matched?
rg '"event": "error_recall"' runs/run_*/memory_events.jsonl

# Did Trigger B (domain recall) fire?
rg '"event": "domain_recall"' runs/run_*/memory_events.jsonl

# Any promotions across all runs?
rg '"event": "lesson_promoted"' runs/run_*/memory_events.jsonl
```

The memory store itself is persisted at `~/.browser_agent/memory.json`. See [docs/memory-system.md](docs/memory-system.md) for the full architecture and [docs/memory-real-flow-evaluation-2026-05-27.md](docs/memory-real-flow-evaluation-2026-05-27.md) for the latest real browser-flow findings.

## 7) Manual Playwright CLI Commands

```bash
playwright-cli open https://example.com
playwright-cli snapshot
playwright-cli click e12
playwright-cli type "search query"
playwright-cli press Enter
playwright-cli screenshot
playwright-cli close
```

## 8) Common Issues

### `playwright-cli not found`

Install it globally or run with `--use-npx`.

### `429 quota exceeded`

Switch to a lower-cost model (e.g., `gemini-1.5-flash`) or wait for quota reset.

### Too many approvals

Use `--hybrid` or `--auto` if you want fewer prompts.

## 9) Safety Notes

- Keep sensitive tasks in `--safe`.
- Store login profiles in a dedicated folder per account.
- Use a dedicated browser profile for automation to avoid leaking personal sessions.

## 10) Setup Validation Checklist

Run these once to confirm Playwright CLI and the agent are working:

```bash
python -m pytest tests -q
playwright-cli open https://example.com
playwright-cli snapshot
playwright-cli close

browser-agent "open example.com" --safe
```

As of 2026-05-27, run the suite in the `mycompagent` Conda env. Memory-system
behavior and focused coverage are documented in `docs/memory-system.md`.

If `playwright-cli` is missing, use:

```bash
npx playwright-cli open https://example.com
```

## 11) Login and MFA Guidance

### MFA/OTP

- Use `--safe` for any login flow.
- Complete MFA manually in the headed browser.
- Avoid automating OTP codes unless explicitly required by policy.

### When login fails

- Use `--debug` to capture traces and video.
- Try a fresh profile: `--persistent --profile ~/.browser-agent-profiles/<site>`.
- Clear stale session data:

```bash
playwright-cli -s=mysession delete-data
```

## 12) Profile and Session Lifecycle

- Sessions isolate cookies/storage by `--session` name.
- Use persistent profiles for long‑lived logins.
- Delete persistent data if it becomes corrupt or risky.

Commands:

```bash
playwright-cli list
playwright-cli close-all
playwright-cli kill-all
playwright-cli -s=mysession delete-data
```

## 13) Debugging Failed Runs

1. Check `runs/<run_id>/run_meta.json` for stop reason.
2. Check `actions.jsonl` for execution errors.
3. Check `llm_responses.jsonl` for planner errors or malformed actions.
4. Inspect `snapshots/step_XXXX.txt` to see the DOM references.
5. Use `--debug` for trace/video if the issue is visual or timing‑related.

## 14) Guardrails and Approvals

- SAFE: every action requires approval.
- HYBRID: approves only risky actions (navigation, typing, storage changes, destructive clicks).
- AUTO: no approvals.

If you see repeated approvals on a low‑risk task, switch to `--hybrid`.

## 15) Allowed Commands (Playwright CLI)

The agent enforces a strict whitelist, aligned to the skill docs. It will reject commands that:

- are not in the allowed list
- have malformed args (e.g., `check --url ...`)
- do not target valid element refs when required

## 16) Example Flows

### Search and open a result

```bash
browser-agent "search for best padel rackets and open the first result" --safe --start-url https://google.com
```

### Fill a form

```bash
browser-agent "fill the contact form with my name and email" --safe --start-url https://example.com/contact
```

## 17) Reset and Clean Up

```bash
playwright-cli close-all
playwright-cli kill-all
playwright-cli delete-data
```

## 18) Operational Tips

- Use `--headed` during early development for visibility.
- Use `--start-url` to reduce unnecessary navigation steps.
- Use a dedicated profile folder per site to isolate login state.

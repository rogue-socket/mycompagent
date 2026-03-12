# Browser Agent CLI Reference

## Installation

```bash
pip install -e .
```

This registers the `browser-agent` command via the console script entry point.

## Usage

```
browser-agent <task> [flags]
browser-agent --memory-status
browser-agent --setup
```

---

## Positional Argument

| Argument | Description |
|----------|-------------|
| `task` | Natural language task for the agent to execute. Required unless using `--memory-status`. |

```bash
browser-agent "Search YouTube for 'python tutorials' and click the first result"
```

---

## Mode Flags

Only one mode can be specified. If none is given, the mode from `~/.browser_agent/config.yaml` is used (default: `safe`).

| Flag | Description |
|------|-------------|
| `--safe` | Require human approval for **every** action before execution. |
| `--hybrid` | Require approval only for **risky** actions (navigation, typing, purchases, storage changes). Safe actions execute automatically. |
| `--auto` | Fully autonomous — no approvals required. |

```bash
# Approve everything
browser-agent "Book a flight to NYC" --safe

# Only approve risky actions like form submissions
browser-agent "Search for weather in London" --hybrid

# Fully autonomous
browser-agent "Find the top news on Hacker News" --auto
```

---

## Model & Limits

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--model MODEL` | string | `gemini-1.5-flash` | Override the LLM model (must be a Gemini model). |
| `--max-steps N` | int | `50` | Maximum number of agent steps before stopping. |

```bash
# Use a different model with more steps
browser-agent "Summarize the Wikipedia page on AI" --model gemini-2.0-flash --max-steps 100

# Quick task with a low step limit
browser-agent "Google 'weather today'" --auto --max-steps 10
```

---

## Browser Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--start-url URL` | string | *(none)* | Open the browser at this URL instead of a blank page. |
| `--browser TYPE` | string | *(default)* | Browser engine: `chrome`, `firefox`, or `webkit`. |
| `--headed` | flag | off | Launch the browser in headed mode (visible window). |
| `--persistent` | flag | off | Use a persistent browser profile (retains cookies/storage across runs). |
| `--profile DIR` | string | *(none)* | Path to a specific browser profile directory. |

```bash
# Start on a specific page
browser-agent "Click the Sign In button" --start-url https://example.com --headed

# Use Firefox with a persistent profile
browser-agent "Log in to my account" --browser firefox --persistent

# Use a custom profile directory
browser-agent "Check my Gmail" --profile ~/.browser_agent/profiles/gmail --headed
```

---

## Session & Playwright Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--session NAME` | string | *(none)* | Name for the Playwright CLI session (reuse across runs). |
| `--config FILE` | string | *(none)* | Path to a Playwright CLI config file. |
| `--use-npx` | flag | off | Use `npx playwright-cli` instead of a direct `playwright-cli` binary. |

```bash
# Named session (can be reattached)
browser-agent "Search for news" --session my-session

# Use npx to run playwright
browser-agent "Open YouTube" --use-npx

# Custom playwright config
browser-agent "Test login flow" --config ./playwright.config.ts
```

---

## Debugging

| Flag | Description |
|------|-------------|
| `--debug` | Enable Playwright tracing and video recording. Saves `trace.zip` and `session.webm` to the run folder. |

```bash
browser-agent "Fill out the contact form" --debug --headed
```

When `--debug` is enabled:
- Tracing starts at the beginning and stops at the end of the run.
- Video is recorded and saved to `runs/<run_id>/session.webm`.
- Trace can be viewed with `npx playwright show-trace runs/<run_id>/trace.zip`.

---

## Setup

| Flag | Description |
|------|-------------|
| `--setup` | Re-run interactive configuration setup and exit. Prompts for API key, model, mode, and other defaults. |

```bash
browser-agent --setup
```

If a config file already exists at `~/.browser_agent/config.yaml`, you'll be asked to confirm before overwriting:

```
Existing config found at C:\Users\you\.browser_agent\config.yaml
Overwrite with new settings? [y/N]: y
LLM API key (Gemini): ****
Model [gemini-1.5-flash]: gemini-2.0-flash
Default mode [safe]: hybrid
Default Playwright session name (optional):
Default start URL (optional):
Use npx playwright-cli by default? [y/N]: n
Saved config to C:\Users\you\.browser_agent\config.yaml
```

If no config exists, it runs the first-time setup wizard directly (same as what happens on the very first run).

---

## Memory

| Flag | Description |
|------|-------------|
| `--memory-status` | Print a summary of all stored lessons and exit. No task or browser is launched. |

```bash
browser-agent --memory-status
```

Example output:

```
Memory file: C:\Users\you\.browser_agent\memory.json
Total lessons: 9

TIER 1 — in system prompt (top 10):
  [seed]     best_practice    | uses=12  | "After entering text in a search box, press Enter..."
  [seed]     best_practice    | uses=8   | "If an overlay or popup is blocking an element..."
  [seed]     tool_fallback    | uses=6   | "If fill fails, click(ref) to focus the input..."

TIER 2 — recalled on demand:
  [learned]  error_recovery   | uses=3   | triggered_on=[youtube.com, google.com] | "When click fails with 'intercepts pointer', try press instead."
  [learned]  error_recovery   | uses=1   | triggered_on=[github.com]              | "When fill fails with 'timeout', try click instead."

Last updated: 2026-03-13
```

### Memory files

| File | Location | Purpose |
|------|----------|---------|
| `memory.json` | `~/.browser_agent/memory.json` | Persistent lesson store. Survives across runs. |
| `memory_events.jsonl` | `runs/<run_id>/memory_events.jsonl` | Per-run log of all memory events (loads, recalls, recordings). |

### How memory works

- **Tier 1** lessons (`tool_fallback`, `best_practice`) are injected into the LLM's system prompt at startup. Always active.
- **Tier 2** lessons (`error_recovery`, `site_specific`) are searched on demand when a command fails or a new domain is visited.
- **Post-run learning** scans the action log after each run for failure→recovery patterns and records new lessons.
- **Promotion**: `error_recovery` lessons are promoted to `best_practice` (Tier 1) after 5+ uses across 3+ domains.
- **Pruning**: Learned lessons older than 90 days with fewer than 5 uses are removed on load. Seed lessons are never pruned.

---

## Configuration File

Located at `~/.browser_agent/config.yaml`. Created interactively on first run.

```yaml
api_key: "your-gemini-api-key"
model: "gemini-1.5-flash"
mode: "safe"
max_steps: 50
max_errors: 5
max_retries: 3
max_elements: 60
max_visible_chars: 2000
min_visible_text: 200
session: ""
start_url: ""
use_npx: false
```

CLI flags override config file values. The precedence order is:

```
CLI flags > config.yaml > built-in defaults
```

---

## Run Artifacts

Each run creates a timestamped folder under `runs/`:

```
runs/run_20260313T143000Z/
├── run_meta.json            # Task, steps, stop reason, runtime
├── actions.jsonl            # Every action taken (command, result, approval)
├── llm_responses.jsonl      # Every LLM response (tool calls, reasoning)
├── browser_state.jsonl      # URL and title at each step
├── interpreter_state.jsonl  # Parsed page state at each step
├── memory_events.jsonl      # Memory loads, recalls, and recordings
├── snapshots/               # Raw DOM snapshots per step
│   ├── step_0001.txt
│   ├── step_0002.txt
│   └── ...
└── screenshots/             # (if captured)
```

---

## Full Examples

```bash
# Basic safe-mode task
browser-agent "Go to google.com and search for 'best pizza near me'"

# Autonomous with specific model and URL
browser-agent "Find the price of the first item" --auto --model gemini-2.0-flash --start-url https://shop.example.com

# Headed Firefox with debugging
browser-agent "Fill out the registration form" --hybrid --browser firefox --headed --debug

# Persistent profile for logged-in sessions
browser-agent "Check my notifications" --auto --start-url https://github.com --persistent --headed

# Just check what the agent has learned
browser-agent --memory-status
```

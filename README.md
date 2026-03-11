# Browser Agent (Playwright CLI)

A **DOM-driven browser agent** powered by Gemini's native function calling. The agent uses a **multi-turn chat** with structured tool calls — no free-text JSON parsing.

Architecture: **Snapshot → Interpreter → Multi-turn Chat (ReAct reasoning + function call) → Playwright CLI execution → Result fed back to chat**

Key design choices:
- **Native function calling** — the LLM returns typed `FunctionCall` objects, not free-text JSON. Eliminates parsing failures.
- **Multi-turn conversation** — the chat maintains history across steps. The LLM sees its prior reasoning and tool results.
- **Chain-of-thought** — the system prompt requires step-by-step reasoning (Observe → Think → Act) before every tool call.
- **Few-shot examples** — the system instruction includes worked examples of correct reasoning patterns.
- **Human-in-the-loop** — three approval modes (safe/hybrid/auto) gate risky actions.

## 1) Setup From Scratch

### 1.1 Requirements

- Python 3.11+
- Playwright CLI installed
- `google-genai` Python package (v1.66+)
- A Gemini API key

### 1.2 Install Python dependencies

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
  actions.jsonl         # Every action executed + approval status + stdout/stderr
  llm_responses.jsonl   # Tool calls + reasoning text from the LLM
  browser_state.jsonl   # URL, title, snapshot paths per step
  interpreter_state.jsonl  # Parsed page state per step
  run_meta.json         # Task, stop_reason, step count, runtime
```

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
playwright-cli open https://example.com
playwright-cli snapshot
playwright-cli close

browser-agent "open example.com" --safe
```

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

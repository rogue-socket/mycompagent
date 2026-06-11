# Repository Guidelines

## Commands
- Conda install: `/opt/miniconda3/bin/conda create -n mycompagent python=3.11 pip -y`, then `source /opt/miniconda3/bin/activate mycompagent`, `python -m pip install --upgrade pip`, `pip install -r requirements-browser-agent.txt`, `pip install -r requirements-dev.txt`, `pip install -e .`, and optionally `pip install -e ../codex-agent` for `--llm-provider codex`.
- Virtualenv alternative: `python3 -m venv .venv`, then `source .venv/bin/activate`, followed by the same pip install commands.
- Configure: `browser-agent --setup` creates or refreshes user-local config at `~/.browser_agent/config.yaml`.
- Run: `browser-agent "open example.com" --safe` starts the CLI with approval on every action. Use `--hybrid` or `--auto` only when the task risk is understood.
- Codex LLM run: `browser-agent "open example.com" --auto --llm-provider codex --start-url https://example.com`.
- Debug: `browser-agent "task" --debug --headed` records traces and video under ignored runtime artifact paths.
- Test: install `requirements-dev.txt`, then run `python -m pytest tests -q`. Use focused tests such as `python -m pytest tests/test_guardrails.py -q` for narrow changes.
- Lint/typecheck/build: no dedicated repo command is configured; do not invent one.

## Project Structure
- `browser_agent/`: Python 3.11+ package and CLI implementation. Main landmarks include `main.py`, `decision_loop.py`, `planner.py`, `interpreter.py`, `playwright_executor.py`, `guardrails.py`, `memory.py`, and `config_manager.py`.
- `tests/`: unit tests using a mix of `unittest` and pytest fixtures.
- `skills/`: Playwright CLI skill instructions and reference docs consumed by the agent.
- `docs/`: architecture, CLI, memory, and planning notes.
- `setup.py` and `requirements-browser-agent.txt`: package metadata and runtime dependencies.

## Working Style
- Make surgical changes and match the existing module style.
- Use 4-space indentation, `snake_case` for modules/functions, `PascalCase` for classes, and typed signatures for new Python code.
- Keep tests deterministic. Prefer mocks or small fixtures over live browser/network work unless the changed behavior requires integration coverage.
- Do not reformat, rename, or clean up unrelated code.

## Agent Behavior
- Absolutely no hardcoded task recipes, game solutions, site-specific answer paths, or baked-in completion sequences. Agents must discover from visible state, use persisted memory as learned evidence, and verify progress through observed page changes.
- Memory is allowed only as learned evidence. If a remembered step does not work in the current run, the agent must mark it as failed/stale for that run and explore alternatives instead of forcing the old path.

## Verification
- Run the narrowest relevant test first, then `python -m pytest tests -q` when shared behavior changes.
- For CLI-facing changes, include the exact `browser-agent ...` command used and note the mode (`--safe`, `--hybrid`, or `--auto`).
- If debug artifacts are needed, inspect `runs/<run_id>/` and `.playwright-cli/`; do not commit those files.

## Safety
- Do not commit API keys, `~/.browser_agent/config.yaml`, browser profiles, storage state, traces, videos, or run logs.
- Ask before destructive Playwright commands such as `playwright-cli close-all`, `kill-all`, or `delete-data`.
- Preserve human approval for login, payment, account, and data-modifying flows.

## Session docs
- `backlog.md`: Codex working backlog if created by `$start`; use `[active]`, `[next]`, and `[blocked: reason]` tags.
- `handoffs/`: dated session handoffs if created by `$start`/`$wrap` or imported from Claude.
- `.codex/memory/claude/`: migrated Claude memory if present. No matching Claude sessions or memory were found during init.
- `.agents/skills/`: project-local Codex skills if migrated later. The repository's existing agent-facing skill docs live in `skills/`.

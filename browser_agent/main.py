"""CLI entrypoint for DOM-driven browser agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from browser_agent.config_manager import ConfigError, ConfigManager
from browser_agent.decision_loop import DecisionLoop
from browser_agent.logger import create_run_paths
from browser_agent.playwright_executor import PlaywrightExecutor
from browser_agent.planner import GeminiPlanner
from browser_agent.skill_checker import SkillCheckError, check_playwright_skill
from browser_agent.skill_loader import SkillLoadError, load_skill_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DOM-driven Playwright browser agent")
    parser.add_argument("task", help="Natural language task to execute")
    parser.add_argument("--safe", action="store_true", help="Require approval for every action")
    parser.add_argument("--hybrid", action="store_true", help="Require approval for risky actions")
    parser.add_argument("--auto", action="store_true", help="Fully autonomous mode")
    parser.add_argument("--model", type=str, default=None, help="LLM model override")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum agent steps")
    parser.add_argument("--session", type=str, default=None, help="Playwright CLI session name")
    parser.add_argument("--start-url", type=str, default=None, help="Open browser at this URL")
    parser.add_argument("--persistent", action="store_true", help="Use persistent browser profile")
    parser.add_argument("--profile", type=str, default=None, help="Use profile directory")
    parser.add_argument("--browser", type=str, default=None, help="Browser type (chrome/firefox/webkit)")
    parser.add_argument("--headed", action="store_true", help="Launch headed browser")
    parser.add_argument("--config", type=str, default=None, help="Playwright CLI config file")
    parser.add_argument("--use-npx", action="store_true", help="Use npx playwright-cli")
    parser.add_argument("--debug", action="store_true", help="Enable tracing and video")
    return parser


def _resolve_mode(args: argparse.Namespace) -> str | None:
    selected = [
        mode
        for mode, enabled in (
            ("safe", args.safe),
            ("hybrid", args.hybrid),
            ("auto", args.auto),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise ValueError("Only one mode flag can be specified")
    return selected[0] if selected else None


def _build_open_args(args: argparse.Namespace) -> list[str]:
    open_args: list[str] = []
    if args.persistent:
        open_args.append("--persistent")
    if args.profile:
        open_args.append(f"--profile={args.profile}")
    if args.browser:
        open_args.append(f"--browser={args.browser}")
    if args.headed:
        open_args.append("--headed")
    if args.config:
        open_args.append(f"--config={args.config}")
    return open_args


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        mode_override = _resolve_mode(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    manager = ConfigManager()
    try:
        config = manager.load()
        config = manager.merge_overrides(
            config,
            model=args.model,
            mode=mode_override,
            max_steps=args.max_steps,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    run_id, paths = create_run_paths()
    print(f"Run ID: {run_id}")

    try:
        repo_root = Path(__file__).resolve().parents[1]
        skill_name, skill_path = check_playwright_skill(repo_root)
        skill_text = load_skill_text(Path(skill_path))
        print(f"Skill check: {skill_name} ({skill_path})")
    except (SkillCheckError, SkillLoadError) as exc:
        print(f"Skill check failed: {exc}", file=sys.stderr)
        return 2

    planner = GeminiPlanner(api_key=str(config["api_key"]), model_name=str(config["model"]))
    session = args.session or str(config.get("session") or "") or None
    start_url = args.start_url or str(config.get("start_url") or "") or None
    use_npx = bool(args.use_npx) or bool(config.get("use_npx", False))
    executor = PlaywrightExecutor(session=session, use_npx=use_npx)

    loop = DecisionLoop(
        task=args.task,
        mode=str(config.get("mode", "safe")),
        planner=planner,
        config=config,
        paths=paths,
        executor=executor,
        open_url=start_url,
        open_args=_build_open_args(args),
        debug=args.debug,
        skill_text=skill_text,
    )

    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

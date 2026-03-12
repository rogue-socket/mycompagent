"""CLI entrypoint for DOM-driven browser agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from browser_agent.config_manager import ConfigError, ConfigManager
from browser_agent.decision_loop import DecisionLoop
from browser_agent.logger import append_jsonl, create_run_paths
from browser_agent.memory import MAX_TIER1, MemoryStore, _TIER1_CATEGORIES
from browser_agent.playwright_executor import PlaywrightExecutor
from browser_agent.planner import ChatPlanner
from browser_agent.prompt_builder import build_system_instruction
from browser_agent.skill_checker import SkillCheckError, check_playwright_skill
from browser_agent.skill_loader import SkillLoadError, load_skill_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DOM-driven Playwright browser agent")
    parser.add_argument("task", nargs="?", default=None, help="Natural language task to execute")
    parser.add_argument("--memory-status", action="store_true", help="Print memory status and exit")
    parser.add_argument("--setup", action="store_true", help="Re-run interactive config setup and exit")
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


def _print_memory_status() -> int:
    """Print a human-readable summary of the memory store and exit."""
    memory = MemoryStore()
    memory.load()

    print(f"Memory file: {memory.path}")
    print(f"Total lessons: {len(memory.lessons)}")

    tier1 = [ls for ls in memory.lessons if ls.category in _TIER1_CATEGORIES]
    tier1.sort(key=lambda ls: (-ls.use_count, ls.source != "seed"))
    tier2 = [ls for ls in memory.lessons if ls.category not in _TIER1_CATEGORIES]

    if tier1:
        print(f"\nTIER 1 — in system prompt (top {MAX_TIER1}):")
        for ls in tier1:
            tag = f"[{ls.source}]"
            print(f"  {tag:<10} {ls.category:<16} | uses={ls.use_count:<3} | \"{ls.lesson}\"")
    else:
        print("\nNo Tier 1 lessons.")

    if tier2:
        print("\nTIER 2 — recalled on demand:")
        for ls in tier2:
            tag = f"[{ls.source}]"
            domains = ", ".join(ls.triggered_domains) if ls.triggered_domains else "none"
            domain_info = f" | domain={ls.domain}" if ls.domain else ""
            print(
                f"  {tag:<10} {ls.category:<16} | uses={ls.use_count:<3} "
                f"| triggered_on=[{domains}]{domain_info} | \"{ls.lesson}\""
            )
    else:
        print("\nNo Tier 2 lessons.")

    if memory.lessons:
        newest = max(ls.last_used for ls in memory.lessons)
        print(f"\nLast updated: {newest}")

    return 0


def _run_setup() -> int:
    """Re-run interactive configuration setup from scratch."""
    manager = ConfigManager()
    if manager.exists():
        print(f"Existing config found at {manager.config_path}")
        confirm = input("Overwrite with new settings? [y/N]: ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Setup cancelled.")
            return 0
    manager.first_run_setup()
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.memory_status:
        return _print_memory_status()

    if args.setup:
        return _run_setup()

    if not args.task:
        parser.error("task is required (unless using --memory-status or --setup)")

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

    memory = MemoryStore(
        on_event=lambda evt: append_jsonl(paths.memory_events_log, evt),
    )
    memory.load()
    tier1 = memory.get_tier1()
    print(
        f"Memory loaded from {memory.path} "
        f"({len(memory.lessons)} lessons, {len(tier1)} tier-1)"
    )

    planner = ChatPlanner(
        api_key=str(config["api_key"]),
        model_name=str(config["model"]),
        system_instruction=build_system_instruction(
            args.task, skill_text, tier1_lessons=tier1
        ),
    )
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
        memory=memory,
    )

    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

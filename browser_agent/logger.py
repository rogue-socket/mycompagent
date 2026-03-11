"""Structured run logging for browser agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunPaths:
    """Filesystem paths for a single run."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshots = root / "snapshots"
        self.screenshots = root / "screenshots"
        self.actions_log = root / "actions.jsonl"
        self.llm_log = root / "llm_responses.jsonl"
        self.browser_state_log = root / "browser_state.jsonl"
        self.interpreter_state_log = root / "interpreter_state.jsonl"
        self.reasoning_log = root / "agent_reasoning.jsonl"
        self.run_meta = root / "run_meta.json"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_run_paths(base_dir: str = "runs") -> tuple[str, RunPaths]:
    run_id = f"run_{_utc_stamp()}"
    root = Path(base_dir) / run_id
    root.mkdir(parents=True, exist_ok=True)
    paths = RunPaths(root)
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    paths.screenshots.mkdir(parents=True, exist_ok=True)
    return run_id, paths


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def write_run_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def write_snapshot(path: Path, step: int, snapshot_text: str) -> Path:
    filename = path / f"step_{step:04d}.txt"
    filename.write_text(snapshot_text, encoding="utf-8")
    return filename

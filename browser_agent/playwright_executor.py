"""Execute Playwright CLI commands."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable


class PlaywrightExecutionError(RuntimeError):
    """Raised when Playwright CLI execution fails."""


@dataclass(slots=True)
class CommandResult:
    """Result of a Playwright CLI command."""

    command: str
    returncode: int
    stdout: str
    stderr: str


class PlaywrightExecutor:
    """Wrapper for running Playwright CLI commands with session support."""

    def __init__(self, session: str | None = None, use_npx: bool = False) -> None:
        self.session = session
        self.use_npx = use_npx

    def run(self, command: str, timeout: float = 45.0) -> CommandResult:
        args = _build_command(command, session=self.session, use_npx=self.use_npx)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PlaywrightExecutionError(
                "playwright-cli not found on PATH. Install it or use --use-npx."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PlaywrightExecutionError(f"Command timed out: {command}") from exc

        return CommandResult(
            command=" ".join(args),
            returncode=proc.returncode,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
        )

    def snapshot(self) -> CommandResult:
        return self.run("playwright-cli snapshot")

    def screenshot(self) -> CommandResult:
        return self.run("playwright-cli screenshot")


def _build_command(command: str, *, session: str | None, use_npx: bool) -> list[str]:
    parts = shlex.split(command)
    if not parts or parts[0] != "playwright-cli":
        raise PlaywrightExecutionError("Commands must start with 'playwright-cli'")

    base: list[str] = ["playwright-cli"]
    if use_npx:
        base = ["npx", "playwright-cli"]

    tail = parts[1:]
    if session and not _has_session_flag(tail):
        tail = [f"-s={session}"] + tail

    return base + tail


def _has_session_flag(args: Iterable[str]) -> bool:
    for idx, arg in enumerate(args):
        if arg.startswith("-s="):
            return True
        if arg in {"-s", "--session"}:
            return True
        if arg.startswith("--session="):
            return True
        if arg == "-s" and idx + 1 < len(args):
            return True
    return False

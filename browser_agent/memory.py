"""Two-tiered memory system for the browser agent.

Tier 1 (proactive): Universal lessons loaded into the system prompt.
    - Category-based: only ``tool_fallback`` and ``best_practice`` qualify.
    - Capped at 10 items.
    - Seeded with known truths; learned lessons can be promoted.

Tier 2 (reactive): Searched on demand via structured field matching.
    - Triggered on command failure, new domain, or stuck detection.
    - Injected into the per-step user message when relevant.

Post-run learning: Scans the actions log for failure→recovery patterns
and records new lessons or increments existing ones.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

# Type for the optional event callback.
MemoryEventCallback = Callable[[dict[str, Any]], None]

# Categories that qualify for Tier 1 (always in system prompt).
_TIER1_CATEGORIES = {"tool_fallback", "best_practice"}

# Promotion thresholds.
_PROMOTE_USE_COUNT = 5
_PROMOTE_DOMAIN_COUNT = 3

# Pruning thresholds.
_PRUNE_MAX_AGE_DAYS = 90
_PRUNE_MIN_USES = 5

# Maximum Tier 1 lessons in the system prompt.
MAX_TIER1 = 10

DEFAULT_MEMORY_PATH = "~/.browser_agent/memory.json"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Lesson:
    lesson: str
    category: str  # tool_fallback | best_practice | error_recovery | site_specific
    failed_command: str | None = None
    error_pattern: str | None = None
    domain: str | None = None
    use_count: int = 0
    created_at: str = ""
    last_used: str = ""
    source: str = "learned"  # "seed" | "learned"
    triggered_domains: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _today()
        if not self.last_used:
            self.last_used = _today()


# ---------------------------------------------------------------------------
# Memory store
# ---------------------------------------------------------------------------


class MemoryStore:
    """Persistent lesson storage with tiered retrieval."""

    def __init__(
        self,
        path: str | Path | None = None,
        on_event: MemoryEventCallback | None = None,
    ) -> None:
        self.path = Path(path or DEFAULT_MEMORY_PATH).expanduser()
        self.lessons: list[Lesson] = []
        self._on_event = on_event

    def _emit(self, event: dict[str, Any]) -> None:
        """Send an event to the registered callback, if any."""
        if self._on_event is not None:
            self._on_event(event)

    # -- persistence --

    def load(self) -> None:
        """Load lessons from disk.  Seeds defaults on first run."""
        if not self.path.exists():
            self.seed_defaults()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("expected a dict")
            self.lessons = [Lesson(**item) for item in raw.get("lessons", [])]
        except (json.JSONDecodeError, TypeError):
            self.lessons = []
            self.seed_defaults()
        self.prune_stale()

    def save(self) -> None:
        """Persist lessons to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "lessons": [asdict(item) for item in self.lessons],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- Tier 1: always loaded --

    def get_tier1(self, max_items: int = MAX_TIER1) -> list[Lesson]:
        """Return universal lessons eligible for the system prompt."""
        candidates = [ls for ls in self.lessons if ls.category in _TIER1_CATEGORIES]
        candidates.sort(key=lambda ls: (-ls.use_count, ls.source != "seed"))
        result = candidates[:max_items]
        self._emit({
            "event": "tier1_loaded",
            "count": len(result),
            "lessons": [ls.lesson for ls in result],
        })
        return result

    # -- Tier 2: searched on demand --

    def recall_on_error(self, command: str, error: str) -> list[Lesson]:
        """Find lessons matching a failed command or error pattern."""
        matches: list[tuple[int, Lesson]] = []
        for lesson in self.lessons:
            score = 0
            if lesson.failed_command and lesson.failed_command == command:
                score += 2
            if lesson.error_pattern and lesson.error_pattern in error.lower():
                score += 3
            if score > 0:
                matches.append((score, lesson))
        matches.sort(key=lambda x: -x[0])
        result = [item for _, item in matches[:3]]
        self._emit({
            "event": "error_recall",
            "command": command,
            "error_snippet": error[:120],
            "matched": len(result),
            "lessons": [ls.lesson for ls in result],
        })
        return result

    def recall_on_domain(self, domain: str) -> list[Lesson]:
        """Find site-specific lessons for a domain."""
        result = [
            ls
            for ls in self.lessons
            if ls.domain
            and (ls.domain == domain or domain.endswith("." + ls.domain))
        ][:3]
        self._emit({
            "event": "domain_recall",
            "domain": domain,
            "matched": len(result),
            "lessons": [ls.lesson for ls in result],
        })
        return result

    # -- Recording and updating --

    def increment_use(self, lesson: Lesson, domain: str | None = None) -> None:
        """Record that a lesson was triggered."""
        lesson.use_count += 1
        lesson.last_used = _today()
        if domain and domain not in lesson.triggered_domains:
            lesson.triggered_domains.append(domain)
        self._maybe_promote(lesson)

    def record_lesson(self, lesson: Lesson) -> None:
        """Add a new lesson if no duplicate exists."""
        for existing in self.lessons:
            if (
                existing.failed_command == lesson.failed_command
                and existing.error_pattern == lesson.error_pattern
            ):
                self._emit({
                    "event": "lesson_deduplicated",
                    "lesson": existing.lesson,
                    "new_use_count": existing.use_count + 1,
                })
                self.increment_use(existing)
                return
        self.lessons.append(lesson)
        self._emit({
            "event": "lesson_recorded",
            "lesson": lesson.lesson,
            "category": lesson.category,
            "failed_command": lesson.failed_command,
            "error_pattern": lesson.error_pattern,
        })

    # -- Promotion --

    def _maybe_promote(self, lesson: Lesson) -> None:
        """Auto-promote error_recovery → best_practice if universal enough."""
        if lesson.category != "error_recovery":
            return
        if lesson.domain is not None:
            return
        if (
            lesson.use_count >= _PROMOTE_USE_COUNT
            and len(lesson.triggered_domains) >= _PROMOTE_DOMAIN_COUNT
        ):
            lesson.category = "best_practice"
            self._emit({
                "event": "lesson_promoted",
                "lesson": lesson.lesson,
                "use_count": lesson.use_count,
                "triggered_domains": list(lesson.triggered_domains),
            })

    # -- Pruning --

    def prune_stale(self, max_age_days: int = _PRUNE_MAX_AGE_DAYS) -> None:
        """Remove learned lessons that are old and rarely used."""
        cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
        before = len(self.lessons)
        self.lessons = [
            ls
            for ls in self.lessons
            if ls.source == "seed"
            or ls.last_used >= cutoff
            or ls.use_count >= _PRUNE_MIN_USES
        ]
        pruned = before - len(self.lessons)
        if pruned:
            self._emit({
                "event": "lessons_pruned",
                "pruned_count": pruned,
                "remaining_count": len(self.lessons),
            })

    # -- Seeding --

    def seed_defaults(self) -> None:
        """Populate with known universal lessons on first run."""
        seeds = [
            Lesson(
                lesson=(
                    "If fill fails, click(ref) to focus the input, "
                    "then type(text) to enter text."
                ),
                category="tool_fallback",
                failed_command="fill",
                error_pattern="too many arguments",
                source="seed",
            ),
            Lesson(
                lesson=(
                    "After entering text in a search box, press Enter to submit. "
                    "Avoid clicking submit buttons — autocomplete dropdowns "
                    "often cover them and cause timeout errors."
                ),
                category="best_practice",
                failed_command="click",
                error_pattern="intercepts pointer",
                source="seed",
            ),
            Lesson(
                lesson=(
                    "If an overlay or popup is blocking an element, press Escape "
                    "to dismiss it before interacting with elements behind it."
                ),
                category="best_practice",
                error_pattern="intercepts pointer",
                source="seed",
            ),
        ]
        for seed in seeds:
            if not any(ls.lesson == seed.lesson for ls in self.lessons):
                self.lessons.append(seed)
        self.save()


# ---------------------------------------------------------------------------
# Post-run learning
# ---------------------------------------------------------------------------

# Error messages that are too generic to learn from.
_SKIP_ERROR_PATTERNS = {"not found", "no such element"}

# Known error phrases worth extracting as patterns.
_KNOWN_ERROR_PHRASES = [
    "too many arguments",
    "intercepts pointer events",
    "timeout",
    "element is not visible",
    "element is not enabled",
    "frame was detached",
    "target closed",
    "navigation interrupted",
]


def extract_lessons_from_run(actions_log: Path, memory: MemoryStore) -> None:
    """Scan a run's action log for failure→recovery patterns and learn."""
    if not actions_log.exists():
        return

    actions = _load_jsonl(actions_log)
    if len(actions) < 2:
        return

    for i in range(len(actions) - 1):
        curr = actions[i]
        nxt = actions[i + 1]

        if curr.get("execution_result") != "error":
            continue
        if nxt.get("execution_result") != "ok":
            continue

        failed_cmd = _extract_command_name(curr.get("command", ""))
        recovery_cmd = _extract_command_name(nxt.get("command", ""))
        error_text = curr.get("stderr", "")

        if not _is_worthy_lesson(failed_cmd, recovery_cmd, error_text):
            continue

        error_phrase = _extract_key_phrase(error_text)
        domain = _extract_domain_from_stdout(nxt.get("stdout", ""))

        lesson = Lesson(
            lesson=(
                f"When {failed_cmd} fails with '{_short_error(error_text)}', "
                f"try {recovery_cmd} instead."
            ),
            category="error_recovery",
            failed_command=failed_cmd,
            error_pattern=error_phrase,
            domain=None,
            use_count=1,
            source="learned",
            triggered_domains=[domain] if domain else [],
        )
        memory.record_lesson(lesson)

    memory.save()


def _is_worthy_lesson(failed_cmd: str, recovery_cmd: str, error_text: str) -> bool:
    if not failed_cmd or not recovery_cmd:
        return False
    if failed_cmd == recovery_cmd:
        return False
    error_lower = error_text.lower()
    if any(skip in error_lower for skip in _SKIP_ERROR_PATTERNS):
        return False
    if len(error_text.strip()) < 10:
        return False
    return True


def _extract_key_phrase(error_text: str) -> str | None:
    error_lower = error_text.lower()
    for phrase in _KNOWN_ERROR_PHRASES:
        if phrase in error_lower:
            return phrase
    first_line = error_lower.strip().split("\n")[0][:80]
    return first_line if first_line else None


def _short_error(error_text: str) -> str:
    first_line = error_text.strip().split("\n")[0]
    return first_line[:60]


def _extract_command_name(command: str) -> str:
    """Extract the CLI command name from a full command string."""
    parts = command.split()
    if len(parts) >= 2 and parts[0] == "playwright-cli":
        return parts[1]
    if parts:
        return parts[0]
    return ""


def _extract_domain_from_stdout(stdout: str) -> str | None:
    """Try to extract a domain from command output."""
    for line in stdout.splitlines():
        if "Page URL:" in line:
            url = line.split("Page URL:", 1)[1].strip()
            return _domain_from_url(url)
    return None


def _domain_from_url(url: str) -> str | None:
    """Extract domain from a URL string."""
    if "://" in url:
        after = url.split("://", 1)[1]
        host = after.split("/", 1)[0].split(":")[0]
        return host if host else None
    return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of dicts."""
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries

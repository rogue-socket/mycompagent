"""Two-tiered memory system for the browser agent.

Tier 1 (proactive): Universal lessons loaded into the system prompt.
    - Category-based: only ``tool_fallback`` and ``best_practice`` qualify.
    - Capped at 10 items.
    - Seeded with known truths; learned lessons can be promoted.

Tier 2 (reactive): Searched on demand via structured field matching.
    - Triggered on command failure or new domain.
    - Injected into the per-step user message when relevant.

Post-run learning: Scans the actions log for failure→recovery patterns
and records new lessons or increments existing ones.
"""

from __future__ import annotations

import json
import re
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
_GENERIC_SINGLE_COMMAND_RECOVERY_RE = re.compile(
    r",\s*try\s+[a-z][a-z0-9_-]*\s+instead\.\s*$", re.IGNORECASE
)


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

    def recall_on_domain(self, domain: str, max_items: int = 60) -> list[Lesson]:
        """Find site-specific lessons for a domain."""
        matches = [
            ls
            for ls in self.lessons
            if ls.domain
            and (ls.domain == domain or domain.endswith("." + ls.domain))
        ]
        matches.sort(
            key=lambda ls: (
                _domain_lesson_priority(ls),
                -_domain_lesson_recency(ls),
                -ls.use_count,
                ls.lesson,
            )
        )
        result = matches[:max_items]
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
        new_combo = _combination_lesson_parts(lesson)
        for existing in self.lessons:
            existing_combo = _combination_lesson_parts(existing)
            if (
                new_combo
                and existing_combo
                and existing.domain == lesson.domain
                and existing_combo[:2] == new_combo[:2]
                and existing_combo[2] != new_combo[2]
            ):
                self._emit({
                    "event": "lesson_conflict_ignored",
                    "existing": existing.lesson,
                    "ignored": lesson.lesson,
                })
                return
            if _is_duplicate_lesson(existing, lesson):
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
        if _is_generic_single_command_recovery(lesson):
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


def _domain_lesson_priority(lesson: Lesson) -> int:
    text = lesson.lesson.lower()
    if "uses an inventory plus canvas model" in text:
        return 0
    if "drag can succeed mechanically" in text:
        return 1
    if text.startswith("observed combination"):
        return 2
    return 3


def _domain_lesson_recency(lesson: Lesson) -> int:
    digits = re.sub(r"\D", "", lesson.created_at or "")
    if not digits:
        return 0
    try:
        return int(digits[:8])
    except ValueError:
        return 0


def _is_generic_single_command_recovery(lesson: Lesson) -> bool:
    """Return whether a learned recovery is too vague for Tier 1 promotion."""
    return bool(_GENERIC_SINGLE_COMMAND_RECOVERY_RE.search(lesson.lesson))


def _is_duplicate_lesson(existing: Lesson, lesson: Lesson) -> bool:
    """Return whether two lessons represent the same stored learning."""
    has_recovery_key = any(
        (
            existing.failed_command,
            existing.error_pattern,
            lesson.failed_command,
            lesson.error_pattern,
        )
    )
    if has_recovery_key:
        return (
            existing.failed_command == lesson.failed_command
            and existing.error_pattern == lesson.error_pattern
        )
    return (
        existing.category == lesson.category
        and existing.domain == lesson.domain
        and existing.lesson == lesson.lesson
    )


def _combination_lesson_parts(lesson: Lesson) -> tuple[str, str, str] | None:
    if lesson.category != "site_specific":
        return None
    match = re.search(
        r"Observed combination on this drag-to-combine page:\s*(.+?)\s+\+\s+(.+?)\s+created\s+(.+?)\.",
        lesson.lesson,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    first = _normalize_crafting_label(match.group(1)).lower()
    second = _normalize_crafting_label(match.group(2)).lower()
    result = _normalize_crafting_label(match.group(3)).lower()
    if not first or not second or not result:
        return None
    pair = tuple(sorted((first, second)))
    return pair[0], pair[1], result


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

_MAX_SHORT_RECOVERY_ACTIONS = 3


def extract_lessons_from_run(
    actions_log: Path,
    memory: MemoryStore,
    *,
    interpreter_state_log: Path | None = None,
) -> None:
    """Scan a run's action log for failure→recovery patterns and learn."""
    if not actions_log.exists():
        return

    actions = _load_jsonl(actions_log)
    if len(actions) < 2:
        return

    for i in range(len(actions) - 1):
        curr = actions[i]
        if curr.get("execution_result") != "error":
            continue

        recovery_actions = _collect_recovery_actions(actions, i + 1)
        if not recovery_actions:
            continue

        failed_cmd = _extract_command_name(curr.get("command", ""))
        recovery_cmd = _extract_command_name(recovery_actions[0].get("command", ""))
        error_text = curr.get("stderr", "")

        if not _is_worthy_lesson(failed_cmd, recovery_cmd, error_text):
            continue

        error_phrase = _extract_key_phrase(error_text)
        domain = _extract_domain_from_actions(recovery_actions)
        recovery_text = _describe_recovery(curr, recovery_actions)

        lesson = Lesson(
            lesson=(
                f"When {failed_cmd} fails with '{_short_error(error_text)}', "
                f"{recovery_text}."
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

    if interpreter_state_log is not None and interpreter_state_log.exists():
        _extract_drag_crafting_lessons(actions, _load_jsonl(interpreter_state_log), memory)

    memory.save()


def _extract_drag_crafting_lessons(
    actions: list[dict[str, Any]],
    states: list[dict[str, Any]],
    memory: MemoryStore,
) -> None:
    if not actions or len(states) < 2:
        return
    domain = _extract_domain_from_states(states)
    if not domain:
        return

    states_by_step = {
        int(state.get("step", -1)): state
        for state in states
        if isinstance(state.get("step"), int)
    }
    if not any(_looks_like_drag_crafting_state(state) for state in states):
        return

    if _learned_canvas_copy_transition(actions, states_by_step):
        memory.record_lesson(
            Lesson(
                lesson=(
                    "This drag-to-combine page uses an inventory plus canvas model: "
                    "if inventory-to-inventory drags do not add items, click or drag an "
                    "inventory item to create a canvas copy, then combine items on the canvas."
                ),
                category="site_specific",
                domain=domain,
                use_count=1,
                source="learned",
                triggered_domains=[domain],
            )
        )

    if _learned_noop_drag_exploration(actions, states_by_step):
        memory.record_lesson(
            Lesson(
                lesson=(
                    "On this drag-to-combine page, a drag can succeed mechanically "
                    "while creating no new inventory item. Treat that pair as failed "
                    "for the current run, avoid retrying it, and explore a different "
                    "visible pair."
                ),
                category="site_specific",
                domain=domain,
                use_count=1,
                source="learned",
                triggered_domains=[domain],
            )
        )

    for action in actions:
        if _extract_command_name(str(action.get("command", ""))) != "drag":
            continue
        step = int(action.get("step", -1))
        before = states_by_step.get(step)
        after = states_by_step.get(step + 1)
        if not before or not after:
            continue
        before_items = _crafting_inventory_items(before)
        after_items = _crafting_inventory_items(after)
        if not before_items or not after_items:
            continue
        new_items = [item for item in after_items if item not in before_items]
        if not new_items:
            continue
        pair = _dragged_pair_from_action(action)
        if not pair:
            continue
        for item in new_items[:3]:
            memory.record_lesson(
                Lesson(
                    lesson=(
                        f"Observed combination on this drag-to-combine page: "
                        f"{pair[0]} + {pair[1]} created {item}."
                    ),
                    category="site_specific",
                    domain=domain,
                    use_count=1,
                    source="learned",
                    triggered_domains=[domain],
                )
            )


def _extract_domain_from_states(states: list[dict[str, Any]]) -> str | None:
    for state in states:
        domain = _domain_from_url(str(state.get("url") or ""))
        if domain:
            return domain
    return None


def _looks_like_drag_crafting_state(state: dict[str, Any]) -> bool:
    visible_text = str(state.get("visible_text") or "").lower()
    clickable_text = " ".join(
        str(element.get("text") or "")
        for element in state.get("clickable_elements") or []
        if isinstance(element, dict)
    ).lower()
    haystack = f"{visible_text}\n{clickable_text}"
    return (
        re.search(r"\bitems?\s+\d+\b", visible_text) is not None
        and any(marker in haystack for marker in ("search items", "clear canvas", "recipes"))
    )


def _crafting_inventory_items(state: dict[str, Any]) -> list[str]:
    visible_text = str(state.get("visible_text") or "")
    lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
    items: list[str] = []
    in_items = False
    for line in lines:
        if re.fullmatch(r"Items?\s+\d+", line, flags=re.IGNORECASE):
            in_items = True
            continue
        if in_items and line.lower() in {"discoveries", "advertisement", "menu"}:
            break
        if in_items:
            label = _normalize_crafting_label(line)
            if label:
                items.append(label)
    return items


def _learned_canvas_copy_transition(
    actions: list[dict[str, Any]],
    states_by_step: dict[int, dict[str, Any]],
) -> bool:
    saw_noop_drag = False
    for action in actions:
        step = int(action.get("step", -1))
        command = _extract_command_name(str(action.get("command", "")))
        before = states_by_step.get(step)
        after = states_by_step.get(step + 1)
        if not before or not after:
            continue
        before_items = _crafting_inventory_items(before)
        after_items = _crafting_inventory_items(after)
        if command == "drag" and before_items == after_items:
            saw_noop_drag = True
        if command == "click" and saw_noop_drag and _canvas_item_count(after) > _canvas_item_count(before):
            return True
    return False


def _learned_noop_drag_exploration(
    actions: list[dict[str, Any]],
    states_by_step: dict[int, dict[str, Any]],
) -> bool:
    for action in actions:
        if _extract_command_name(str(action.get("command", ""))) != "drag":
            continue
        if not _dragged_pair_from_action(action):
            continue
        step = int(action.get("step", -1))
        before = states_by_step.get(step)
        after = states_by_step.get(step + 1)
        if not before or not after:
            continue
        before_items = _crafting_inventory_items(before)
        after_items = _crafting_inventory_items(after)
        if before_items and before_items == after_items:
            return True
    return False


def _canvas_item_count(state: dict[str, Any]) -> int:
    count = 0
    for element in state.get("clickable_elements") or []:
        text = str(element.get("text") or "")
        lowered = text.lower()
        if "generic card" in text and " | " in text and not any(
            marker in lowered
            for marker in ("discoveries", "dark mode", "clear canvas", "recipes", "menu", "mute")
        ):
            count += 1
    return count


def _dragged_pair_from_action(action: dict[str, Any]) -> tuple[str, str] | None:
    stdout = str(action.get("stdout") or "")
    match = re.search(r'"Dragged\s+(.+?)\s+to\s+(.+?)"', stdout)
    if not match:
        return None
    first = _normalize_crafting_label(match.group(1))
    second = _normalize_crafting_label(match.group(2))
    if not first or not second:
        return None
    return first, second


def _normalize_crafting_label(value: str) -> str:
    value = re.sub(r"^[^\w#]+", "", value.strip(), flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _collect_recovery_actions(
    actions: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    successful: list[dict[str, Any]] = []

    for action in actions[start_index:]:
        result = action.get("execution_result")
        if result == "ok":
            successful.append(action)
            if len(successful) > _MAX_SHORT_RECOVERY_ACTIONS:
                return successful[:1]
            continue
        if result == "completed":
            return successful
        break

    return successful[:1]


def _extract_domain_from_actions(actions: list[dict[str, Any]]) -> str | None:
    for action in actions:
        domain = _extract_domain_from_stdout(action.get("stdout", ""))
        if domain:
            return domain
    return None


def _describe_recovery(
    failed_action: dict[str, Any], recovery_actions: list[dict[str, Any]]
) -> str:
    select_recovery = _describe_select_recovery(failed_action, recovery_actions)
    if select_recovery:
        return select_recovery

    phrases = [_describe_recovery_action(action) for action in recovery_actions]
    if len(phrases) == 1:
        command = _extract_command_name(recovery_actions[0].get("command", ""))
        if phrases[0] == command:
            return f"try {command} instead"
    return ", then ".join(phrases)


def _describe_select_recovery(
    failed_action: dict[str, Any], recovery_actions: list[dict[str, Any]]
) -> str | None:
    if _extract_command_name(failed_action.get("command", "")) != "select":
        return None
    if len(recovery_actions) < 2:
        return None

    first, second = recovery_actions[0], recovery_actions[1]
    if _extract_command_name(first.get("command", "")) != "click":
        return None
    if _extract_command_name(second.get("command", "")) != "click":
        return None

    first_kind = _target_kind(first)
    second_kind = _target_kind(second)
    if first_kind not in {"button", "combobox"} or second_kind != "option":
        return None

    control_name = "combobox" if first_kind == "combobox" else "control"
    option_name = "matching option"
    if not _target_matches_select_value(failed_action, second):
        option_name = "option"
    return f"click the {control_name}, then click the {option_name}"


def _describe_recovery_action(action: dict[str, Any]) -> str:
    command = _extract_command_name(action.get("command", ""))
    target_kind = _target_kind(action)
    if command == "click" and target_kind in {"button", "combobox", "option"}:
        return f"click the {target_kind}"
    return command


def _target_kind(action: dict[str, Any]) -> str | None:
    target = action.get("target")
    if not isinstance(target, dict):
        return None
    description = str(target.get("description") or "").strip().lower()
    if not description:
        return None
    return description.split(maxsplit=1)[0].rstrip(":")


def _target_matches_select_value(
    failed_action: dict[str, Any], recovery_action: dict[str, Any]
) -> bool:
    requested_value = _select_requested_value(failed_action.get("command", ""))
    if not requested_value:
        return False
    target = recovery_action.get("target")
    if not isinstance(target, dict):
        return False
    label = str(target.get("label") or "").strip()
    return label.lower() == requested_value.lower()


def _select_requested_value(command: str) -> str | None:
    parts = command.split(maxsplit=3)
    if len(parts) >= 4 and parts[0] == "playwright-cli" and parts[1] == "select":
        return parts[3].strip("\"'")

    parts = command.split(maxsplit=2)
    if len(parts) >= 3 and parts[0] == "select":
        return parts[2].strip("\"'")
    return None


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

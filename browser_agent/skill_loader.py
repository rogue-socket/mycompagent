"""Load skill content for prompt guidance."""

from __future__ import annotations

from pathlib import Path


class SkillLoadError(RuntimeError):
    """Raised when skill content cannot be loaded."""


def load_skill_text(skill_path: Path) -> str:
    try:
        raw = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillLoadError(f"Unable to read skill file: {skill_path}") from exc

    text = _strip_frontmatter(raw).strip()
    if not text:
        raise SkillLoadError(f"Skill file is empty after frontmatter: {skill_path}")
    return text


def _strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[idx + 1 :])
    return text

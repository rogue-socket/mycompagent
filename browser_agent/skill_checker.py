"""Skill presence and metadata checks."""

from __future__ import annotations

from pathlib import Path


class SkillCheckError(RuntimeError):
    """Raised when Playwright CLI skill is missing or invalid."""


def check_playwright_skill(repo_root: Path) -> tuple[str, str]:
    """Return (name_line, path) if present, otherwise raise."""
    skill_path = repo_root / "skills" / "playwright-cli" / "SKILL.md"
    if not skill_path.exists():
        raise SkillCheckError("Playwright CLI skill not found")

    content = skill_path.read_text(encoding="utf-8")
    name_line = _find_name_line(content)
    if "playwright-cli" not in name_line:
        raise SkillCheckError(f"{skill_path} does not declare playwright-cli")
    return name_line.strip(), str(skill_path)


def _find_name_line(content: str) -> str:
    for line in content.splitlines():
        if line.lower().startswith("name:"):
            return line
    return "name: (unknown)"

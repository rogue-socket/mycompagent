"""Parse Playwright CLI snapshot output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class ElementRef:
    ref: str
    description: str
    url: str = ""


@dataclass(slots=True)
class SnapshotState:
    url: str
    title: str
    elements: list[ElementRef]
    raw_text: str
    source_path: str | None = None


def parse_snapshot(snapshot_text: str) -> SnapshotState:
    url = _extract_field(snapshot_text, ["URL:", "Page URL:", "url:"])
    title = _extract_field(snapshot_text, ["Title:", "Page title:", "title:"])

    elements: list[ElementRef] = []
    seen: set[str] = set()
    lines = snapshot_text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = re.match(r"^(e\d+)\s*:\s*(.+)$", stripped)
        if match:
            ref = match.group(1)
            if ref not in seen:
                elements.append(
                    ElementRef(
                        ref=ref,
                        description=match.group(2).strip(),
                        url=_extract_ref_url(lines, index),
                    )
                )
                seen.add(ref)
            continue

        # YAML-style refs: e.g. 'combobox "Search" [active] [ref=e37]'
        ref_match = re.search(r"\[ref=(e\d+)\]", stripped)
        if ref_match:
            ref = ref_match.group(1)
            if ref in seen:
                continue
            description = _clean_ref_line(stripped)
            if description:
                elements.append(
                    ElementRef(
                        ref=ref,
                        description=description,
                        url=_extract_ref_url(lines, index),
                    )
                )
                seen.add(ref)

    return SnapshotState(url=url, title=title, elements=elements, raw_text=snapshot_text)


def load_snapshot_text(cli_output: str) -> tuple[str, str | None]:
    """Extract snapshot content from CLI output or snapshot file path."""
    path = _extract_snapshot_path(cli_output)
    if path:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if file_path.exists():
            snapshot_text = file_path.read_text(encoding="utf-8")
            return _merge_cli_metadata(snapshot_text, cli_output), str(file_path)
    return cli_output, None


def compact_elements(elements: Iterable[ElementRef], max_items: int) -> list[ElementRef]:
    items = list(elements)
    if len(items) <= max_items:
        return items
    return items[:max_items]


def _extract_field(text: str, prefixes: list[str]) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in prefixes:
            if stripped.lower().startswith(prefix.lower()):
                return stripped[len(prefix) :].strip()
    return ""


def _extract_snapshot_path(text: str) -> str | None:
    # Matches: [Snapshot](.playwright-cli/page-...yml)
    match = re.search(r"\[Snapshot\]\(([^)]+)\)", text)
    if match:
        return match.group(1)
    # Matches: Snapshot: path
    match = re.search(r"Snapshot\s*:\s*(\S+)", text)
    if match:
        return match.group(1)
    return None


def _merge_cli_metadata(snapshot_text: str, cli_output: str) -> str:
    """Preserve URL/title from the CLI wrapper when loading a snapshot file."""
    metadata: list[str] = []
    if not _extract_field(snapshot_text, ["URL:", "Page URL:", "url:"]):
        url = _extract_field(cli_output, ["URL:", "Page URL:", "- Page URL:", "url:"])
        if url:
            metadata.append(f"Page URL: {url}")
    if not _extract_field(snapshot_text, ["Title:", "Page title:", "Page Title:", "title:"]):
        title = _extract_field(
            cli_output,
            ["Title:", "Page title:", "- Page Title:", "title:"],
        )
        if title:
            metadata.append(f"Page Title: {title}")
    if not metadata:
        return snapshot_text
    return "\n".join([*metadata, snapshot_text])


def _extract_ref_url(lines: list[str], start_index: int) -> str:
    """Extract a child /url line that belongs to a snapshot ref."""
    for line in lines[start_index + 1 : start_index + 8]:
        stripped = line.strip()
        if not stripped:
            continue
        if "[ref=e" in stripped:
            break
        if stripped.startswith("- /url:") or stripped.startswith("/url:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _clean_ref_line(line: str) -> str:
    # Remove list markers and indentation.
    cleaned = line.lstrip("- ").strip()
    # Drop bracketed metadata like [ref=e12] or [cursor=pointer]
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned).strip()
    # Remove trailing colon
    cleaned = cleaned.rstrip(":").strip()
    # Skip structural lines that are not actionable
    if cleaned.startswith(("/url:", "text:")):
        return ""
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned

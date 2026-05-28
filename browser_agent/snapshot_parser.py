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
    metadata: tuple[str, ...] = ()
    child_text: str = ""


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
                description = _clean_ref_line(match.group(2).strip())
                elements.append(
                    ElementRef(
                        ref=ref,
                        description=description,
                        url=_extract_ref_url(lines, index),
                        metadata=_extract_metadata(match.group(2)),
                        child_text=_extract_descendant_text(lines, index),
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
                        metadata=_extract_metadata(stripped),
                        child_text=_extract_descendant_text(lines, index),
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
    """Preserve the latest URL/title from CLI output when loading a snapshot file.

    Playwright CLI may emit stale metadata inside the snapshot file itself after
    tab navigation, so prefer metadata from the wrapper output if present.
    """
    url = _extract_field(cli_output, ["URL:", "Page URL:", "- Page URL:", "url:"])
    title = _extract_field(
        cli_output,
        ["Title:", "Page title:", "- Page Title:", "title:"],
    )
    if not url and not title:
        return snapshot_text

    metadata: list[str] = []
    if url:
        metadata.append(f"Page URL: {url}")
    if title:
        metadata.append(f"Page Title: {title}")

    cleaned_snapshot = _remove_snapshot_metadata_lines(
        snapshot_text,
        preserve_url=not url,
        preserve_title=not title,
    )
    if not metadata:
        return cleaned_snapshot
    return "\n".join([*metadata, cleaned_snapshot])


def _remove_snapshot_metadata_lines(
    snapshot_text: str,
    *,
    preserve_url: bool,
    preserve_title: bool,
) -> str:
    if preserve_url and preserve_title:
        return snapshot_text

    stripped: list[str] = []
    for line in snapshot_text.splitlines():
        lowered = line.strip().lower()
        if not preserve_url and (
            lowered.startswith("page url:") or lowered.startswith("url:")
        ):
            continue
        if not preserve_title and (
            lowered.startswith("page title:")
            or lowered.startswith("title:")
        ):
            continue
        stripped.append(line)
    return "\n".join(stripped)


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


def _extract_metadata(line: str) -> tuple[str, ...]:
    metadata: list[str] = []
    for item in re.findall(r"\[([^\]]+)\]", line):
        item = item.strip()
        if item and not item.startswith("ref="):
            metadata.append(item)
    return tuple(metadata)


def _extract_descendant_text(lines: list[str], start_index: int) -> str:
    """Collect useful text from child snapshot lines under a ref."""
    base_indent = _line_indent(lines[start_index])
    parts: list[str] = []
    seen: set[str] = set()

    for line in lines[start_index + 1 : start_index + 80]:
        stripped = line.strip()
        if not stripped:
            continue

        indent = _line_indent(line)
        if indent <= base_indent and (
            stripped.startswith("- ") or re.match(r"^e\d+\s*:", stripped)
        ):
            break

        text = _text_from_snapshot_line(stripped)
        key = text.lower()
        if text and key not in seen:
            parts.append(text)
            seen.add(key)
        if len(" | ".join(parts)) >= 500:
            break

    return " | ".join(parts)[:500]


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _text_from_snapshot_line(line: str) -> str:
    cleaned = line.lstrip("- ").strip()
    if cleaned.startswith(("/url:", "url:")):
        return ""
    if cleaned.startswith("text:"):
        return _strip_quotes(cleaned.split(":", 1)[1].strip())

    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned).rstrip(":").strip()
    if not cleaned or cleaned.startswith(("/url:", "url:")):
        return ""

    quoted = [item.strip() for item in re.findall(r'"([^"]+)"', cleaned) if item.strip()]
    if quoted:
        return " ".join(quoted)

    if ":" in cleaned:
        _, value = cleaned.split(":", 1)
        value = _strip_quotes(value.strip())
        if value:
            return value

    lowered = cleaned.lower()
    structural_roles = {
        "article",
        "banner",
        "contentinfo",
        "generic",
        "group",
        "img",
        "list",
        "listitem",
        "main",
        "navigation",
        "paragraph",
        "region",
        "section",
    }
    if lowered in structural_roles:
        return ""
    return cleaned


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


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

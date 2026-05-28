"""Task contracts and evidence tracking for comparison-style browsing tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlparse


_PRICE_RE = re.compile(
    r"(?P<currency>₹|INR|Rs\.?|USD|\$)\s*(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)",
    re.I,
)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*(AM|PM)\b", re.I)
_SLOT_RANGE_RE = re.compile(
    r"\b(?P<start_h>\d{1,2})(?::(?P<start_m>[0-5]\d))?\s*"
    r"(?P<start_ampm>am|pm)?\s*(?:-|to|–|—)\s*"
    r"(?P<end_h>\d{1,2})(?::(?P<end_m>[0-5]\d))?\s*"
    r"(?P<end_ampm>am|pm)?\b",
    re.I,
)
_DURATION_RE = re.compile(
    r"\b(?:(?P<hours>\d+(?:\.\d+)?)\s*h(?:ou)?rs?|"
    r"(?P<minutes>\d+)\s*m(?:in)?s?)\b",
    re.I,
)
_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_MONTH_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,\s*20\d{2})?\b",
    re.I,
)
_PRICE_TASK_RE = re.compile(
    r"\b(cheapest|lowest|least expensive|best price|minimum price|lowest price)\b",
    re.I,
)
_MULTI_SITE_TASK_RE = re.compile(
    r"\b(across|between|compare|compare and|different|multiple)\s+(?:the\s+)?(websites?|sites?)\b",
    re.I,
)
_LIMITED_SCOPE_RE = re.compile(
    r"\b(among checked|among the checked|visible|checked option|checked options|limited|could not|unable|not fully visible)\b",
    re.I,
)
_SNIPPET_HINT_RE = re.compile(r"\bsnippets?\b|\bfrom google\b|\bsearch results\b", re.I)
_SEARCH_HOSTS = {
    "google.com",
    "www.google.com",
    "search.google.com",
    "duckduckgo.com",
    "www.bing.com",
    "search.brave.com",
    "search.yahoo.com",
}


@dataclass(slots=True)
class TaskContract:
    """Small, deterministic summary of task-level obligations."""

    is_price_comparison: bool = False
    objective: str = ""
    required_duration_minutes: int | None = None
    time_window_text: str = ""
    start_minutes: int | None = None
    end_minutes: int | None = None
    minimum_candidates: int = 0
    requires_multiple_websites: bool = False
    minimum_distinct_websites: int = 0

    @property
    def active(self) -> bool:
        return (
            self.is_price_comparison
            or self.required_duration_minutes is not None
            or self.requires_multiple_websites
        )


@dataclass(slots=True)
class EvidenceObservation:
    """One observed price-like candidate from a page."""

    step: int
    url: str
    title: str
    currency: str
    amount: Decimal
    context: str
    date_text: str = ""
    start_time_text: str = ""
    duration_minutes: int | None = None

    @property
    def source_key(self) -> str:
        return self.title or self.url or self.context[:60]

    @property
    def candidate_key(self) -> tuple[str, str, Decimal]:
        return (self.source_key, self.context, self.amount)

    def satisfies(self, contract: TaskContract) -> bool:
        if (
            contract.required_duration_minutes is not None
            and self.duration_minutes != contract.required_duration_minutes
        ):
            return False
        return True


@dataclass(slots=True)
class FinishValidation:
    accepted: bool
    message: str = ""


@dataclass(slots=True)
class SlotEvidence:
    start_minutes: int
    end_minutes: int
    currency: str
    amount: Decimal
    context: str


@dataclass
class EvidenceLedger:
    """Accumulated evidence relevant to task completion."""

    observations: list[EvidenceObservation] = field(default_factory=list)
    _seen: set[tuple[str, str, str, Decimal, int | None]] = field(default_factory=set)
    visited_domains: set[str] = field(default_factory=set)

    def add_page(
        self,
        *,
        step: int,
        url: str,
        title: str,
        text: str,
        contract: TaskContract,
    ) -> None:
        if not contract.active:
            return
        domain = _domain_from_url(url)
        if domain:
            self.visited_domains.add(domain)
        lines = _meaningful_lines(text)
        if not lines:
            return
        page_date = _first_match(text, _ISO_DATE_RE) or _first_match(text, _MONTH_DATE_RE)
        page_time = _first_match(text, _TIME_RE)
        page_duration = _duration_minutes(text)
        for index, line in enumerate(lines):
            for match in _PRICE_RE.finditer(line):
                amount = _parse_decimal(match.group("amount"))
                if amount is None:
                    continue
                context = _context_window(lines, index)
                observation = EvidenceObservation(
                    step=step,
                    url=url,
                    title=_clean_title(title),
                    currency=_normalise_currency(match.group("currency")),
                    amount=amount,
                    context=context,
                    date_text=page_date,
                    start_time_text=page_time,
                    duration_minutes=page_duration,
                )
                key = (
                    observation.source_key,
                    observation.currency,
                    observation.context,
                    observation.amount,
                    observation.duration_minutes,
                )
                if key in self._seen:
                    continue
                self._seen.add(key)
                self.observations.append(observation)
        for observation in _slot_coverage_observations(
            lines=lines,
            step=step,
            url=url,
            title=title,
            contract=contract,
        ):
            key = (
                observation.source_key,
                observation.currency,
                observation.context,
                observation.amount,
                observation.duration_minutes,
            )
            if key in self._seen:
                continue
            self._seen.add(key)
            self.observations.append(observation)

    def summary(self, contract: TaskContract, max_items: int = 6) -> str:
        if not contract.active:
            return ""
        lines: list[str] = []
        if contract.is_price_comparison:
            lines.append(
                "Task requires comparing candidate prices before claiming cheapest."
            )
        if contract.requires_multiple_websites:
            lines.append(
                "Task requires inspecting multiple websites and using source pages (not just search snippets)."
            )
        if contract.required_duration_minutes is not None:
            lines.append(
                "Required time window: "
                f"{contract.time_window_text} "
                f"({contract.required_duration_minutes // 60:g} hr"
                f"{'' if contract.required_duration_minutes % 60 == 0 else f' {contract.required_duration_minutes % 60} min'})."
            )
        eligible = self.eligible_observations(contract)
        ineligible = [obs for obs in self.observations if obs not in eligible]
        if eligible:
            lines.append("Best price evidence so far:")
            for obs in sorted(eligible, key=lambda item: item.amount)[:max_items]:
                lines.append(f"- {self._format_observation(obs)}")
        elif self.observations:
            lines.append("Observed prices that do not yet satisfy all hard constraints:")
            for obs in self.observations[-max_items:]:
                lines.append(f"- {self._format_observation(obs)}")
        else:
            lines.append("No price evidence recorded yet.")
        if ineligible and eligible:
            lines.append("Other price evidence with incomplete constraints:")
            for obs in ineligible[-min(2, max_items) :]:
                lines.append(f"- {self._format_observation(obs)}")
        return "\n".join(lines)

    def eligible_observations(self, contract: TaskContract) -> list[EvidenceObservation]:
        return [obs for obs in self.observations if obs.satisfies(contract)]

    def validate_finish(
        self,
        finish_text: str,
        contract: TaskContract,
        *,
        current_url: str | None = None,
    ) -> FinishValidation:
        if not contract.active:
            return FinishValidation(True)
        text = finish_text or ""
        eligible = self.eligible_observations(contract)
        visited_sites = self._visited_distinct_websites()
        if contract.requires_multiple_websites:
            if _is_search_page(current_url):
                return FinishValidation(
                    False,
                    "Finish rejected: this task requires inspecting multiple websites. "
                    "Do not finish from search pages or snippets alone; open and read "
                    "result pages first.",
                )
            if len(visited_sites) < contract.minimum_distinct_websites:
                return FinishValidation(
                    False,
                    "Finish rejected: this task requires at least "
                    f"{contract.minimum_distinct_websites} distinct websites worth of "
                    "evidence before finishing.",
                )
            if _SNIPPET_HINT_RE.search(text):
                return FinishValidation(
                    False,
                    "Finish rejected: completion text appears to rely on snippets. "
                    "Open result pages and use first-hand page content.",
                )
        if contract.required_duration_minutes is not None and not eligible:
            return FinishValidation(
                False,
                "Finish rejected: the task has a hard time-window/duration "
                f"constraint ({contract.time_window_text}), but no collected price "
                "evidence satisfies that duration. Adjust the duration/time controls "
                "or explicitly continue gathering evidence.",
            )
        if contract.is_price_comparison:
            if not eligible:
                return FinishValidation(
                    False,
                    "Finish rejected: no price evidence has been collected for this "
                    "comparison task.",
                )
            best = min(eligible, key=lambda item: item.amount)
            finish_prices = _prices_in_text(text)
            if finish_prices and min(finish_prices) > best.amount:
                return FinishValidation(
                    False,
                    "Finish rejected: the answer cites a higher price than cheaper "
                    f"evidence already collected. Best known evidence is "
                    f"{best.currency} {best.amount} from {best.source_key}: "
                    f"{best.context}.",
                )
            distinct_candidates = {obs.candidate_key for obs in eligible}
            if (
                contract.minimum_candidates
                and len(distinct_candidates) < contract.minimum_candidates
                and not _LIMITED_SCOPE_RE.search(text)
            ):
                return FinishValidation(
                    False,
                    "Finish rejected: cheapest/lowest-price tasks require comparing "
                    f"at least {contract.minimum_candidates} candidates or clearly "
                    "stating the limited scope of checked options.",
                )
        return FinishValidation(True)

    def _visited_distinct_websites(self) -> set[str]:
        return {
            domain
            for domain in self.visited_domains
            if domain and not _is_search_host(domain)
        }

    @staticmethod
    def _format_observation(obs: EvidenceObservation) -> str:
        details = [f"{obs.currency} {obs.amount}", obs.source_key]
        if obs.date_text:
            details.append(obs.date_text)
        if obs.start_time_text:
            details.append(obs.start_time_text)
        if obs.duration_minutes is not None:
            details.append(_format_duration(obs.duration_minutes))
        details.append(obs.context[:180])
        return " | ".join(details)


def build_task_contract(task: str) -> TaskContract:
    contract = TaskContract()
    if _PRICE_TASK_RE.search(task or ""):
        contract.is_price_comparison = True
        contract.objective = "minimize_price"
        contract.minimum_candidates = 2
    window = _time_window(task or "")
    if window:
        start_minutes, end_minutes, text = window
        if end_minutes > start_minutes:
            contract.required_duration_minutes = end_minutes - start_minutes
            contract.time_window_text = text
            contract.start_minutes = start_minutes
            contract.end_minutes = end_minutes
    if _MULTI_SITE_TASK_RE.search(task or ""):
        contract.requires_multiple_websites = True
        contract.minimum_distinct_websites = 2
    return contract


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return ""
    return host.lower().removeprefix("www.")


def _is_search_host(host: str) -> bool:
    return host in _SEARCH_HOSTS


def _is_search_page(url: str | None) -> bool:
    if not url:
        return False
    return _is_search_host(_domain_from_url(url))


def _time_window(text: str) -> tuple[int, int, str] | None:
    pattern = re.compile(
        r"\b(?P<start_h>\d{1,2})(?::(?P<start_m>[0-5]\d))?\s*"
        r"(?P<start_ampm>am|pm)?\s*(?:-|to|–|—)\s*"
        r"(?P<end_h>\d{1,2})(?::(?P<end_m>[0-5]\d))?\s*"
        r"(?P<end_ampm>am|pm)\b",
        re.I,
    )
    match = pattern.search(text)
    if not match:
        return None
    end_ampm = match.group("end_ampm")
    start_ampm = match.group("start_ampm") or end_ampm
    start = _time_to_minutes(
        int(match.group("start_h")),
        int(match.group("start_m") or 0),
        start_ampm,
    )
    end = _time_to_minutes(
        int(match.group("end_h")),
        int(match.group("end_m") or 0),
        end_ampm,
    )
    return start, end, match.group(0)


def _time_to_minutes(hour: int, minute: int, ampm: str) -> int:
    hour = hour % 12
    if ampm.lower() == "pm":
        hour += 12
    return hour * 60 + minute


def _meaningful_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _context_window(lines: list[str], index: int, radius: int = 4) -> str:
    start = max(index - radius, 0)
    end = min(index + radius + 1, len(lines))
    return " | ".join(lines[start:end])


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _normalise_currency(value: str) -> str:
    lowered = value.lower()
    if lowered in {"₹", "inr", "rs", "rs."}:
        return "INR"
    if value == "$" or lowered == "usd":
        return "USD"
    return value.upper()


def _clean_title(title: str) -> str:
    if not title:
        return ""
    return re.split(r"\s+[-|]\s+", title, maxsplit=1)[0].strip()


def _first_match(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return match.group(0)


def _duration_minutes(text: str) -> int | None:
    lines = _meaningful_lines(text)
    for index, line in enumerate(lines):
        if "duration" not in line.lower():
            continue
        for candidate in lines[index : min(index + 4, len(lines))]:
            value = _duration_from_line(candidate)
            if value is not None:
                return value
    return None


def _duration_from_line(line: str) -> int | None:
    total = 0
    found = False
    for match in _DURATION_RE.finditer(line):
        found = True
        if match.group("hours"):
            total += int(Decimal(match.group("hours")) * 60)
        elif match.group("minutes"):
            total += int(match.group("minutes"))
    return total if found else None


def _slot_coverage_observations(
    *,
    lines: list[str],
    step: int,
    url: str,
    title: str,
    contract: TaskContract,
) -> list[EvidenceObservation]:
    if (
        contract.start_minutes is None
        or contract.end_minutes is None
        or contract.required_duration_minutes is None
    ):
        return []
    slots = _slot_evidence(lines, contract)
    if not slots:
        return []
    observations: list[EvidenceObservation] = []
    currencies = {slot.currency for slot in slots}
    for currency in currencies:
        matching_slots = [slot for slot in slots if slot.currency == currency]
        sequence = _covering_slot_sequence(
            matching_slots,
            contract.start_minutes,
            contract.end_minutes,
        )
        if not sequence:
            continue
        total = sum((slot.amount for slot in sequence), Decimal("0"))
        parts = [
            f"{_format_clock(slot.start_minutes)}-{_format_clock(slot.end_minutes)} "
            f"{slot.currency} {slot.amount}"
            for slot in sequence
        ]
        context = (
            f"Consecutive priced slots covering {contract.time_window_text}: "
            + "; ".join(parts)
        )
        observations.append(
            EvidenceObservation(
                step=step,
                url=url,
                title=_clean_title(title),
                currency=currency,
                amount=total,
                context=context,
                duration_minutes=contract.required_duration_minutes,
            )
        )
    return observations


def _slot_evidence(lines: list[str], contract: TaskContract) -> list[SlotEvidence]:
    slots: list[SlotEvidence] = []
    for index, line in enumerate(lines):
        range_match = _SLOT_RANGE_RE.search(line)
        if not range_match:
            continue
        slot_range = _slot_range_minutes(range_match, contract)
        if not slot_range:
            continue
        start, end = slot_range
        duration = end - start
        if duration <= 0 or duration > 180:
            continue
        price_match = _price_near_line(lines, index)
        if not price_match:
            continue
        amount = _parse_decimal(price_match.group("amount"))
        if amount is None:
            continue
        slots.append(
            SlotEvidence(
                start_minutes=start,
                end_minutes=end,
                currency=_normalise_currency(price_match.group("currency")),
                amount=amount,
                context=_context_window(lines, index, radius=2),
            )
        )
    return slots


def _slot_range_minutes(
    match: re.Match[str],
    contract: TaskContract,
) -> tuple[int, int] | None:
    end_ampm = match.group("end_ampm")
    start_ampm = match.group("start_ampm") or end_ampm
    start = _clock_to_minutes(
        int(match.group("start_h")),
        int(match.group("start_m") or 0),
        start_ampm,
        anchor=contract.start_minutes,
    )
    end = _clock_to_minutes(
        int(match.group("end_h")),
        int(match.group("end_m") or 0),
        end_ampm,
        anchor=contract.end_minutes,
    )
    if start is None or end is None:
        return None
    if end <= start and end + 12 * 60 <= 24 * 60:
        end += 12 * 60
    return start, end


def _clock_to_minutes(
    hour: int,
    minute: int,
    ampm: str | None,
    *,
    anchor: int | None,
) -> int | None:
    if hour > 23 or minute > 59:
        return None
    if ampm:
        return _time_to_minutes(hour, minute, ampm)
    candidates = [hour * 60 + minute]
    if 1 <= hour <= 11:
        candidates.append((hour + 12) * 60 + minute)
    if anchor is None:
        return candidates[0]
    return min(candidates, key=lambda value: abs(value - anchor))


def _price_near_line(lines: list[str], index: int) -> re.Match[str] | None:
    for candidate in lines[index : min(index + 4, len(lines))]:
        match = _PRICE_RE.search(candidate)
        if match:
            return match
    return None


def _covering_slot_sequence(
    slots: list[SlotEvidence],
    start_minutes: int,
    end_minutes: int,
) -> list[SlotEvidence]:
    sequence: list[SlotEvidence] = []
    cursor = start_minutes
    while cursor < end_minutes:
        candidates = [
            slot
            for slot in slots
            if slot.start_minutes == cursor and slot.end_minutes <= end_minutes
        ]
        if not candidates:
            return []
        slot = min(candidates, key=lambda item: item.amount)
        sequence.append(slot)
        cursor = slot.end_minutes
    return sequence


def _prices_in_text(text: str) -> list[Decimal]:
    prices: list[Decimal] = []
    for match in _PRICE_RE.finditer(text or ""):
        amount = _parse_decimal(match.group("amount"))
        if amount is not None:
            prices.append(amount)
    return prices


def _format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} hr {remainder} min"
    if hours:
        return f"{hours} hr"
    return f"{remainder} min"


def _format_clock(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    return f"{hours:02d}:{remainder:02d}"

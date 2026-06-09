"""Deterministic helpers for Neal Agarwal's Password Game."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re


PERIODIC_TABLE: tuple[tuple[str, int], ...] = (
    ("H", 1),
    ("He", 2),
    ("Li", 3),
    ("Be", 4),
    ("B", 5),
    ("C", 6),
    ("N", 7),
    ("O", 8),
    ("F", 9),
    ("Ne", 10),
    ("Na", 11),
    ("Mg", 12),
    ("Al", 13),
    ("Si", 14),
    ("P", 15),
    ("S", 16),
    ("Cl", 17),
    ("Ar", 18),
    ("K", 19),
    ("Ca", 20),
    ("Sc", 21),
    ("Ti", 22),
    ("V", 23),
    ("Cr", 24),
    ("Mn", 25),
    ("Fe", 26),
    ("Co", 27),
    ("Ni", 28),
    ("Cu", 29),
    ("Zn", 30),
    ("Ga", 31),
    ("Ge", 32),
    ("As", 33),
    ("Se", 34),
    ("Br", 35),
    ("Kr", 36),
    ("Rb", 37),
    ("Sr", 38),
    ("Y", 39),
    ("Zr", 40),
    ("Nb", 41),
    ("Mo", 42),
    ("Tc", 43),
    ("Ru", 44),
    ("Rh", 45),
    ("Pd", 46),
    ("Ag", 47),
    ("Cd", 48),
    ("In", 49),
    ("Sn", 50),
    ("Sb", 51),
    ("Te", 52),
    ("I", 53),
    ("Xe", 54),
    ("Cs", 55),
    ("Ba", 56),
    ("La", 57),
    ("Ce", 58),
    ("Pr", 59),
    ("Nd", 60),
    ("Pm", 61),
    ("Sm", 62),
    ("Eu", 63),
    ("Gd", 64),
    ("Tb", 65),
    ("Dy", 66),
    ("Ho", 67),
    ("Er", 68),
    ("Tm", 69),
    ("Yb", 70),
    ("Lu", 71),
    ("Hf", 72),
    ("Ta", 73),
    ("W", 74),
    ("Re", 75),
    ("Os", 76),
    ("Ir", 77),
    ("Pt", 78),
    ("Au", 79),
    ("Hg", 80),
    ("Tl", 81),
    ("Pb", 82),
    ("Bi", 83),
    ("Po", 84),
    ("At", 85),
    ("Rn", 86),
    ("Fr", 87),
    ("Ra", 88),
    ("Ac", 89),
    ("Th", 90),
    ("Pa", 91),
    ("U", 92),
    ("Np", 93),
    ("Pu", 94),
    ("Am", 95),
    ("Cm", 96),
    ("Bk", 97),
    ("Cf", 98),
    ("Es", 99),
    ("Fm", 100),
    ("Md", 101),
    ("No", 102),
    ("Lr", 103),
    ("Rf", 104),
    ("Db", 105),
    ("Sg", 106),
    ("Bh", 107),
    ("Hs", 108),
    ("Mt", 109),
    ("Ds", 110),
    ("Rg", 111),
    ("Cn", 112),
    ("Nh", 113),
    ("Fl", 114),
    ("Mc", 115),
    ("Lv", 116),
    ("Ts", 117),
    ("Og", 118),
)

ROMAN_NUMERAL_CHARS = frozenset("IVXLCDM")
EGG = "🥚"


@dataclass(frozen=True)
class ElementScore:
    total: int
    symbols: tuple[str, ...]
    remainder: str


def score_password_elements(password: str) -> ElementScore:
    """Score elements exactly like Password Game Rule 18.

    The game removes all two-letter element symbols in periodic-table order,
    then removes all one-letter symbols in periodic-table order.
    """
    total = 0
    symbols: list[str] = []
    remainder = password
    for symbol, atomic_number in _ordered_symbols():
        count = remainder.count(symbol)
        if count:
            total += count * atomic_number
            symbols.extend([symbol] * count)
            remainder = remainder.replace(symbol, "")
    return ElementScore(total=total, symbols=tuple(symbols), remainder=remainder)


def solve_password_game_elements(password: str, target: int = 200) -> dict[str, object]:
    """Return Rule 18 diagnostics and an edit suggestion."""
    current = score_password_elements(password)
    result: dict[str, object] = {
        "target": target,
        "current_sum": current.total,
        "counted_symbols": list(current.symbols),
        "is_valid": current.total == target,
    }
    if current.total == target:
        result["message"] = "Current password already satisfies Rule 18."
        return result

    append_suffix = _find_suffix(password, target)
    if append_suffix is not None:
        result.update(
            {
                "suggested_suffix": append_suffix,
                "suggested_password": password + append_suffix,
                "suggested_sum": target,
                "message": "Append suggested_suffix to satisfy Rule 18.",
            }
        )
        return result

    egg_prefix = _prefix_through_last_egg(password)
    if egg_prefix and egg_prefix != password:
        prefix_suffix = _find_suffix(egg_prefix, target)
        if prefix_suffix is not None:
            result.update(
                {
                    "clean_prefix": egg_prefix,
                    "suggested_suffix": prefix_suffix,
                    "suggested_password": egg_prefix + prefix_suffix,
                    "suggested_sum": target,
                    "message": (
                        "Current password cannot be fixed by appending safely. "
                        "Replace content after Paul's egg with suggested_suffix."
                    ),
                }
            )
            return result

    case_repair = _find_case_repair(password, target)
    if case_repair is not None:
        repaired_password, suffix, case_changes = case_repair
        result.update(
            {
                "suggested_suffix": suffix,
                "suggested_password": repaired_password,
                "suggested_sum": target,
                "case_changes": case_changes,
                "message": (
                    "Fill suggested_password exactly to satisfy Rule 18. "
                    "It lowercases accidental element symbols and preserves protected tokens."
                ),
            }
        )
        return result

    result["message"] = (
        "No short roman-safe suffix found. Remove recent element additions or "
        "provide a cleaner password prefix."
    )
    return result


def _ordered_symbols() -> tuple[tuple[str, int], ...]:
    two_letter = tuple(item for item in PERIODIC_TABLE if len(item[0]) == 2)
    one_letter = tuple(item for item in PERIODIC_TABLE if len(item[0]) == 1)
    return two_letter + one_letter


def _find_suffix(password: str, target: int) -> str | None:
    if score_password_elements(password).total == target:
        return ""
    allowed_symbols = tuple(
        symbol
        for symbol, _ in PERIODIC_TABLE
        if not any(char in ROMAN_NUMERAL_CHARS for char in symbol)
    )
    frontier = [""]
    for _ in range(4):
        next_frontier: list[str] = []
        for prefix in frontier:
            for symbol in allowed_symbols:
                suffix = prefix + symbol
                total = score_password_elements(password + suffix).total
                if total == target:
                    return suffix
                if total < target:
                    next_frontier.append(suffix)
        frontier = next_frontier
    return None


def _find_case_repair(
    password: str, target: int
) -> tuple[str, str, list[dict[str, object]]] | None:
    protected = _protected_case_indexes(password)
    mutable_indexes = [
        index
        for index, char in enumerate(password)
        if char.isalpha() and char.isupper() and index not in protected
    ]
    for change_count in range(1, min(len(mutable_indexes), 14) + 1):
        for indexes in combinations(mutable_indexes, change_count):
            candidate_chars = list(password)
            for index in indexes:
                candidate_chars[index] = candidate_chars[index].lower()
            candidate = "".join(candidate_chars)
            suffix = _find_suffix(candidate, target)
            if suffix is None:
                continue
            repaired_password = candidate + suffix
            return repaired_password, suffix, _case_changes(password, candidate, indexes)
    return None


def _protected_case_indexes(password: str) -> set[int]:
    protected: set[int] = set()
    for match in re.finditer(r"[IVXLCDM]+(?:x[IVXLCDM]+)*", password):
        protected.update(range(match.start(), match.end()))
    for match in re.finditer(r"(?<![A-Za-z])[KQRBN][a-h][1-8][+#]?(?![A-Za-z])", password):
        protected.update(range(match.start(), match.end()))
    for sponsor in ("Pepsi", "Shell", "Starbucks"):
        start = 0
        while True:
            index = password.find(sponsor, start)
            if index == -1:
                break
            protected.update(range(index, index + len(sponsor)))
            start = index + len(sponsor)
    return protected


def _case_changes(
    original: str, candidate: str, indexes: tuple[int, ...]
) -> list[dict[str, object]]:
    return [
        {"index": index, "from": original[index], "to": candidate[index]}
        for index in indexes
    ]


def _prefix_through_last_egg(password: str) -> str | None:
    index = password.rfind(EGG)
    if index == -1:
        return None
    return password[: index + len(EGG)]

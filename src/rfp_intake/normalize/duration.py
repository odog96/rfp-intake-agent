"""Regex-based duration and frequency parser for clinical study timelines."""

from __future__ import annotations

import re
from typing import Any

# Word-to-number mapping for common written numbers
_WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22,
    "twenty-four": 24, "thirty": 30, "thirty-six": 36,
    "forty": 40, "forty-eight": 48, "fifty": 50, "fifty-two": 52,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

# Unit normalization
_UNIT_MAP: dict[str, str] = {
    "d": "days", "day": "days", "days": "days",
    "w": "weeks", "wk": "weeks", "wks": "weeks", "week": "weeks", "weeks": "weeks",
    "m": "months", "mo": "months", "mos": "months", "month": "months", "months": "months",
    "y": "years", "yr": "years", "yrs": "years", "year": "years", "years": "years",
    "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
}

# Q-notation pattern: Q2W, Q3W, Q4W, Q12W, etc.
_Q_PATTERN = re.compile(r"^[Qq](\d+)([DdWwMm])$")

# "every N units" pattern
_EVERY_PATTERN = re.compile(
    r"every\s+(\d+|" + "|".join(_WORD_NUMBERS.keys()) + r")\s+"
    r"(days?|weeks?|months?|years?|hours?|wks?|mos?|yrs?|hrs?)",
    re.IGNORECASE,
)

# "N units" pattern (e.g., "40 months", "21 days")
_N_UNITS_PATTERN = re.compile(
    r"^~?\s*(\d+(?:\.\d+)?)\s+"
    r"(days?|weeks?|months?|years?|hours?|wks?|mos?|yrs?|hrs?)$",
    re.IGNORECASE,
)

# Range pattern: "6-8 weeks", "6 to 8 weeks"
_RANGE_PATTERN = re.compile(
    r"^~?\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s+"
    r"(days?|weeks?|months?|years?|hours?|wks?|mos?|yrs?|hrs?)$",
    re.IGNORECASE,
)

# "once/twice weekly/daily/monthly" patterns
_FREQ_WORD_PATTERN = re.compile(
    r"^(once|twice|three times?|four times?)\s+"
    r"(daily|weekly|monthly|yearly|per\s+day|per\s+week|per\s+month|a\s+day|a\s+week|a\s+month)$",
    re.IGNORECASE,
)

_FREQ_WORD_MAP: dict[str, int] = {
    "once": 1, "twice": 2, "three times": 3, "three time": 3,
    "four times": 4, "four time": 4,
}

_FREQ_UNIT_MAP: dict[str, str] = {
    "daily": "days", "per day": "days", "a day": "days",
    "weekly": "weeks", "per week": "weeks", "a week": "weeks",
    "monthly": "months", "per month": "months", "a month": "months",
    "yearly": "years",
}


def _parse_number(s: str) -> int | float | None:
    """Parse a number from string, including word forms."""
    s = s.strip().lower()
    if s in _WORD_NUMBERS:
        return _WORD_NUMBERS[s]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return None


def _normalize_unit(raw_unit: str) -> str:
    """Normalize a unit string to canonical form."""
    return _UNIT_MAP.get(raw_unit.lower().strip(), raw_unit.lower().strip())


def normalize_duration(raw: str) -> dict[str, Any] | None:
    """Parse a duration/frequency string into structured form.

    Returns dict with keys: n, unit (and optionally n_min, n_max for ranges).
    Returns None if the string cannot be parsed.
    """
    raw = raw.strip()
    if not raw:
        return None

    # Q-notation: Q3W → 21 days
    m = _Q_PATTERN.match(raw)
    if m:
        n = int(m.group(1))
        unit_char = m.group(2).upper()
        if unit_char == "W":
            return {"n": n * 7, "unit": "days"}
        elif unit_char == "D":
            return {"n": n, "unit": "days"}
        elif unit_char == "M":
            return {"n": n, "unit": "months"}

    # Range: "6-8 weeks"
    m = _RANGE_PATTERN.match(raw)
    if m:
        n_min = _parse_number(m.group(1))
        n_max = _parse_number(m.group(2))
        unit = _normalize_unit(m.group(3))
        if n_min is not None and n_max is not None:
            return {"n_min": n_min, "n_max": n_max, "unit": unit}

    # "every N units"
    m = _EVERY_PATTERN.search(raw)
    if m:
        n_val = _parse_number(m.group(1))
        unit = _normalize_unit(m.group(2))
        if n_val is not None:
            if unit == "weeks":
                return {"n": int(n_val) * 7, "unit": "days"}
            return {"n": n_val, "unit": unit}

    # "N units" (simple)
    m = _N_UNITS_PATTERN.match(raw)
    if m:
        n_simple = _parse_number(m.group(1))
        unit_simple = _normalize_unit(m.group(2))
        if n_simple is not None:
            return {"n": n_simple, "unit": unit_simple}

    # "once/twice weekly" etc.
    m = _FREQ_WORD_PATTERN.match(raw)
    if m:
        freq_word = m.group(1).lower()
        unit_word = m.group(2).lower()
        count = _FREQ_WORD_MAP.get(freq_word)
        freq_unit = _FREQ_UNIT_MAP.get(unit_word)
        if count is not None and freq_unit is not None:
            return {"n": count, "unit": freq_unit, "per": True}

    return None

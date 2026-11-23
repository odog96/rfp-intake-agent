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

# Any amount, anywhere in a wording: a number and a unit, "28 days" of "every 28
# days for 24 months", or hyphenated, "24-month" of "42 months (24-month accrual
# period + 18-month treatment/follow-up period)". Counted, never read: see
# amount_count.
_ANY_AMOUNT_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|" + "|".join(_WORD_NUMBERS.keys()) + r")(?:\s+|-)"
    r"(days?|weeks?|months?|years?|hours?|wks?|mos?|yrs?|hrs?)\b",
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

    # "every N units", only where it is the wording's one amount (Prompt B item
    # 7): "every 2 weeks for 12 weeks, then every 4 weeks" is not 14 days, so it
    # is left as written.
    m = _EVERY_PATTERN.search(raw) if amount_count(raw) == 1 else None
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


def amount_count(raw: str) -> int:
    """How many amounts (a number and a unit of time) a wording holds: 1 for
    "42 months (total study duration)", 2 for "42 months to 48 months" and for
    "3 years 6 months". A wording is only ever shortened to an amount when it
    holds one (Prompt B item 7). Nothing is added up."""
    return len(_ANY_AMOUNT_PATTERN.findall(raw))


# An amount as amounts_in reads it: a range's lower end, when it is one ("1" of
# "1-2 years", "28" of "28 to 35 days"), then the number and the unit. A number
# after "Day " or a minus sign names a day, not the start of a range ("Day 1 to
# 28 days after the last dose" holds the one amount 28 days).
# The number words it reads: one to nineteen, and the tens alone or with a unit
# ("twenty", "twenty-eight", "thirty one"), the tens first so "seventy" is not
# read as "seven". "hundred" is not read, so "two hundred days" gives no amount
# rather than 100 days; a wording with no amount is compared by its words
# (pipeline-rules-reviewer, third reading, 23 November 2026).
_SMALL_WORDS: dict[str, int] = {
    w: n for w, n in _WORD_NUMBERS.items() if n < 20 and "-" not in w
}
_TENS_WORDS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = (
    r"(?:" + "|".join(_TENS_WORDS) + r")"
    r"(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine)\b)?"
    r"|" + "|".join(sorted(_SMALL_WORDS, key=len, reverse=True))
)
# A number in digits, with thousands separators read as one number: "1,000
# days" is 1000 days, not 0.
_DIGITS = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# A range's sign: a hyphen or dash only between digits, as a number word's own
# hyphen ("twenty-four months") is not one; "to", "or" and "and" between any
# two numbers, so "1 or 2 years" and "between 1 and 2 years" are ranges like
# "1-2 years" (pipeline-rules-reviewer, second reading, 23 November 2026).
# Nor is a number read after "hundred", "thousand" or a digit and a space, where
# it is the end of a longer number: "one hundred and twenty days" would read as
# 20 days and "1 000 days" as 0 days (pipeline-rules-reviewer, 23 November 2026).
_AMOUNT_OR_RANGE_PATTERN = re.compile(
    r"(?<![\w.,\-])(?<![Dd]ay )"
    r"(?<!hundred )(?<!hundred and )(?<!thousand )(?<!thousand and )(?<!\d )"
    r"(?:between\s+)?"
    r"(?:(" + _DIGITS + r")\s*(?:-|\u2013|to\b|or\b|and\b)\s*"
    r"|(" + _NUMBER_WORDS + r")\s+(?:to|or|and)\s+)?"
    r"(" + _DIGITS + r"|" + _NUMBER_WORDS + r")(?:\s+|-)"
    r"(days?|weeks?|months?|years?|hours?|wks?|mos?|yrs?|hrs?)\b",
    re.IGNORECASE,
)


def _amount_number(s: str) -> int | float | None:
    """A number amounts_in read, in digits ("1,000", "3.5") or in words
    ("twenty-eight", "thirty one")."""
    s = s.strip().lower()
    if s[:1].isdigit():
        return _parse_number(s.replace(",", ""))
    total = 0
    for part in re.split(r"[-\s]+", s):
        n = _TENS_WORDS.get(part, _SMALL_WORDS.get(part))
        if n is None:
            return None
        total += n
    return total


def amounts_in(raw: str) -> list[tuple[int | float, int | float, str]]:
    """Every amount (a number and a unit of time) a wording holds, in order, as
    its lower end, its upper end and its unit in the plural: (28, 28, "days")
    for "28 days" and for "a 28-day period", (3.5, 4, "years") for "3.5-4
    years". A range is read by both its ends, so "1-2 years" and "2 years" are
    two amounts (pipeline-rules-reviewer, 23 November 2026). Nothing is
    converted or added up: 4 weeks and 28 days are two different amounts."""
    out: list[tuple[int | float, int | float, str]] = []
    for low_digits, low_word, number, unit in _AMOUNT_OR_RANGE_PATTERN.findall(raw):
        n = _amount_number(number)
        if n is None:
            continue
        low = low_digits or low_word
        lo = _amount_number(low) if low else n
        out.append((n if lo is None else lo, n, _normalize_unit(unit)))
    return out

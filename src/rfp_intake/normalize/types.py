"""Type-specific normalizers — pure functions, zero I/O, zero LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from rfp_intake.domain.registry import EntryKey
from rfp_intake.normalize.enum_maps import (
    BLINDING_MAP,
    DRUG_FORM_MAP,
    PHASE_MAP,
    YES_NO_MAP,
)

# Word-to-number for integer parsing
_WORD_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}

# Regex to find parenthesized numbers: "forty (40)" → extract 40
_PAREN_NUMBER = re.compile(r"\((\d+)\)")
# Regex for leading approximate markers
_APPROX_PREFIX = re.compile(
    r"^[~≈≥≤><]?\s*(?:approximately|approx\.?|about|around|up\s+to|at\s+least)?\s*",
    re.IGNORECASE,
)
# Regex for trailing non-numeric content
_TRAILING_JUNK = re.compile(
    r"\s*(sites?|subjects?|patients?|centres?|centers?|pages?|visits?|months?|weeks?|days?|years?).*$",
    re.IGNORECASE,
)
# A parenthetical with no parenthetical inside it, removed innermost first.
_PARENTHETICAL = re.compile(r"\([^()]*\)")
# A letter with a digit somewhere after it: words, then a second figure.
_WORDS_THEN_DIGITS = re.compile(r"[^\W\d_].*\d", re.DOTALL)
# A whole number at the start, with or without thousands commas, and the rest.
_LEADING_WHOLE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(.*)", re.DOTALL)
# What may follow it: words, spaces and the punctuation of a sentence. No
# digit, and no sign such as % or + that would make it another quantity.
_WORDS_ONLY = re.compile(r"(?:[^\W\d_]+|[\s.,;:'\"’/&–—-]+)+")


def normalize_int(raw: str) -> int | None:
    """Parse an integer from clinical text.

    Handles: "40", "forty (40)", "~120", "approximately 75", "forty", and a
    leading whole number followed only by words ("20,800 CRF pages total") or
    by a parenthetical, which may hold digits ("260 (130 per arm)").

    Words followed by a second figure outside any parenthetical give None,
    because the text states two numbers and this function cannot know which is
    the count: "10 visits per site for a total of 750 visits" is not 10.
    NORMALIZE then keeps the document's words as the value (CLAUDE.md open
    item 10, 2026-10-19; changed 2026-11-23).
    """
    raw = raw.strip()
    if not raw:
        return None

    # Check for parenthesized number first: "forty (40)" → 40
    m = _PAREN_NUMBER.search(raw)
    if m:
        return int(m.group(1))

    # Strip approximate prefixes
    cleaned = _APPROX_PREFIX.sub("", raw)

    outside = _outside_parentheticals(cleaned)
    if _WORDS_THEN_DIGITS.search(outside):
        return None

    # A whole number, then only words or parentheticals: "20,800 CRF pages
    # total", "260 (130 per arm)". The number must end at a space or a bracket,
    # so "200-260", "1.5" and "10/site" are not read this way.
    lead = _LEADING_WHOLE.fullmatch(cleaned)
    if lead and _only_words(lead.group(2)):
        return int(lead.group(1).replace(",", ""))

    # Strip trailing unit words
    cleaned = _TRAILING_JUNK.sub("", cleaned).strip()

    # Try direct integer parse
    try:
        return int(cleaned.replace(",", "").replace(" ", ""))
    except ValueError:
        pass

    # Try word-number lookup
    lower = cleaned.lower().strip()
    if lower in _WORD_NUMBERS:
        return _WORD_NUMBERS[lower]

    # Try float → int for "120.0" style
    try:
        val = float(cleaned.replace(",", ""))
        if val == int(val):
            return int(val)
    except ValueError:
        pass

    return None


def _outside_parentheticals(text: str) -> str:
    """The text with every balanced parenthetical removed, nested ones too."""
    while True:
        shorter = _PARENTHETICAL.sub(" ", text)
        if shorter == text:
            return text
        text = shorter


def _only_words(tail: str) -> bool:
    """Whether what follows a leading number is words, parentheticals, or both.

    It must start with a space or an opening bracket, hold something, and,
    outside its parentheticals, hold no digit and nothing but words, spaces and
    sentence punctuation.
    """
    if not tail.strip() or not (tail[0].isspace() or tail[0] == "("):
        return False
    outside = _outside_parentheticals(tail)
    return _WORDS_ONLY.fullmatch(outside) is not None if outside.strip() else True


def normalize_enum(raw: str, allowed_values: list[str]) -> str | None:
    """Normalize a raw enum value to its canonical form.

    Uses domain-specific maps (phase, blinding, drug form, yes/no) and
    falls back to fuzzy matching against allowed_values.
    """
    raw_lower = raw.strip().lower()
    if not raw_lower:
        return None

    # Direct match
    if raw_lower in allowed_values:
        return raw_lower
    # Underscore variant
    raw_under = raw_lower.replace(" ", "_").replace("-", "_")
    if raw_under in allowed_values:
        return raw_under

    # Domain-specific maps
    for map_dict in (PHASE_MAP, BLINDING_MAP, DRUG_FORM_MAP, YES_NO_MAP):
        if raw_lower in map_dict:
            candidate = map_dict[raw_lower]
            if candidate in allowed_values:
                return candidate

    # Fuzzy: strip punctuation and try prefix match
    raw_stripped = re.sub(r"[^a-z0-9]", "", raw_lower)
    for val in allowed_values:
        val_stripped = re.sub(r"[^a-z0-9]", "", val)
        if raw_stripped == val_stripped:
            return val

    return None


def normalize_bool(raw: str) -> bool | None:
    """Normalize a boolean value from text."""
    raw_lower = raw.strip().lower()
    if raw_lower in ("yes", "true", "y", "1"):
        return True
    if raw_lower in ("no", "false", "n", "0"):
        return False
    return None


def normalize_text(raw: str) -> str:
    """Normalize free text: collapse whitespace, strip."""
    text = re.sub(r"\s+", " ", raw).strip()
    return text


# A length written as one hyphenated word, the whole of a value: "28-day",
# "14-21-day", "24-month". The patient timeline's hint asks for "28 days", and
# the model still wrote "28-day" for "a 28-day Screening period" (Amendment
# p.47, run r-20261123-oct29-run3), so the same length read two ways.
_HYPHENATED_AMOUNT = re.compile(
    r"^(\d+(?:\.\d+)?(?:\s*(?:-|–|to)\s*\d+(?:\.\d+)?)?)-(hour|day|week|month|year)s?$",
    re.IGNORECASE,
)


def amount_words(text: str) -> str:
    """A length written as one hyphenated word, as a number and a unit: "28-day"
    gives "28 days", "1-week" gives "1 week". Any other text is returned as it is."""
    m = _HYPHENATED_AMOUNT.match(text)
    if m is None:
        return text
    n, unit = m.group(1), m.group(2).lower()
    return f"{n} {unit}" if n == "1" else f"{n} {unit}s"


def normalize_entry(raw: str, keys: list[EntryKey]) -> dict[str, Any] | None:
    """One `object[]` entry, the JSON object EXTRACT validated, with its keys in
    `schema` order: an int key as an int, an enum key as its allowed value, text
    with its whitespace collapsed and a length written as one hyphenated word
    as a number and a unit (`amount_words`). A key stated empty, or one that does not
    normalize, is left out, so a missing key and an unstated one read the same.
    None when `raw` is not a JSON object. Added 2026-10-12, fix 1 of that day's
    brief.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    entry: dict[str, Any] = {}
    for key in keys:
        value = data.get(key.name)
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value)
        normalized: Any
        if key.type == "int":
            normalized = value if isinstance(value, int) and not isinstance(value, bool) \
                else normalize_int(text)
        elif key.type == "enum":
            normalized = normalize_enum(text, key.values)
        else:
            normalized = amount_words(normalize_text(text)) or None
        if normalized is not None:
            entry[key.name] = normalized
    return entry


def normalize_list_enum(raw: str, allowed_values: list[str]) -> list[str] | None:
    """Normalize a list of enum values from comma-separated or JSON-like text."""
    raw = raw.strip()
    if not raw:
        return None

    # Try splitting by comma, semicolon, or newline
    parts = re.split(r"[,;\n]+", raw)
    results: list[str] = []
    for part in parts:
        part = part.strip().strip("[]\"' ")
        if not part:
            continue
        normalized = normalize_enum(part, allowed_values)
        if normalized is not None:
            results.append(normalized)

    return results if results else None


def normalize_value(raw: str, field_type: str, allowed_values: list[str] | None = None) -> Any:
    """Dispatch to the appropriate normalizer based on field type.

    Returns the normalized value, or None if normalization fails.
    Also returns unit information for duration-like text fields.
    """
    if field_type == "int":
        return normalize_int(raw)
    elif field_type == "number":
        return _normalize_number(raw)
    elif field_type == "bool":
        return normalize_bool(raw)
    elif field_type == "enum":
        if allowed_values:
            return normalize_enum(raw, allowed_values)
        return None
    elif field_type == "list[enum]":
        if allowed_values:
            return normalize_list_enum(raw, allowed_values)
        return None
    elif field_type == "text" or field_type.startswith("list[") or field_type == "object[]":
        return normalize_text(raw)
    return normalize_text(raw)


def _normalize_number(raw: str) -> int | float | None:
    """Parse a numeric value (int or float)."""
    cleaned = _APPROX_PREFIX.sub("", raw.strip())
    cleaned = _TRAILING_JUNK.sub("", cleaned).strip()
    cleaned = cleaned.replace(",", "")
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None

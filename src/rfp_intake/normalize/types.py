"""Type-specific normalizers — pure functions, zero I/O, zero LLM."""

from __future__ import annotations

import re
from typing import Any

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


def normalize_int(raw: str) -> int | None:
    """Parse an integer from clinical text.

    Handles: "40", "forty (40)", "~120", "approximately 75", "forty".
    """
    raw = raw.strip()
    if not raw:
        return None

    # Check for parenthesized number first: "forty (40)" → 40
    m = _PAREN_NUMBER.search(raw)
    if m:
        return int(m.group(1))

    # Strip approximate prefixes and trailing unit words
    cleaned = _APPROX_PREFIX.sub("", raw)
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

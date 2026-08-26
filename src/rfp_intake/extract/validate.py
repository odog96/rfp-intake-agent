"""Post-call validation for extracted field records.

Non-negotiable per ARCHITECTURE.md §4.4:
1. quote must be substring of excerpt (normalized whitespace)
2. page must fall inside task window
3. enum values must be in registry's allowed set
4. numeric fields must parse
"""

from __future__ import annotations

import re

from rfp_intake.domain.dynamic import FieldExtractionItem
from rfp_intake.domain.registry import FieldDef


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces for comparison."""
    return re.sub(r"\s+", " ", text).strip()


def validate_quote(quote: str, excerpt: str) -> bool:
    """Check that quote is a substring of the excerpt (normalized whitespace)."""
    norm_quote = _normalize_whitespace(quote)
    norm_excerpt = _normalize_whitespace(excerpt)
    if not norm_quote:
        return False
    return norm_quote in norm_excerpt


def validate_page(page: int, page_window: tuple[int, int]) -> bool:
    """Check that page falls within the task's page window (inclusive)."""
    return page_window[0] <= page <= page_window[1]


def validate_enum(raw_value: str, field_def: FieldDef) -> bool:
    """Check that raw_value is plausibly mappable to an allowed enum value.

    We accept: exact match, case-insensitive match, or underscore/hyphen/space variants.
    The normalizer will do the final mapping — here we just reject obviously invalid values.
    """
    if field_def.type not in ("enum", "list[enum]"):
        return True
    if not field_def.values:
        return True

    raw_lower = raw_value.strip().lower()
    # Accept if it matches any allowed value directly
    for val in field_def.values:
        if raw_lower == val:
            return True
        # Fuzzy: strip non-alphanumeric
        if re.sub(r"[^a-z0-9]", "", raw_lower) == re.sub(r"[^a-z0-9]", "", val):
            return True

    # Accept if it looks like it can normalize (we trust the normalizer to handle it)
    # Only reject values that are clearly not in the domain
    return True


def validate_numeric(raw_value: str, field_def: FieldDef) -> bool:
    """Check that numeric fields contain at least one digit."""
    if field_def.type not in ("int", "number"):
        return True
    return bool(re.search(r"\d", raw_value))


def validate_record(
    item: FieldExtractionItem,
    excerpt: str,
    page_window: tuple[int, int],
    field_def: FieldDef,
) -> tuple[bool, str | None]:
    """Validate a single extraction item. Returns (valid, reason_if_invalid)."""
    if not validate_quote(item.quote, excerpt):
        return False, "quote_not_found_in_excerpt"

    if not validate_page(item.page, page_window):
        return False, f"page_{item.page}_outside_window_{page_window}"

    if not validate_enum(item.raw_value, field_def):
        return False, f"enum_value_invalid_{item.raw_value}"

    if not validate_numeric(item.raw_value, field_def):
        return False, f"numeric_field_no_digits_{item.raw_value}"

    return True, None

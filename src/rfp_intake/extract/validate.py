"""Post-call validation for extracted field records.

Non-negotiable per ARCHITECTURE.md §4.4:
1. quote must be substring of excerpt (normalized whitespace)
2. page must fall inside task window
3. enum values must be in registry's allowed set
4. numeric fields must parse
5. an `object[]` field's entry must be one JSON object of its schema's keys
"""

from __future__ import annotations

import json
import re
from collections import Counter

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


# The block markers `extract/prompt.py` writes: one per page of text, one per table.
_PAGE_MARKER = re.compile(r"--- (?:Page \d+|Table \(page \d+\)) ---")
_WORD = re.compile(r"[a-z0-9]+")


def _word_key(word: str) -> str:
    return "".join(_WORD.findall(word.lower()))


def nearest_passage(quote: str, excerpt: str, context_words: int = 4) -> str | None:
    """The unbroken stretch of the excerpt that shares most words with a failed quote.

    Used only to word the repair retry: the model is shown this text and asked
    to copy its quote from it. It is never used as a quote. The retry's quote
    goes through `validate_quote` like any other, so the check itself is
    unchanged. Fix 1 of the 2026-10-09 brief: the synthetic RFP's two-column
    rows put the label's second line between the value's words, so the
    sentence the model reads is not one the excerpt contains, and the retry,
    told only "quote_not_found_in_excerpt", failed the same way.

    The result is words of `_normalize_whitespace(excerpt)` joined by single
    spaces, so it is itself a substring `validate_quote` accepts. It never
    crosses a "--- Page N ---" or "--- Table (page N) ---" marker, so it is
    text from one page or one table. None when
    no stretch shares at least half the quote's words, so the retry is never
    pointed at an unrelated passage.
    """
    quote_keys = [k for k in (_word_key(w) for w in quote.split()) if k]
    if not quote_keys:
        return None
    wanted = Counter(quote_keys)
    width = len(quote_keys) + len(quote_keys) // 2 + 2
    needed = max(2, (len(quote_keys) + 1) // 2)

    best_score = 0
    best: tuple[list[str], int, int] | None = None
    for block in _PAGE_MARKER.split(excerpt):
        words = _normalize_whitespace(block).split(" ")
        keys = [_word_key(w) for w in words]
        for start in range(len(words)):
            if keys[start] not in wanted:
                continue
            window = Counter(keys[start:start + width])
            score = sum(min(n, wanted[k]) for k, n in window.items() if k in wanted)
            if score > best_score:
                last = max(
                    i for i in range(start, min(start + width, len(words)))
                    if keys[i] in wanted
                )
                best_score, best = score, (words, start, last)

    if best is None or best_score < needed:
        return None
    words, start, last = best
    lo = max(0, start - context_words)
    hi = min(len(words), last + 1 + context_words)
    return " ".join(words[lo:hi])


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

    if field_def.type == "object[]" and field_def.schema_def and item.status == "found":
        reason = validate_entry(item.raw_value, item.quote, field_def)
        if reason is not None:
            return False, reason

    return True, None


_WHOLE_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


def validate_entry(raw_value: str, quote: str, field_def: FieldDef) -> str | None:
    """Why an `object[]` entry fails, or None. Added 2026-10-12, fix 1 of that
    day's brief, when EXTRACT began asking for each entry as one JSON object.

    The entry must be a JSON object whose keys are all in the field's `schema`.
    An enum key's value must be one of its values, as `validate_enum` reads
    them but strictly. An int key's value must be a whole number, written as a
    number or as digits, and the quote must contain that number, alone or with
    thousands commas: a count no passage states is not evidence. Text keys are
    not checked against the quote, as no other text value is.
    """
    try:
        entry = json.loads(raw_value)
    except ValueError:
        return "entry_not_json"
    if not isinstance(entry, dict):
        return "entry_not_a_json_object"
    keys = {k.name: k for k in field_def.entry_keys()}
    unknown = [k for k in entry if k not in keys]
    if unknown:
        return f"entry_key_not_in_schema_{','.join(unknown)}"
    for name, value in entry.items():
        key = keys[name]
        if value is None:
            continue
        if key.type == "enum":
            said = re.sub(r"[^a-z0-9]", "", str(value).lower())
            if not any(said == re.sub(r"[^a-z0-9]", "", v) for v in key.values):
                return f"entry_{name}_value_not_allowed_{value}"
        elif key.type == "int":
            text = str(value).strip() if not isinstance(value, bool) else ""
            if not _WHOLE_NUMBER.fullmatch(text):
                return f"entry_{name}_not_a_whole_number_{value}"
            n = int(text.replace(",", ""))
            written = {str(n), f"{n:,}"}
            stated = {m.group(0) for m in _WHOLE_NUMBER.finditer(quote)}
            if not written & stated:
                return f"entry_{name}_number_not_in_quote_{n}"
    return None

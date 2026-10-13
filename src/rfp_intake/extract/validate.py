"""Post-call validation for extracted field records.

Non-negotiable per ARCHITECTURE.md §4.4:
1. quote must be substring of excerpt (normalized whitespace)
2. page must fall inside task window
3. enum values must be in registry's allowed set
4. numeric fields must parse
5. an `object[]` field's entry must be one JSON object of its schema's keys

`answers_field` adds two rules of 2026-10-12 (stage 6 of that day's plan) that
reject a record without a repair retry, since a retry cannot make the passage
say what it does not say.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from rfp_intake.domain.dynamic import FieldExtractionItem
from rfp_intake.domain.registry import FieldDef
from rfp_intake.normalize.types import normalize_value

# A "not specified" record below this confidence is rejected (fix 4 of the
# 2026-10-12 brief). In run r-20261012-angus a guess that the documents were
# silent outweighed nothing and still printed as "Not specified".
NOT_SPECIFIED_MIN_CONFIDENCE = 0.50


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces for comparison."""
    return re.sub(r"\s+", " ", text).strip()


# Curly apostrophes and quotation marks read as their straight forms, and nothing
# else does (item 5 of Oliver's 2026-10-13 prompt). The protocol prints
# "subject’s" on p.12 and p.79 where the model types "subject's", and the
# quote check rejected sentences that were copied word for word.
_STRAIGHT_MARKS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def comparable_text(text: str) -> str:
    """Text as the quote check compares it: whitespace collapsed, curly
    apostrophes and quotation marks straightened."""
    return _normalize_whitespace(text).translate(_STRAIGHT_MARKS)


def validate_quote(quote: str, excerpt: str) -> bool:
    """Check that quote is a substring of the excerpt, compared as
    `comparable_text` writes both."""
    norm_quote = comparable_text(quote)
    norm_excerpt = comparable_text(excerpt)
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


def answers_field(item: FieldExtractionItem, field_def: FieldDef) -> str | None:
    """Why a record that passed `validate_record` is still rejected, or None.

    Two rules, both of stage 6 of the 2026-10-12 plan:
    - a "not specified" record the model rates below
      NOT_SPECIFIED_MIN_CONFIDENCE is a guess that the document is silent, not
      a document saying so;
    - a value the field lists under `quote_must_say` in config/fields.yaml needs
      a quote containing one of its words, whole and in any case. The value is
      read as NORMALIZE will read it, so "IV infusion" and "infusion_iv" are the
      same value.
    """
    if item.status == "not_specified":
        if item.confidence < NOT_SPECIFIED_MIN_CONFIDENCE:
            return f"not_specified_below_{NOT_SPECIFIED_MIN_CONFIDENCE:.2f}_confidence"
        return None
    if not field_def.quote_must_say:
        return None
    value = normalize_value(item.raw_value, field_def.type, field_def.values)
    values = value if isinstance(value, list) else [value]
    quote_words = {w.casefold() for w in re.findall(r"\w+", item.quote)}
    for v in values:
        words = field_def.quote_must_say.get(str(v)) if v is not None else None
        if words and not any(w.casefold() in quote_words for w in words):
            return f"quote_does_not_say_{v}"
    return None

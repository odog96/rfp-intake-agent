"""Load and validate config/sections.yaml into a typed Pydantic model.

Mirrors domain/precedence.py: this is the loader only. Deciding which sections
to drop lives in sections/set_aside.py, with the node that does it.

The two lists are kept here rather than in the node so that nothing in
`sections/` has to import `yaml`, and so a test can load a policy from a
hand-written file without touching the graph.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from rfp_intake.config.settings import get_settings

# Mirrors sections/headings.py `_NUMBERED`: a heading's leading dotted number is
# furniture, so "7.2 Inclusion Criteria" must match the entry "inclusion
# criteria". Kept as its own pattern rather than imported, because headings.py
# captures the number for its own use and this only wants it gone.
_LEADING_NUMBER = re.compile(r"^\d{1,2}(?:\.\d{1,3}){0,4}\.?\s*")


def normalise_heading(heading: str) -> str:
    """A heading reduced to what matching compares: no number, one space, lowercase."""
    collapsed = " ".join(heading.split())
    return _LEADING_NUMBER.sub("", collapsed).lower()


@lru_cache(maxsize=1024)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Match `phrase` as whole words, however its spaces are broken up.

    Whole words, so `ethics` matches "Ethics Committee" but not "Bioethics", and
    `crf` does not match "eCRF" (which has its own entry). `(?<!\\w)`/`(?!\\w)`
    rather than `\\b`, because `\\b` is in the wrong place for a phrase that
    starts or ends with punctuation, such as `non-clinical`.

    `\\s+` between the words, because PDF text puts line breaks inside a phrase:
    the protocol's "unblinded pharmacy staff" spans two lines.
    """
    words = [re.escape(word) for word in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def contains_phrase(text: str, phrase: str) -> bool:
    """True when `text` contains `phrase` as a whole phrase, case-insensitively."""
    if not phrase or not text:
        return False
    return _phrase_pattern(phrase).search(text) is not None


# How far either side of a phrase a number still counts as belonging to it.
# "Approximately 350 eCRF pages are expected" and "eCRF pages: 350" both fit.
NUMBER_WINDOW_CHARS = 60


def contains_phrase_with_number(text: str, phrase: str) -> bool:
    """True when `text` contains `phrase` with a digit close to it.

    docs/ANALYST_PROCEDURE_PROTOCOL.md section 4: ignore "case report form
    details, unless the text gives a number of case report forms" [02:00:16]. The
    number is the whole point — a count can be budgeted, prose about how to
    complete a form cannot — and on `samples/Example protocol 2.pdf` the bare
    phrase appears in the glossary, the adverse-event section and the compliance
    section, none of which state a count.
    """
    if not phrase or not text:
        return False
    for match in _phrase_pattern(phrase).finditer(text):
        before = text[max(0, match.start() - NUMBER_WINDOW_CHARS) : match.start()]
        after = text[match.end() : match.end() + NUMBER_WINDOW_CHARS]
        if any(char.isdigit() for char in before + after):
            return True
    return False


# Enough slack for the whitespace PyMuPDF inserts inside a heading.
_HEADING_SLACK_CHARS = 40


def strip_heading(text: str, heading: str) -> str:
    """`text` with its own leading heading removed, so a rescue reads the body only.

    A section's text starts at its heading, and the heading has already had its
    say. Section "8.1 Emergency Unblinding" of the sample protocol is on
    `set_aside` and its heading contains the rescue phrase `unblinded`, so a
    rescue that read the heading would let every listed section rescue itself.

    Whitespace-insensitive, because PyMuPDF returns "1.3.2 \\nClinical
    Experience". If the heading is not where it is expected, the whole text is
    returned: the effect is to keep a section that might otherwise go, which is
    the safe direction.
    """
    needle = "".join(heading.split()).lower()
    if not needle:
        return text

    limit = min(len(text), len(heading) + _HEADING_SLACK_CHARS)
    position = 0
    matched = 0
    while position < limit and matched < len(needle):
        char = text[position]
        position += 1
        if char.isspace():
            continue
        if char.lower() != needle[matched]:
            return text
        matched += 1

    return text[position:] if matched == len(needle) else text


class SectionsPolicy(BaseModel):
    """The two lists from config/sections.yaml, normalised for matching.

    Both are stored lowercased and whitespace-collapsed, which is the form
    `normalise_heading` produces, so a comparison never has to normalise twice.
    """

    set_aside: list[str] = Field(default_factory=list)
    always_set_aside: list[str] = Field(default_factory=list)
    keep_if_contains: list[str] = Field(default_factory=list)
    keep_if_contains_with_number: list[str] = Field(default_factory=list)

    def is_always_set_aside(self, heading: str) -> bool:
        """True for an index section, which no phrase in its text can rescue.

        A table of contents, a list of tables and a glossary are made of the
        document's own headings and definitions. Every rescue phrase appears in
        them by construction — on `samples/Example protocol 2.pdf` the contents
        page rescued itself with "Emergency Unblinding" and the glossary with
        "case report form" — and none of those mentions is a fact about the
        study. So these sections go whatever their text says.
        """
        normalised = normalise_heading(heading)
        if not normalised:
            return False
        return any(contains_phrase(normalised, entry) for entry in self.always_set_aside)

    def matching_set_aside(self, heading: str) -> str | None:
        """The first `set_aside` entry matching this heading, or None to keep it.

        Returns the entry rather than a bool so the node can record *why* a
        section went, which is the only way to tell a deliberate removal from a
        heading that matched something nobody intended.
        """
        normalised = normalise_heading(heading)
        if not normalised:
            return None
        for entry in [*self.always_set_aside, *self.set_aside]:
            if contains_phrase(normalised, entry):
                return entry
        return None

    def rescuing_phrase(self, body: str) -> str | None:
        """The first `keep_if_contains` phrase in this section's body, or None.

        docs/ANALYST_PROCEDURE_PROTOCOL.md section 5: the heading says the
        section is irrelevant but one sentence inside it drives cost.

        `body` must be the section's text with its heading stripped — see
        `strip_heading`. Phrases on `keep_if_contains_with_number` only rescue
        when a number is near them.
        """
        for phrase in self.keep_if_contains:
            if contains_phrase(body, phrase):
                return phrase
        for phrase in self.keep_if_contains_with_number:
            if contains_phrase_with_number(body, phrase):
                return phrase
        return None


def load_sections_policy(path: Path | None = None) -> SectionsPolicy:
    """Load sections.yaml into a typed SectionsPolicy.

    A missing file raises rather than returning an empty policy. An empty policy
    would turn SET_ASIDE_SECTIONS into a silent no-op: every run would still
    succeed, every section would still be read, and the only sign would be a
    larger bill. Failing here makes a broken deployment obvious.
    """
    if path is None:
        path = Path(get_settings().sections_yaml_path)

    if not path.exists():
        raise FileNotFoundError(f"Sections policy not found: {path}")

    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    if "set_aside" not in data:
        raise ValueError(f"Sections policy has no 'set_aside' list: {path}")

    return SectionsPolicy(
        set_aside=_clean(data.get("set_aside")),
        always_set_aside=_clean(data.get("always_set_aside")),
        keep_if_contains=_clean(data.get("keep_if_contains")),
        keep_if_contains_with_number=_clean(data.get("keep_if_contains_with_number")),
    )


def _clean(entries: Any) -> list[str]:
    """Normalise a yaml list to lowercase, single-spaced, non-empty strings."""
    if not entries:
        return []
    cleaned = []
    for entry in entries:
        text = " ".join(str(entry).split()).lower()
        if text:
            cleaned.append(text)
    return cleaned


@lru_cache(maxsize=1)
def get_sections_policy() -> SectionsPolicy:
    """Singleton access to the loaded sections policy."""
    return load_sections_policy()


def reset_sections_policy() -> None:
    """For testing — clear the cached policy after changing the settings path."""
    get_sections_policy.cache_clear()

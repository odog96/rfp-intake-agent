"""Canonicalise the scope label a model writes onto a record.

`FieldRecord.scope` says which part of the study a value applies to — the whole
study, one treatment arm, one country. RECONCILE groups records by it, so two
records only get compared when they are talking about the same thing.

The label is free text written by the model, and the source documents name the
same arm differently on purpose. Observed in run r-sonnet46-pair-114917: the
placebo arm arrived as `cohort:Placebo`, `cohort:placebo` and
`cohort:Placebo+SoC`, so the number of subjects resolved three times instead of
once. Recognising those as one thing is the pipeline's job, not the document's.

Pure functions, table-driven tests, no LLM (CLAUDE.md rule 7).
"""

from __future__ import annotations

import re

# Prefixes that mean "one treatment arm". The documents use these
# interchangeably — "Arm 1", "Group 1" and "Cohort A" are the same kind of thing.
_ARM_PREFIXES = {"arm", "cohort", "group", "part", "treatment", "branch"}

# Prefixes worth keeping distinct, mapped to one spelling each.
_PREFIX_ALIASES = {
    "country": "country",
    "countries": "country",
    "region": "country",
    "site": "site",
    "period": "period",
    "phase": "period",
    "visit": "visit",
}

# Labels that all mean "the study as a whole" rather than one part of it.
_WHOLE_STUDY = {
    "total",
    "overall",
    "study",
    "whole study",
    "entire study",
    "all",
    "all arms",
    "all cohorts",
    "combined",
    "study-wide",
    "studywide",
    "global",
}

# Trailing qualifiers that describe co-administered background therapy rather
# than identifying a different arm. "NEOD001" and "NEOD001 + SoC" are one arm.
_BACKGROUND_THERAPY = re.compile(
    r"""
    \s*
    (?:\+|\bplus\b|\band\b|\bwith\b)
    \s*
    (?:
        soc\b
      | standard[\s\-]of[\s\-]care\b
      | background[\s\-]therapy\b
      | concomitant[\s\-]\w+
    )
    .*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_scope(scope: str | None) -> str | None:
    """Return a canonical form of `scope`, or None when it is absent.

    None is preserved rather than folded into "total". An absent scope means the
    model did not say which part of the study the value covers, which is not the
    same claim as "this covers the whole study" — collapsing the two would
    silently merge an unqualified number into the study-wide total.
    """
    if scope is None:
        return None

    text = scope.strip().lower()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None

    prefix, _, label = text.partition(":")
    if not label:
        prefix, label = "", text
    prefix = prefix.strip()
    label = label.strip()

    label = _BACKGROUND_THERAPY.sub("", label).strip()
    label = label.strip(" .,;-_()[]")

    if not label:
        return _canonical_prefix(prefix) or None

    if label in _WHOLE_STUDY and _canonical_prefix(prefix) is None:
        return "total"

    canonical_prefix = _canonical_prefix(prefix)
    if canonical_prefix is None:
        return label
    return f"{canonical_prefix}:{label}"


def _canonical_prefix(prefix: str) -> str | None:
    if not prefix:
        return None
    if prefix in _ARM_PREFIXES:
        return "cohort"
    return _PREFIX_ALIASES.get(prefix, prefix)


def scopes_match(left: str | None, right: str | None) -> bool:
    """Whether two scope labels refer to the same part of the study."""
    return normalize_scope(left) == normalize_scope(right)

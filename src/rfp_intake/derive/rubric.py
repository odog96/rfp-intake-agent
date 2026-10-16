"""Transparent, table-driven rubrics for DERIVE. Pure Python, zero LLM.

Each rubric is a scored sum over already-resolved evidence, never a "vibe
call" — ARCHITECTURE.md §4.8. A reviewer must be able to see why a rating
landed where it did and disagree with it.

Each rubric says why twice. `why` is the reasons in plain words, for the row in
report.pdf: the words come from `value_words` and the labels in
config/fields.yaml, never a field id or an enum spelling (fix 8 of the
2026-10-12 brief, CLAUDE.md open item 5). `explanation` is the full account for
Appendix A — the same reasons, then the score or every input with its quote.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import Provenance, ResolvedField

# visits.intensity_evidence weights, per ARCHITECTURE.md §4.8's rubric table.
# fields.yaml's `visits.intensity_evidence` enum has evolved past the exact
# evidence names in that table (e.g. `overnight_stay`, `complex_procedures`
# were added; "visit window < 3 days" became `long_visit_windows`, which
# inverts the original wording — a *narrow* window signals tighter, more
# intensive monitoring, which is what `long_visit_windows` is standing in
# for here). Weights below are a best-effort mapping onto the current enum,
# not a re-derivation from source. Recalibrate against the golden set per
# ARCHITECTURE.md §9 rather than trusting these numbers as-is.
#
# The order is the order the "Why" line names the evidence in, heaviest first
# and, within a weight, the order below: visits, then the infusion, then
# procedures and samples (fix 8 of the 2026-10-12 brief).
VISIT_INTENSITY_WEIGHTS: dict[str, int] = {
    "frequent_early_visits": 2,
    "infusion_observation_period": 2,
    "overnight_stay": 2,
    "imaging": 2,
    "pk_pd_sampling": 2,
    "complex_procedures": 2,
    "multiple_assessments_per_visit": 2,
    "ecgs": 1,
    "safety_labs": 1,
    "biomarker_sampling": 1,
    "questionnaires": 1,
    "long_visit_windows": 1,
    "none_found": 0,
}
_WEIGHT_ORDER = {flag: i for i, flag in enumerate(VISIT_INTENSITY_WEIGHTS)}

VISIT_INTENSITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (8, "high"),
    (4, "moderate"),
    (0, "low"),
)


# blinding.placebo_assumption, from docs/ANALYST_PROCEDURE_PROTOCOL.md section 6.
# The route decides rules 3 and 4, so `ip.form`'s enum is split in two here.
# `intrathecal` counts as injected: it is a needle into the spine, and the reason
# rule 3 exists — a liquid is hard to colour-match — applies to it exactly as it
# does to a drip. Anything in neither set (topical, inhaled, ophthalmic, other,
# not_specified) decides nothing, and the rubric says so rather than falling
# through to the oral rule, which would read as reassurance nobody had evidence
# for.
_INJECTED_FORMS = frozenset({"injection_sc", "injection_im", "infusion_iv", "intrathecal"})
_ORAL_FORMS = frozenset({"oral_tablet", "oral_capsule", "oral_solution"})

# `blinding.unblinded_staff_stated` reads `true`, not `yes`: YAML turns an
# unquoted `yes` into a boolean, and every other yes-or-no field in
# config/fields.yaml already says `true`/`false`. Both spellings are accepted
# here so that a reworded enum cannot silently switch the contradiction rule off,
# and test_rubric.py asserts the registry still offers one of them.
_STAFF_STATED_YES = frozenset({"true", "yes"})

PLACEBO_OPEN_LABEL = "not applicable: open-label"
PLACEBO_INJECTED_NOT_STATED = "probably not matching; unblinded handling likely"
PLACEBO_ORAL_NOT_STATED = (
    "matching not confirmed; oral drug; do not assume unblinded monitoring"
)
PLACEBO_CONTRADICTION = (
    "the documents contradict each other: ask the sponsor whether the placebo matches"
)
# An input whose rows give different answers, where the rule reached needs that
# input. The rubric does not choose one of them (approved by Oliver, 2026-10-11).
PLACEBO_INPUTS_DISAGREE = (
    "the documents give more than one answer to a question this depends on: ask the sponsor"
)


@dataclass
class RubricResult:
    value: str | None
    status: Literal["not_specified", "needs_review"]
    confidence: float
    sources: list[Provenance] = field(default_factory=list)
    explanation: str = ""
    why: str = ""


def _usable(rows: list[ResolvedField]) -> list[ResolvedField]:
    """The rows that carry an answer. A `not_found`, `not_specified` or
    `not_applicable` row is treated as absent, because a rubric reading a
    missing answer as a real one is how an inference becomes a fact."""
    return [r for r in rows if r.status not in ("not_found", "not_specified", "not_applicable")]


def _sources_of(rows: list[ResolvedField]) -> list[Provenance]:
    """Every row's pages, each page once, in the order the rows came."""
    out: list[Provenance] = []
    for r in rows:
        out.extend(p for p in r.sources if p not in out)
    return out


def _word(fd: FieldDef | None, value: str) -> str:
    """The word report.pdf prints for an enum value: its `value_words` entry in
    config/fields.yaml, or the value with spaces for its underscores."""
    if fd is not None and value in fd.value_words:
        return fd.value_words[value]
    return value.replace("_", " ")


def _in_sentence(word: str) -> str:
    """A word as it reads inside a sentence: "Imaging" becomes "imaging", while
    "ECGs" and "IV infusion", which open with capitals, stay as they are."""
    if word[:2].isupper():
        return word
    return word[:1].lower() + word[1:]


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _label(fields: dict[str, FieldDef], field_id: str) -> str:
    fd = fields.get(field_id)
    return fd.label if fd is not None else field_id


def compute_visit_intensity(
    inputs: dict[str, list[ResolvedField]], registry: Registry | None = None
) -> RubricResult:
    """Score `visits.intensity_evidence` flags into a low/moderate/high rating.

    `inputs` is keyed by the field ids in visits.intensity_rating's
    `derived_from` (ARCHITECTURE.md §4.8: evidence, frequency, count), each with
    every row DERIVE did not leave out. Only `visits.intensity_evidence`
    currently drives the score — see the module docstring on
    VISIT_INTENSITY_WEIGHTS for why frequency/count aren't independently
    scored: the evidence enum already carries a `frequent_early_visits` flag
    for that signal.

    The flags of every evidence row are combined and each is counted once:
    a "Months 1-3" row names evidence about the same patients as the
    study-wide row, so it adds to it rather than replacing it (approved by
    Oliver, 2026-10-11). The confidence is the weakest row's, because the
    rating rests on all of them.
    """
    rows = [
        r for r in _usable(inputs.get("visits.intensity_evidence", []))
        if isinstance(r.value, list)
    ]
    flags = list(dict.fromkeys(
        f for r in rows for f in (r.value or []) if isinstance(f, str)
    ))
    if all(f == "none_found" for f in flags):
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)

    score = sum(VISIT_INTENSITY_WEIGHTS.get(flag, 0) for flag in flags)
    rating = next(label for floor, label in VISIT_INTENSITY_THRESHOLDS if score >= floor)

    contributing = sorted(
        (f for f in flags if VISIT_INTENSITY_WEIGHTS.get(f, 0) > 0),
        key=lambda f: (-VISIT_INTENSITY_WEIGHTS.get(f, 0), _WEIGHT_ORDER.get(f, 0)),
    )
    fields = {f.id: f for f in (registry or get_registry()).fields}
    evidence_def = fields.get("visits.intensity_evidence")

    def phrase(flag: str) -> str:
        """A flag's words, with the part of the study it was found for when every
        row naming it is about one part, as in "(Months 1-3)"."""
        words = _in_sentence(_word(evidence_def, flag))
        scopes = [r.scope for r in rows if flag in (r.value or [])]
        if scopes and all(scopes):
            words += f" ({', '.join(dict.fromkeys(s for s in scopes if s))})"
        return words

    why = ("; ".join(phrase(f) for f in contributing) or "no weighted evidence was found") + "."
    explanation = (
        f"{why[0].upper()}{why[1:]} Scored {score} points: 0 to 3 is low, "
        f"4 to 7 moderate, 8 or more high"
    )
    if len(rows) > 1:
        # Each scope once, with how many rows had it: a run can hold thirty rows
        # with no scope, and naming each one buries the scopes that matter.
        scopes = Counter(f'"{r.scope}"' if r.scope else "no scope stated" for r in rows)
        explanation += (
            f"; combined {len(rows)} rows of "
            f"{_in_sentence(_label(fields, 'visits.intensity_evidence'))}: "
        ) + ", ".join(f"{scope} ({n})" if n > 1 else scope for scope, n in scopes.items())

    return RubricResult(
        value=rating,
        status="needs_review",  # GATE always sends derived fields to needs_review
        confidence=min(r.confidence for r in rows),
        sources=_sources_of(rows),
        explanation=explanation,
        why=why,
    )


def _answer_key(value: object) -> object:
    """Two rows give the same answer if they differ only in case or list order."""
    if isinstance(value, list):
        return tuple(sorted(str(v).casefold() for v in value))
    return str(value).casefold()


def _agreed(rows: list[ResolvedField]) -> tuple[ResolvedField | None, list[ResolvedField]]:
    """The one answer an input's rows give, or every row when they differ.

    Returns `(row, [row])` when the usable rows agree — the first stands for
    them all — and `(None, rows)` when they differ, so the caller cannot use one
    by accident. With no usable row, the first row (a `not_found` one, say)
    stands in, so the explanation can still say what came back.
    """
    usable = [r for r in _usable(rows) if r.value is not None]
    if len({_answer_key(r.value) for r in usable}) > 1:
        return None, usable
    first = usable[0] if usable else (rows[0] if rows else None)
    return first, [first] if first is not None else []


def _value_of(resolved: ResolvedField | None) -> str | None:
    """One enum value off a resolved field, or None if there is nothing usable."""
    if resolved is None or not _usable([resolved]):
        return None
    return resolved.value if isinstance(resolved.value, str) else None


def _forms_of(resolved: ResolvedField | None) -> list[str]:
    """`ip.form` is list[enum], so it arrives as a list and may hold several routes."""
    if resolved is None or not _usable([resolved]):
        return []
    if isinstance(resolved.value, list):
        return [v for v in resolved.value if isinstance(v, str)]
    return [resolved.value] if isinstance(resolved.value, str) else []


# What `_cite` says for a row with no value, by its status.
_NO_VALUE_WORDS = {
    "needs_review": "no single answer",
    "not_found": "not found",
    "not_specified": "not specified",
}


def _cite(fd: FieldDef | None, field_id: str, rows: list[ResolvedField]) -> str:
    """One input named in an explanation, by its label and in the report's words,
    with its quote when it has one.

    Every branch below names the inputs it used, which
    `docs/PLAN_2026-10-02.md` stage 5 item 9 requires: a person has to be able to
    see which four answers produced the assumption and disagree with it. Where
    an input's rows disagree, every answer is named.
    """
    label = fd.label if fd is not None else field_id
    if not rows:
        return f"{label}: not found"
    said = []
    for resolved in rows:
        value = resolved.value
        if value is None:
            words = _NO_VALUE_WORDS.get(resolved.status, resolved.status.replace("_", " "))
        elif isinstance(value, list):
            words = ", ".join(_word(fd, str(v)) for v in value)
        else:
            words = _word(fd, str(value))
        said.append(f'{words} ("{resolved.quote}")' if resolved.quote else words)
    return f"{label}: " + " or ".join(said)


def compute_placebo_assumption(
    inputs: dict[str, list[ResolvedField]], registry: Registry | None = None
) -> RubricResult:
    """What to assume about the placebo, from `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 6.

    Rules 1 to 4 in the order the plan sets, with rule 6's contradiction checked
    before rule 2 — because rule 2 would otherwise accept `matching_stated` and
    report the matching placebo as settled, which is the one outcome the
    contradiction exists to prevent. Rule 1 still comes first: on an open-label
    study nobody is blinded, so unblinded staff and a matching placebo are not in
    tension and there is nothing to ask the sponsor about.

    Each input arrives as every row DERIVE has for it. Rows that agree are one
    answer. Rows that disagree decide nothing: when the rule reached needs that
    input, the answer is PLACEBO_INPUTS_DISAGREE, naming every row, rather than
    one row chosen by position (approved by Oliver, 2026-10-11).

    **RECONCILE cannot do this.** It compares values of the same field across
    documents (`reconcile/__init__.py`), and this is a disagreement between two
    different fields. That is why the check lives in a rubric.
    """
    names = (
        "blinding.design", "blinding.placebo_matching", "blinding.unblinded_staff_stated",
        "ip.form",
    )
    agreed = {name: _agreed(inputs.get(name, [])) for name in names}
    design, design_rows = agreed["blinding.design"]
    matching, matching_rows = agreed["blinding.placebo_matching"]
    staff, staff_rows = agreed["blinding.unblinded_staff_stated"]
    form, form_rows = agreed["ip.form"]

    design_value = _value_of(design)
    matching_value = _value_of(matching)
    staff_value = _value_of(staff)
    forms = _forms_of(form)

    fields = {f.id: f for f in (registry or get_registry()).fields}
    # `not_specified` names no route, so the "Why" line does not say the drug is
    # given by it.
    named_forms = [f for f in forms if f != "not_specified"]
    form_words = _and([_in_sentence(_word(fields.get("ip.form"), f)) for f in named_forms]) \
        if named_forms else ""

    every_row = [r for name in names for r in inputs.get(name, [])]
    # Every row, used or not: an answer resting on an input nobody could settle
    # is no surer for having been reached another way.
    confidence = min((r.confidence for r in every_row), default=0.0)

    def answered_by(name: str) -> list[ResolvedField]:
        """The rows whose answer the rule used: every row giving it, not only the first.

        Empty when no row gives an answer: the rule used the absence, and an
        unsettled row's page is not where an absence is stated.
        """
        return [r for r in _usable(inputs.get(name, [])) if r.value is not None]

    def result(value: str, why: str, *used: tuple[str, list[ResolvedField]]) -> RubricResult:
        """The answer, citing only the inputs the rule reached it from.

        An input the rule did not use is named after "Looked at but not used" and
        is not cited, so a page in the row's sources is always a page the answer
        rests on (CLAUDE.md open item 4, approved by Oliver on 2026-10-12).
        """
        used_names = [name for name, _ in used]
        explanation = f"{why} Read from " + "; ".join(
            _cite(fields.get(name), name, rows) for name, rows in used
        ) + "."
        unused = [
            _cite(fields.get(name), name, agreed[name][1]) for name in names
            if name not in used_names and inputs.get(name)
        ]
        if unused:
            explanation += " Looked at but not used: " + "; ".join(unused) + "."
        return RubricResult(
            value=value,
            status="needs_review",  # GATE always sends derived fields to needs_review
            confidence=confidence,
            sources=_sources_of([r for name in used_names for r in answered_by(name)]),
            explanation=explanation,
            why=why,
        )

    def split(name: str) -> bool:
        return agreed[name][0] is None and len(agreed[name][1]) > 1

    def disagree(name: str, *also: tuple[str, list[ResolvedField]]) -> RubricResult:
        why = f'The documents give different answers for "{_label(fields, name)}".'
        return result(PLACEBO_INPUTS_DISAGREE, why, *also, (name, agreed[name][1]))

    if split("blinding.design"):
        return disagree("blinding.design")

    # Rule 1. Open-label: whether the placebo matches does not matter.
    if design_value == "open_label":
        return result(
            PLACEBO_OPEN_LABEL, "The study is open-label, so nobody is blinded.",
            ("blinding.design", design_rows),
        )

    if split("blinding.placebo_matching"):
        return disagree("blinding.placebo_matching")
    # Rule 6 turns on the staff answer only when matching is stated.
    if matching_value == "matching_stated" and split("blinding.unblinded_staff_stated"):
        return disagree(
            "blinding.unblinded_staff_stated", ("blinding.placebo_matching", matching_rows)
        )

    # Rule 6. Stated matching and stated unblinded staff cannot both be right.
    if matching_value == "matching_stated" and staff_value in _STAFF_STATED_YES:
        return result(
            PLACEBO_CONTRADICTION,
            "The documents say the placebo matches and also that unblinded staff are "
            "needed; both cannot be right.",
            ("blinding.placebo_matching", matching_rows),
            ("blinding.unblinded_staff_stated", staff_rows),
        )

    # Rule 2. The document says: use what it says.
    if matching_value == "matching_stated":
        return result(
            "the document says the placebo matches as supplied",
            "The documents say the placebo matches as supplied.",
            ("blinding.placebo_matching", matching_rows),
        )
    if matching_value == "not_matching_stated":
        return result(
            "the document says the placebo does not match; unblinded handling likely",
            "The documents say the placebo does not match, so unblinded staff will likely "
            "prepare it.",
            ("blinding.placebo_matching", matching_rows),
        )

    if split("ip.form"):
        return disagree("ip.form")

    # Rules 3 and 4. The document does not say, so the route decides.
    # Injected wins a document stating both routes: the liquid is the one that
    # cannot be colour-matched, and under-pricing unblinded staff costs more than
    # pricing them needlessly.
    if any(f in _INJECTED_FORMS for f in forms):
        return result(
            PLACEBO_INJECTED_NOT_STATED,
            f"The drug is given by {form_words}, so the placebo is likely prepared by "
            "unblinded pharmacy staff.",
            ("ip.form", form_rows),
        )
    if any(f in _ORAL_FORMS for f in forms):
        return result(
            PLACEBO_ORAL_NOT_STATED,
            f"The drug is given by {form_words}, and the documents do not settle whether the "
            "placebo matches, so do not assume unblinded monitoring.",
            ("ip.form", form_rows),
        )

    # Here and below, as in rules 3 and 4, the matching row settled nothing, so it
    # is looked at but not used: citing it would cite a page the answer does not
    # rest on, such as an unsettled "Matching placebo" (CLAUDE.md open item 4).
    if not forms:
        return result(
            "matching not stated and the document does not say how the drug is given; "
            "ask the sponsor",
            "No document says how the drug is given.",
            ("ip.form", form_rows),
        )
    return result(
        "matching not stated and the route of administration does not decide it; "
        "ask the sponsor",
        f"The drug is given by {form_words}, which does not settle whether the placebo matches."
        if named_forms else "No document says how the drug is given.",
        ("ip.form", form_rows),
    )

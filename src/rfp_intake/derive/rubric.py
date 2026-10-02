"""Transparent, table-driven rubrics for DERIVE. Pure Python, zero LLM.

Each rubric is a scored sum over already-resolved evidence, never a "vibe
call" — ARCHITECTURE.md §4.8. A reviewer must be able to see why a rating
landed where it did and disagree with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
VISIT_INTENSITY_WEIGHTS: dict[str, int] = {
    "pk_pd_sampling": 2,
    "biomarker_sampling": 1,
    "imaging": 2,
    "ecgs": 1,
    "safety_labs": 1,
    "questionnaires": 1,
    "infusion_observation_period": 2,
    "multiple_assessments_per_visit": 2,
    "frequent_early_visits": 2,
    "complex_procedures": 2,
    "overnight_stay": 2,
    "long_visit_windows": 1,
    "none_found": 0,
}

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


@dataclass
class RubricResult:
    value: str | None
    status: Literal["not_specified", "needs_review"]
    confidence: float
    sources: list[Provenance] = field(default_factory=list)
    explanation: str = ""


def compute_visit_intensity(inputs: dict[str, ResolvedField | None]) -> RubricResult:
    """Score `visits.intensity_evidence` flags into a low/moderate/high rating.

    `inputs` is keyed by the field ids in visits.intensity_rating's
    `derived_from` (ARCHITECTURE.md §4.8: evidence, frequency, count).
    Only `visits.intensity_evidence` currently drives the score — see the
    module docstring on VISIT_INTENSITY_WEIGHTS for why frequency/count
    aren't independently scored: the evidence enum already carries a
    `frequent_early_visits` flag for that signal.
    """
    evidence = inputs.get("visits.intensity_evidence")
    if evidence is None or evidence.status != "needs_review":
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)
    if not isinstance(evidence.value, list):
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)

    flags: list[str] = [f for f in evidence.value if isinstance(f, str)]
    if not flags or flags == ["none_found"]:
        return RubricResult(value="not_specified", status="not_specified", confidence=0.0)

    score = sum(VISIT_INTENSITY_WEIGHTS.get(flag, 0) for flag in flags)
    rating = next(label for floor, label in VISIT_INTENSITY_THRESHOLDS if score >= floor)

    contributing = sorted(
        (f for f in flags if VISIT_INTENSITY_WEIGHTS.get(f, 0) > 0),
        key=lambda f: -VISIT_INTENSITY_WEIGHTS.get(f, 0),
    )
    explanation = (
        f"score={score} from {', '.join(contributing) or 'no weighted evidence'} "
        f"(thresholds: 0-3 low, 4-7 moderate, 8+ high)"
    )

    return RubricResult(
        value=rating,
        status="needs_review",  # GATE always sends derived fields to needs_review
        confidence=evidence.confidence,
        sources=list(evidence.sources),
        explanation=explanation,
    )


def _value_of(resolved: ResolvedField | None) -> str | None:
    """One enum value off a resolved field, or None if there is nothing usable.

    A field that came back `not_found` is treated as absent, because a rubric
    reading a missing answer as a real one is how an inference becomes a fact.
    """
    if resolved is None or resolved.status in ("not_found", "not_specified"):
        return None
    return resolved.value if isinstance(resolved.value, str) else None


def _forms_of(resolved: ResolvedField | None) -> list[str]:
    """`ip.form` is list[enum], so it arrives as a list and may hold several routes."""
    if resolved is None or resolved.status in ("not_found", "not_specified"):
        return []
    if isinstance(resolved.value, list):
        return [v for v in resolved.value if isinstance(v, str)]
    return [resolved.value] if isinstance(resolved.value, str) else []


def _cite(label: str, resolved: ResolvedField | None) -> str:
    """One input named in an explanation, with its quote when it has one.

    Every branch below names the inputs it used, which
    `docs/PLAN_2026-10-02.md` stage 5 item 9 requires: a person has to be able to
    see which four answers produced the assumption and disagree with it.
    """
    if resolved is None:
        return f"{label}=absent"
    value = resolved.value if resolved.value is not None else resolved.status
    if resolved.quote:
        return f'{label}={value} ("{resolved.quote}")'
    return f"{label}={value}"


def compute_placebo_assumption(inputs: dict[str, ResolvedField | None]) -> RubricResult:
    """What to assume about the placebo, from `docs/ANALYST_PROCEDURE_PROTOCOL.md` section 6.

    Rules 1 to 4 in the order the plan sets, with rule 6's contradiction checked
    before rule 2 — because rule 2 would otherwise accept `matching_stated` and
    report the matching placebo as settled, which is the one outcome the
    contradiction exists to prevent. Rule 1 still comes first: on an open-label
    study nobody is blinded, so unblinded staff and a matching placebo are not in
    tension and there is nothing to ask the sponsor about.

    **RECONCILE cannot do this.** It compares values of the same field across
    documents (`reconcile/__init__.py`), and this is a disagreement between two
    different fields. That is why the check lives in a rubric.
    """
    design = inputs.get("blinding.design")
    matching = inputs.get("blinding.placebo_matching")
    staff = inputs.get("blinding.unblinded_staff_stated")
    form = inputs.get("ip.form")

    design_value = _value_of(design)
    matching_value = _value_of(matching)
    staff_value = _value_of(staff)
    forms = _forms_of(form)

    sources = [
        provenance
        for resolved in (design, matching, staff, form)
        if resolved is not None
        for provenance in resolved.sources
    ]
    confidence = min(
        (r.confidence for r in (design, matching, staff, form) if r is not None),
        default=0.0,
    )

    def result(value: str, *used: tuple[str, ResolvedField | None]) -> RubricResult:
        return RubricResult(
            value=value,
            status="needs_review",  # GATE always sends derived fields to needs_review
            confidence=confidence,
            sources=sources,
            explanation="; ".join(_cite(label, resolved) for label, resolved in used),
        )

    # Rule 1. Open-label: whether the placebo matches does not matter.
    if design_value == "open_label":
        return result(PLACEBO_OPEN_LABEL, ("blinding.design", design))

    # Rule 6. Stated matching and stated unblinded staff cannot both be right.
    if matching_value == "matching_stated" and staff_value in _STAFF_STATED_YES:
        return result(
            PLACEBO_CONTRADICTION,
            ("blinding.placebo_matching", matching),
            ("blinding.unblinded_staff_stated", staff),
        )

    # Rule 2. The document says: use what it says.
    if matching_value == "matching_stated":
        return result(
            "the document says the placebo matches as supplied",
            ("blinding.placebo_matching", matching),
        )
    if matching_value == "not_matching_stated":
        return result(
            "the document says the placebo does not match; unblinded handling likely",
            ("blinding.placebo_matching", matching),
            ("blinding.unblinded_staff_stated", staff),
        )

    # Rules 3 and 4. The document does not say, so the route decides.
    # Injected wins a document stating both routes: the liquid is the one that
    # cannot be colour-matched, and under-pricing unblinded staff costs more than
    # pricing them needlessly.
    if any(f in _INJECTED_FORMS for f in forms):
        return result(PLACEBO_INJECTED_NOT_STATED, ("ip.form", form))
    if any(f in _ORAL_FORMS for f in forms):
        return result(PLACEBO_ORAL_NOT_STATED, ("ip.form", form))

    if not forms:
        return result(
            "matching not stated and the document does not say how the drug is given; "
            "ask the sponsor",
            ("blinding.placebo_matching", matching),
            ("ip.form", form),
        )
    return result(
        "matching not stated and the route of administration does not decide it; "
        "ask the sponsor",
        ("ip.form", form),
    )

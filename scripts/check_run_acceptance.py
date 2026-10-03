"""Did one live run meet the acceptance criteria? — one run, four answers.

Reads a run's `extraction.json` and reports the three fields the criteria name
plus the confirmed count among "the original 36" — the fields that existed
before stage 5 of docs/PLAN_2026-10-02.md, which is the set every earlier run's
count was measured against (CLAUDE.md lines 264, 272, 533).

The five names below are the exception that proves non-negotiable rule 4 rather
than breaking it: rule 4 forbids a *pipeline* decision keyed to a field name,
and nothing here feeds the pipeline. This is a diagnostic that compares today's
run with runs taken before those five fields existed, so the comparison set is
history and cannot be derived from today's `config/fields.yaml` — the registry
has no stage marker. If a sixth field is added, this list does not change: the
denominator is meant to stay at 36 so the numbers stay comparable.

Usage: python scripts/check_run_acceptance.py <run_id> [floor]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rfp_intake.domain.registry import get_registry

# Added by stage 5, so outside "the original 36". From docs/PLAN_2026-10-02.md
# stage 5 items 4, 5, 6, 7 and 9.
ADDED_BY_STAGE_5 = frozenset(
    {
        "study.primary_objective",
        "blinding.placebo_matching",
        "blinding.unblinded_staff_stated",
        "visits.schedule_present",
        "blinding.placebo_assumption",
    }
)

# The fields the acceptance criteria name individually.
WATCHED = ("blinding.placebo_matching", "blinding.unblinded_staff_stated", "study.phase")


def main(run_id: str, floor: int) -> int:
    path = Path("runs") / run_id / "extraction.json"
    data = json.loads(path.read_text())
    registry = get_registry()

    # One field can hold several rows: a scoped field resolves once per document.
    # A field counts as confirmed if any of its rows is, which is how the earlier
    # runs' counts were read off the report.
    rows: dict[str, list[dict]] = {}
    for row in data["resolved_fields"]:
        rows.setdefault(row["field_id"], []).append(row)

    original = [f for f in registry.fields if f.id not in ADDED_BY_STAGE_5]

    print(f"run: {run_id}")
    print(f"{len(registry.fields)} fields in the registry, {len(original)} of them original")
    print()

    for field_id in WATCHED:
        for row in rows.get(field_id, []):
            scope = row.get("scope") or "-"
            print(
                f"  {field_id} [{scope}]: value={row['value']!r} "
                f"status={row['status']} confidence={row['confidence']}"
            )
        if field_id not in rows:
            print(f"  {field_id}: MISSING from the report")
    print()

    confirmed = sorted(
        f.id
        for f in original
        if any(row["status"] == "confirmed" for row in rows.get(f.id, []))
    )
    print(f"confirmed among the original {len(original)}: {len(confirmed)} (floor {floor})")
    print(f"errors recorded in the run: {len(data.get('errors') or [])}")
    print(f"contradictions: {len(data.get('contradictions') or [])}")

    if len(confirmed) < floor:
        print("\nBELOW FLOOR")
        return 1
    return 0


if __name__ == "__main__":
    run = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 26
    raise SystemExit(main(run, limit))

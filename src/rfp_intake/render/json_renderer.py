"""extraction.json — the canonical, full-fidelity output. Pure function over
RunState; job/output.py is the only place that touches a filesystem.
See ARCHITECTURE.md §4.10.
"""

from __future__ import annotations

from typing import Any

from rfp_intake.domain.registry import Registry
from rfp_intake.domain.schemas import RunState


def build_extraction_document(state: RunState, registry: Registry) -> dict[str, Any]:
    """Build the canonical extraction document: every ResolvedField with its
    value, status, confidence, sources, quote, contradiction, and
    derived_from, versioned by the registry that produced it.
    """
    field_by_id = {f.id: f for f in registry.fields}

    resolved_fields = []
    for rf in state.resolved:
        field_def = field_by_id.get(rf.field_id)
        resolved_fields.append({
            "field_id": rf.field_id,
            "group": field_def.group if field_def else None,
            "label": field_def.label if field_def else rf.field_id,
            "value": rf.value,
            "status": rf.status,
            "confidence": rf.confidence,
            "scope": rf.scope,
            "sources": [p.model_dump(mode="json") for p in rf.sources],
            "quote": rf.quote,
            "derived_from": rf.derived_from,
            "notes": rf.notes,
            "contradiction": rf.contradiction.model_dump(mode="json") if rf.contradiction else None,
        })

    return {
        "run_id": state.run_id,
        "registry_version": registry.registry_version,
        "resolved_fields": resolved_fields,
        "contradictions": [c.model_dump(mode="json") for c in state.contradictions],
        "errors": [e.model_dump(mode="json") for e in state.errors],
    }

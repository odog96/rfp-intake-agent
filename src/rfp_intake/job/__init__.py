"""CML Job entry point — runs the full extraction pipeline for one RFP package."""

from __future__ import annotations

import sys
from typing import Any

import structlog

from rfp_intake.config.settings import get_settings
from rfp_intake.graph import build_graph
from rfp_intake.job.output import write_extraction_json
from rfp_intake.job.status import StatusWriter

logger = structlog.get_logger()


def main(run_id: str) -> None:
    """Execute the pipeline for a single run. Called by CML Job."""
    settings = get_settings()
    run_path = settings.run_dir / run_id
    inputs_dir = run_path / "inputs"

    if not inputs_dir.exists():
        raise SystemExit(f"No inputs directory: {inputs_dir}")

    writer = StatusWriter(run_path)
    writer.write("starting", node="INGEST")
    logger.info("job_started", run_id=run_id, inputs=str(inputs_dir))

    graph = build_graph()
    compiled = graph.compile()

    try:
        final_state: dict[str, Any] = {}
        for event in compiled.stream({"run_id": run_id}):
            node_name = next(iter(event))
            writer.write("running", node=node_name.upper())
            final_state.update(event[node_name])
            logger.info("node_completed", run_id=run_id, node=node_name)

        write_extraction_json(run_path, final_state)
        writer.write("completed", node="DONE")
        logger.info("job_completed", run_id=run_id)

    except Exception as exc:
        logger.error("job_failed", run_id=run_id, error=str(exc))
        writer.write("failed", node="ERROR", error=str(exc))
        raise SystemExit(1) from exc


def cli() -> None:
    """CLI entry point: python -m rfp_intake.job <run_id>."""
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m rfp_intake.job <run_id>")
    main(sys.argv[1])

"""CML Job entry point — runs the full extraction pipeline for one RFP package."""

from __future__ import annotations

import sys
from typing import Any, cast

import structlog

from rfp_intake.config.settings import get_settings
from rfp_intake.domain.schemas import RunState
from rfp_intake.graph import build_graph
from rfp_intake.job.output import write_reports
from rfp_intake.job.status import RunFailure, StatusWriter
from rfp_intake.llm.health import ServiceFailure, describe_failure, probe_model_service

logger = structlog.get_logger()


def main(run_id: str) -> None:
    """Execute the pipeline for a single run. Called by CML Job."""
    settings = get_settings()
    run_path = settings.run_dir / run_id
    inputs_dir = run_path / "inputs"

    if not inputs_dir.exists():
        raise SystemExit(f"No inputs directory: {inputs_dir}")

    writer = StatusWriter(run_path)
    writer.write("starting", node="PREFLIGHT")
    logger.info("job_started", run_id=run_id, inputs=str(inputs_dir))

    try:
        # Ask the model service one trivial question before reading a 137-page
        # protocol. Without this the run parses everything, fails every LLM call
        # one node at a time, and takes minutes to say what one second could:
        # the service is not answering. The probe sends no document text, so it
        # is safe in any privacy mode.
        service_failure = probe_model_service()
        if service_failure is not None:
            _fail(writer, node="PREFLIGHT", failure=_model_service_failure(service_failure))
            logger.error(
                "model_service_unavailable",
                run_id=run_id,
                kind=service_failure.kind,
                service=service_failure.service,
                detail=service_failure.detail,
            )
            raise SystemExit(1)

        writer.write("running", node="INGEST")
        graph = build_graph()
        compiled = graph.compile()

        # "values" carries the accumulated state, so RunState's operator.add
        # reducers survive; merging the "updates" chunks by hand did not, and
        # silently dropped every INGEST, CLASSIFY and EXTRACT error from
        # extraction.json. "updates" is still streamed for the node name.
        final_state: dict[str, Any] = {}
        for mode, chunk in compiled.stream(
            {"run_id": run_id}, stream_mode=["updates", "values"]
        ):
            if mode == "updates":
                node_name = next(iter(chunk))
                writer.write("running", node=node_name.upper())
                logger.info("node_completed", run_id=run_id, node=node_name)
            else:
                final_state = dict(cast("dict[str, Any]", chunk))

        final_state.pop("run_id", None)
        run_state = RunState(run_id=run_id, **final_state)
        write_reports(run_path, run_state)

        # A run that resolved nothing produced a blank report. That is a failed
        # run, not a completed one, and saying "completed" sent an analyst back
        # to their documents looking for a fault that was never there.
        failure = _empty_run_failure(run_state)
        if failure is None:
            writer.write("completed", node="DONE")
            logger.info("job_completed", run_id=run_id)
        else:
            _fail(writer, node="DONE", failure=failure)
            logger.error("job_produced_nothing", run_id=run_id, error=failure.reason)
            # Exit non-zero so the CML Jobs API and status.json agree. The
            # Cloudera AI Application treats the CML Jobs API as authoritative
            # for whether the process succeeded (ARCHITECTURE.md §6.3).
            raise SystemExit(1)

    except Exception as exc:
        logger.error("job_failed", run_id=run_id, error=str(exc))
        _fail(
            writer,
            node="ERROR",
            failure=RunFailure(
                kind="unexpected",
                reason="The review stopped before it could finish.",
                action=(
                    "No report was produced. Press try again, and send the run "
                    "identifier to your platform contact if it stops a second time."
                ),
                detail=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise SystemExit(1) from exc


def _fail(writer: StatusWriter, *, node: str, failure: RunFailure) -> None:
    """Write one failed status. `error` keeps the single string it always held,
    so anything reading status.json the old way is unaffected."""
    writer.write(
        "failed",
        node=node,
        error=f"{failure.reason} {failure.action}"
        + (f" ({failure.detail})" if failure.detail else ""),
        failure=failure,
    )


def _model_service_failure(probe: ServiceFailure) -> RunFailure:
    """The probe's two sentences, in the shape status.json carries."""
    return RunFailure(
        kind="model_service",
        reason=probe.reason,
        action=probe.action,
        service=probe.service,
        detail=probe.detail,
    )


# Which model role each node calls, so a failure that surfaced mid-run can name
# the service it was talking to rather than "the model service".
_NODE_ROLES = {"CLASSIFY": "classify", "EXTRACT": "extract", "ADJUDICATE": "adjudicate"}


def _empty_run_failure(state: RunState) -> RunFailure | None:
    """Return why this run produced nothing usable, or None if it did.

    Every node catches its own exceptions and records a RunError, so a run in
    which every LLM call failed still reaches DONE and writes a report full of
    `not_found`. Only the caller can tell that apart from a genuinely silent
    set of documents: not one FieldRecord came off any page AND something
    errored. `state.resolved` cannot be the test — DERIVE computes a field
    from an empty input, so a run that extracted nothing still ends with one
    resolved field that has a value.
    """
    if state.records:
        return None
    failures = [e for e in state.errors if e.kind == "call_failed"]
    if not failures:
        return None

    first = failures[0]
    detail = (
        f"{len(failures)} step(s) failed to get an answer from the model. "
        f"First failure ({first.node}): {first.error}"
    )

    # The preflight probe passed or this run would not have got here, so a
    # service failure now means the credential lapsed or the service went down
    # part-way through — the long-protocol case the probe cannot catch. Reuse
    # the probe's own wording so the page reads identically either way.
    described = describe_failure(
        first.error, provider=_provider_for_node(first.node), role=_NODE_ROLES.get(first.node)
    )
    if described.kind != "unknown":
        return RunFailure(
            kind="model_service",
            reason=described.reason,
            action=described.action,
            service=described.service,
            detail=detail,
        )

    return RunFailure(
        kind="no_extraction",
        reason="Nothing could be extracted from the documents.",
        action=(
            "No report worth reading was produced. Send the run identifier below "
            "to your platform contact."
        ),
        detail=detail,
    )


def _provider_for_node(node: str) -> str:
    """The provider the given node's role is bound to, or a neutral phrase.

    Config that will not load must not turn a diagnosis into a crash, so every
    failure here falls back to wording that names no provider at all.
    """
    role = _NODE_ROLES.get(node)
    if role is None:
        return "the model service"
    try:
        from rfp_intake.domain.model_routing import get_model_routing

        binding = get_model_routing().roles.get(role)
    except Exception:  # noqa: BLE001 - naming the service is a nicety, never a failure
        return "the model service"
    return binding.provider if binding else "the model service"


def cli() -> None:
    """CLI entry point: python -m rfp_intake.job <run_id>."""
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m rfp_intake.job <run_id>")
    main(sys.argv[1])

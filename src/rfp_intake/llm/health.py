"""Is the model service actually answering? — one plain answer, any provider.

Every LLM call in this pipeline is made inside a node that catches its own
exceptions and records a RunError, which is right: one failed extraction group
must not lose the other eight. The cost is that a run in which *every* call
fails still walks the whole graph and writes a report full of `not_found`, and
runs r-20260831-150720 .. r-20260901-140857 did exactly that after the AWS
Bedrock bearer token expired. Nothing on the analyst's screen said so.

This module is the missing witness. It answers one question — can the configured
model service be reached and will it answer? — and phrases the answer for two
different readers: `reason` and `action` for the analyst on the page, `detail`
for whoever has to fix it.

Nothing here is provider-specific by design. Cloudera AI Inference returns a 401
where AWS Bedrock returns an AccessDeniedException, and an analyst should not
have to know which one the run happened to be pointed at.
"""

from __future__ import annotations

from typing import Literal

import structlog
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from rfp_intake.config.settings import get_settings
from rfp_intake.domain.model_routing import get_model_routing
from rfp_intake.llm.credentials import bedrock_credential_report
from rfp_intake.llm.provider import LLMRole, get_llm

logger = structlog.get_logger()

# Every role config/models.yaml binds. Probing all of them catches a routing where
# only one role points at a service whose credential has lapsed. `other_study_check`
# was added by stage 4 of docs/PLAN_2026-10-02.md; a role left out here is a role
# whose first failure happens deep into a paid run instead of at PREFLIGHT.
PROBE_ROLES: tuple[LLMRole, ...] = ("classify", "other_study_check", "extract", "adjudicate")

# What the probe sends. Short on purpose: this is a reachability check, and it
# carries no document text, so it is safe to send to any configured provider
# regardless of privacy_mode.
PROBE_PROMPT = "Reply with the single word: ok"

# The words for a provider, for someone who has never read models.yaml.
_SERVICE_NAMES = {
    "bedrock": "AWS Bedrock",
    "caii": "Cloudera AI Inference",
    "litellm": "the local model proxy",
    "mock": "the built-in test model",
}

FailureKind = Literal[
    "credential_expired",
    "credential_rejected",
    "model_unavailable",
    "unreachable",
    "rate_limited",
    "misconfigured",
    "unknown",
]

# Matched against the lowercased error text, first hit wins, so the more specific
# phrases come first. Both providers' vocabularies are in one table deliberately:
# the same analyst-facing sentence has to come out either way.
#
# AWS wording is from botocore; the OpenAI-compatible wording is from the openai
# and httpx clients CAII and the LiteLLM proxy are reached through.
_SIGNATURES: tuple[tuple[str, FailureKind], ...] = (
    ("bearer token has expired", "credential_expired"),
    ("expiredtoken", "credential_expired"),
    ("security token included in the request is expired", "credential_expired"),
    ("token is expired", "credential_expired"),
    ("signature expired", "credential_expired"),
    ("unrecognizedclient", "credential_rejected"),
    ("invalidsignature", "credential_rejected"),
    ("unable to locate credentials", "credential_rejected"),
    ("nocredentialserror", "credential_rejected"),
    ("accessdenied", "credential_rejected"),
    ("authenticationerror", "credential_rejected"),
    ("permissiondenied", "credential_rejected"),
    ("incorrect api key", "credential_rejected"),
    ("invalid api key", "credential_rejected"),
    ("401", "credential_rejected"),
    ("403", "credential_rejected"),
    ("resourcenotfound", "model_unavailable"),
    ("validationexception", "model_unavailable"),
    ("model not found", "model_unavailable"),
    ("does not exist", "model_unavailable"),
    ("notfounderror", "model_unavailable"),
    ("404", "model_unavailable"),
    ("throttl", "rate_limited"),
    ("too many requests", "rate_limited"),
    ("ratelimit", "rate_limited"),
    ("429", "rate_limited"),
    ("timeout", "unreachable"),
    ("timed out", "unreachable"),
    ("connection", "unreachable"),
    ("could not resolve", "unreachable"),
    ("name or service not known", "unreachable"),
    ("endpointconnectionerror", "unreachable"),
    ("providerunavailable", "misconfigured"),
    ("credentialerror", "misconfigured"),
    ("unknown provider", "misconfigured"),
    ("pip install", "misconfigured"),
)

# One sentence saying what happened, and one saying who has to do what about it.
# `{service}` is filled in from _SERVICE_NAMES. These are read by an analyst who
# did nothing wrong, so none of them start by describing the software.
_WORDING: dict[FailureKind, tuple[str, str]] = {
    "credential_expired": (
        "The access key for {service} has expired, so no question could be asked "
        "of the model.",
        "This is not a problem with your documents. An administrator needs to "
        "renew the {service} credential, then the review can be run again.",
    ),
    "credential_rejected": (
        "{service} refused the access key this project is using.",
        "This is not a problem with your documents. An administrator needs to "
        "check the {service} credential, then the review can be run again.",
    ),
    "model_unavailable": (
        "{service} does not offer the model this project is configured to use.",
        "This is not a problem with your documents. An administrator needs to "
        "correct the model name in config/models.yaml.",
    ),
    "unreachable": (
        "{service} could not be reached.",
        "This is not a problem with your documents. The service may be down or "
        "blocked by the network — an administrator needs to check it.",
    ),
    "rate_limited": (
        "{service} is refusing requests because too many have been sent.",
        "Wait a few minutes and press try again. If it keeps happening, tell "
        "your platform contact.",
    ),
    "misconfigured": (
        "This project is not correctly set up to talk to {service}.",
        "This is not a problem with your documents. An administrator needs to "
        "check config/models.yaml and the deployment.",
    ),
    "unknown": (
        "{service} did not answer.",
        "This is not a problem with your documents. Send the run identifier "
        "below to your platform contact.",
    ),
}


class ServiceFailure(BaseModel):
    """Why the model service could not be used, for both readers.

    `reason` and `action` are the two sentences the Cloudera AI Application
    shows. `detail` is the provider's own words, which belong in the operator's
    expander and nowhere else.
    """

    kind: FailureKind
    service: str
    role: str | None = None
    model: str | None = None
    reason: str
    action: str
    detail: str

    @property
    def summary(self) -> str:
        """One line for status.json's `error` and for the log."""
        return f"{self.reason} {self.action} ({self.detail})"


def service_name(provider: str) -> str:
    """The words for a provider id, falling back to the id itself."""
    return _SERVICE_NAMES.get(provider, provider)


def classify_failure(error: BaseException | str) -> FailureKind:
    """Which kind of model-service failure this error text describes.

    Text matching, not exception types, because the two provider stacks raise
    entirely different classes — botocore's ClientError carries the reason in a
    string either way, and langchain wraps several of them.
    """
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error
    lowered = text.lower()
    for needle, kind in _SIGNATURES:
        if needle in lowered:
            return kind
    return "unknown"


def describe_failure(
    error: BaseException | str,
    *,
    provider: str,
    role: str | None = None,
    model: str | None = None,
) -> ServiceFailure:
    """Turn one provider error into the two sentences an analyst can act on."""
    kind = classify_failure(error)
    name = service_name(provider)
    reason, action = _WORDING[kind]
    detail = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    return ServiceFailure(
        kind=kind,
        service=name,
        role=role,
        model=model,
        reason=reason.format(service=name),
        action=action.format(service=name),
        detail=detail,
    )


def probe_model_service(roles: tuple[LLMRole, ...] = PROBE_ROLES) -> ServiceFailure | None:
    """Ask each configured model service one trivial question.

    Returns None when every distinct binding answered, or the first failure
    otherwise. Never raises: a probe that blows up in an unforeseen way is
    itself the answer, and must not take down the caller.

    Distinct bindings are probed once each, so the usual case — all three roles
    on one provider and model — costs one short call.
    """
    settings = get_settings()
    if settings.llm_backend == "mock":
        # The offline path has no service to reach. Saying "healthy" here is
        # what lets the test suite run the job end to end without a network.
        return None

    try:
        routing = get_model_routing()
    except Exception as exc:  # noqa: BLE001 - a routing that will not load is a failure
        return describe_failure(exc, provider="the configured model service")

    seen: set[tuple[str, str]] = set()
    for role in roles:
        binding = routing.roles.get(role)
        if binding is None:
            continue
        if (binding.provider, binding.model) in seen:
            continue
        seen.add((binding.provider, binding.model))

        _report_credential(binding.provider)
        failure = _probe_one(role, binding.provider, binding.model)
        if failure is not None:
            return failure

    return None


def _report_credential(provider: str) -> None:
    """Say which kind of AWS credential a Bedrock probe is about to use.

    PREFLIGHT's job is to make a credential problem visible before a paid run, and
    which *kind* of credential was used is the one piece it could not say. Runs
    r-20260831-150720 .. r-20260901-140857 failed on an expired bearer token, and
    a bearer token silently takes precedence over standard access keys, so a stale
    one makes correct access keys look rejected. See `llm/credentials.py`.

    Logged, not raised: an unexpected environment is not a reason to stop a run
    that may be about to work. Variable names only — never a value.
    """
    if provider != "bedrock":
        return

    report = bedrock_credential_report()
    log = logger.warning if (report.shadowed or report.kind == "ambient") else logger.info
    log(
        "model_service_credential",
        provider=provider,
        credential_kind=report.kind,
        shadowed=report.shadowed,
        env_vars_set=report.present,
        summary=report.summary,
    )


def _probe_one(role: LLMRole, provider: str, model: str) -> ServiceFailure | None:
    """One short call to one binding. The failure, or None if it answered."""
    try:
        get_llm(role).invoke([HumanMessage(content=PROBE_PROMPT)])
    except Exception as exc:  # noqa: BLE001 - every failure shape is a failed probe
        failure = describe_failure(exc, provider=provider, role=role, model=model)
        if provider == "bedrock":
            # Which credential kind was refused belongs with the provider's own
            # words, in the operator's detail — not in the analyst's two sentences.
            failure = failure.model_copy(
                update={
                    "detail": f"{failure.detail} [{bedrock_credential_report().summary}]"
                }
            )
        logger.warning(
            "model_service_probe_failed",
            role=role,
            provider=provider,
            model=model,
            kind=failure.kind,
            detail=failure.detail,
        )
        return failure

    logger.info("model_service_probe_ok", role=role, provider=provider, model=model)
    return None

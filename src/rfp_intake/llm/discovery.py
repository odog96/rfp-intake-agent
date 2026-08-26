"""Model discovery — ask a provider what it actually serves.

Exists so the admin surface can offer a real list instead of a free-text box,
and so a misconfigured `model:` in models.yaml is caught before a run rather
than as an opaque 404 mid-extraction.

This is the scaffold for the "model setup job" in the eventual design: a
scheduled job would call discover_models() per provider and cache the result for
the UI. Nothing here is on the pipeline's critical path.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from rfp_intake.config.settings import Settings, get_settings
from rfp_intake.domain.model_routing import ModelRouting, get_model_routing
from rfp_intake.llm.provider import resolve_api_key

DISCOVERY_TIMEOUT_S = 30.0


class DiscoveryError(Exception):
    """A provider could not be queried for its served models."""


class DiscoveredModel(BaseModel):
    id: str
    provider: str


def discover_models(provider: str, settings: Settings | None = None) -> list[DiscoveredModel]:
    """List the model ids a provider currently serves."""
    settings = settings or get_settings()

    if provider == "mock":
        return [DiscoveredModel(id="mock", provider="mock")]

    if provider in ("caii", "litellm"):
        base_url = settings.caii_base_url if provider == "caii" else settings.litellm_base_url
        return _discover_openai_compatible(provider, base_url, settings)

    if provider == "bedrock":
        # Scaffold: needs bedrock:ListFoundationModels via boto3, which is a
        # different call shape from the OpenAI /models convention. Deferred
        # until a Bedrock account is actually wired up — guessing the response
        # shape now would just be untested code.
        raise DiscoveryError(
            "Discovery for 'bedrock' is not implemented yet. List models with "
            "`aws bedrock list-foundation-models` and set models.yaml by hand."
        )

    raise DiscoveryError(f"Unknown provider: {provider}")


def _discover_openai_compatible(
    provider: str, base_url: str, settings: Settings
) -> list[DiscoveredModel]:
    url = f"{base_url.rstrip('/')}/models"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {resolve_api_key(provider, settings)}"},  # type: ignore[arg-type]
            timeout=DISCOVERY_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - every failure shape is a discovery failure
        raise DiscoveryError(f"Could not query {url}: {type(exc).__name__}: {exc}") from exc

    return [
        DiscoveredModel(id=entry["id"], provider=provider)
        for entry in payload.get("data", [])
        if entry.get("id")
    ]


def describe_active_routing(routing: ModelRouting | None = None) -> dict[str, object]:
    """Summarise what each role is bound to, for display and for audit.json.

    `external_services` matches ARCHITECTURE.md §6.4, where an empty array is
    the evidence that no document content left the environment.
    """
    routing = routing or get_model_routing()
    return {
        "privacy_mode": routing.privacy_mode,
        "roles": {
            role: {
                "provider": binding.provider,
                "model": binding.model,
                "egress": routing.provider_for(role).egress,
            }
            for role, binding in routing.roles.items()
        },
        "external_services": routing.external_services,
    }


def validate_bindings_against_provider(
    routing: ModelRouting | None = None, settings: Settings | None = None
) -> dict[str, str]:
    """Check each role's configured model id is actually served.

    Returns {role: message} for problems only — an empty dict means every
    binding resolves. Providers that cannot be queried are reported, not raised,
    so one unreachable provider does not hide problems with the others.
    """
    routing = routing or get_model_routing()
    problems: dict[str, str] = {}
    cache: dict[str, list[str] | str] = {}

    for role, binding in routing.roles.items():
        if binding.provider not in cache:
            try:
                cache[binding.provider] = [
                    m.id for m in discover_models(binding.provider, settings)
                ]
            except DiscoveryError as exc:
                cache[binding.provider] = str(exc)
        served = cache[binding.provider]
        if isinstance(served, str):
            problems[role] = f"could not verify: {served}"
        elif binding.model not in served:
            problems[role] = (
                f"model '{binding.model}' not served by "
                f"'{binding.provider}' (has: {served})"
            )

    return problems

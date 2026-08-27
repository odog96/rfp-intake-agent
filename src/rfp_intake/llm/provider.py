"""LLM provider seam — get_llm(role) dispatches to the configured backend."""

from __future__ import annotations

from typing import Literal

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from rfp_intake.config.settings import Settings, get_settings
from rfp_intake.domain.model_routing import (
    ModelRouting,
    RoleBinding,
    check_role_allowed,
    get_model_routing,
)

logger = structlog.get_logger()

LLMRole = Literal["classify", "extract", "adjudicate"]
LLMBackend = Literal["caii", "litellm"]

# Providers reachable over an OpenAI-compatible HTTP API — construction differs
# only by base URL and credential.
_OPENAI_COMPATIBLE = ("caii", "litellm")

# The OpenAI client requires a non-empty api_key even against an endpoint that
# does not authenticate. This placeholder keeps an unsecured local proxy working
# without pretending a credential was supplied.
_UNAUTHENTICATED_PLACEHOLDER = "not-needed"


class CredentialError(Exception):
    """A configured credential source could not be read."""


class ProviderUnavailableError(Exception):
    """A configured provider's dependencies are not installed."""


def get_llm(role: LLMRole) -> BaseChatModel:
    """Return an LLM instance for the given role, per config/models.yaml.

    `RFP_INTAKE_LLM_BACKEND=mock` short-circuits routing entirely — it is the
    offline/test escape hatch the suite relies on. Otherwise the per-role binding
    in models.yaml decides both provider and model, superseding the flat
    model_* settings.
    """
    settings = get_settings()
    if settings.llm_backend == "mock":
        from rfp_intake.llm.mock import MockChatModel

        return MockChatModel(role=role)

    routing = get_model_routing()
    # Re-check the privacy invariant here, not only at load: a routing built or
    # mutated in memory must not be able to reach an external service in
    # private mode just because it skipped the loader.
    binding = check_role_allowed(routing, role)

    provider = routing.provider_for(role)
    if provider.is_external:
        logger.warning(
            "llm_external_provider_selected",
            role=role,
            provider=binding.provider,
            model=binding.model,
            privacy_mode=routing.privacy_mode,
            detail="Document excerpts will leave the customer boundary.",
        )

    return build_chat_model(binding, routing, settings)


def build_chat_model(
    binding: RoleBinding,
    routing: ModelRouting,
    settings: Settings | None = None,
) -> BaseChatModel:
    """Construct the chat model for one already-privacy-checked binding."""
    settings = settings or get_settings()
    name = binding.provider

    if name == "mock":
        from rfp_intake.llm.mock import MockChatModel

        return MockChatModel(role=binding.role)

    if name in _OPENAI_COMPATIBLE:
        from langchain_openai import ChatOpenAI

        # models.yaml wins when it names an endpoint; Settings is the fallback so
        # existing RFP_INTAKE_*_BASE_URL deployments keep working unchanged.
        default_url = settings.caii_base_url if name == "caii" else settings.litellm_base_url
        base_url = routing.providers[name].base_url or default_url
        return ChatOpenAI(
            base_url=base_url,
            model=binding.model,
            api_key=resolve_api_key(name, settings),  # type: ignore[arg-type]
            timeout=settings.llm_timeout_s,
            max_tokens=settings.llm_max_tokens,
        )

    if name == "bedrock":
        return _build_bedrock(binding, settings)

    raise ValueError(f"Unknown provider: {name}")


def _build_bedrock(binding: RoleBinding, settings: Settings) -> BaseChatModel:
    """Bedrock lives behind an optional import so boto3/langchain-aws stay off the
    critical path for private-mode deployments that will never call it."""
    try:
        from langchain_aws import ChatBedrockConverse  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ProviderUnavailableError(
            "Provider 'bedrock' requires the aws extra: "
            "pip install 'rfp-intake[aws]'"
        ) from exc

    # Credentials come from the standard AWS chain (env, profile, instance role).
    # Deliberately not re-plumbed through Settings — one credential source.
    model: BaseChatModel = ChatBedrockConverse(
        model=binding.model, region_name=settings.bedrock_region
    )
    return model


def resolve_api_key(backend: LLMBackend, settings: Settings | None = None) -> str:
    """Resolve the credential for a backend.

    For CAII, `caii_api_key_file` wins over `caii_api_key` when set, so a token
    rotated on disk is picked up without restarting the process. Falls back to a
    placeholder when nothing is configured, which is correct for an unsecured
    local proxy and wrong everywhere else — hence the warning.
    """
    settings = settings or get_settings()

    if backend == "caii" and settings.caii_api_key_file is not None:
        path = settings.caii_api_key_file
        try:
            token = path.read_text().strip()
        except OSError as exc:
            raise CredentialError(f"Cannot read caii_api_key_file at {path}: {exc}") from exc
        if not token:
            raise CredentialError(f"caii_api_key_file at {path} is empty")
        return token

    key = settings.caii_api_key if backend == "caii" else settings.litellm_api_key
    if key:
        return key

    logger.warning(
        "llm_credential_missing",
        backend=backend,
        detail="No API key configured; sending a placeholder. Expect 401 from a secured endpoint.",
    )
    return _UNAUTHENTICATED_PLACEHOLDER

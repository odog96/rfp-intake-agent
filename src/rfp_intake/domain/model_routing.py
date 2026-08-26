"""Load and validate config/models.yaml into typed Pydantic models.

Mirrors domain/precedence.py's role: this is the loader plus the privacy
invariant, not the provider construction. Building the actual chat model from a
binding lives in llm/provider.py, because vendor SDKs may only be imported
under llm/ (CLAUDE.md rule 5).

The privacy invariant is enforced here at load time and re-checked in
llm/provider.py at construction time. That duplication is deliberate: a routing
mutated in memory after load, or a provider constructed by a future code path
that skips the loader, must still not reach an external service in private mode.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from rfp_intake.config.settings import get_settings

PrivacyMode = Literal["private", "mixed", "open"]

# How far document text travels when a provider is used.
#   none          stays inside the customer boundary
#   unverifiable  destination cannot be determined from here — treated as external
#   external      definitionally outside the boundary
Egress = Literal["none", "unverifiable", "external"]

LLM_ROLES = ("classify", "extract", "adjudicate")


class PrivacyViolationError(Exception):
    """A routing would send document content somewhere the privacy mode forbids."""


class ProviderSpec(BaseModel):
    name: str
    egress: Egress
    description: str = ""

    @property
    def is_external(self) -> bool:
        """Anything not provably on-box counts as external. `unverifiable`
        resolves to True on purpose — see the litellm note in models.yaml."""
        return self.egress != "none"


class RoleBinding(BaseModel):
    role: str
    provider: str
    model: str
    # Required in `mixed` mode for any role bound to an external provider. Has no
    # effect in `private` (never permitted) or `open` (always permitted).
    allow_external: bool = False
    # Pin the structured-output strategy for this role. None = auto-detect.
    # Set it when auto-detection guesses wrong for a served model — tool-call
    # adherence varies by model, not just by endpoint type.
    strategy: Literal["native", "guided"] | None = None


class ModelRouting(BaseModel):
    privacy_mode: PrivacyMode
    providers: dict[str, ProviderSpec]
    roles: dict[str, RoleBinding] = Field(default_factory=dict)

    def binding(self, role: str) -> RoleBinding:
        if role not in self.roles:
            raise KeyError(f"No model binding configured for role: {role}")
        return self.roles[role]

    def provider_for(self, role: str) -> ProviderSpec:
        binding = self.binding(role)
        if binding.provider not in self.providers:
            raise KeyError(
                f"Role '{role}' is bound to unknown provider '{binding.provider}'. "
                f"Known providers: {sorted(self.providers)}"
            )
        return self.providers[binding.provider]

    @property
    def external_services(self) -> list[str]:
        """Providers this routing would actually reach that sit outside the
        boundary. Feeds audit.json.external_services (ARCHITECTURE.md §6.4),
        where an empty list is the evidence that nothing left."""
        names = {
            b.provider for b in self.roles.values() if self.providers[b.provider].is_external
        }
        return sorted(names)


def check_role_allowed(routing: ModelRouting, role: str) -> RoleBinding:
    """Assert one role's binding satisfies the privacy mode. Raises PrivacyViolationError.

    Returns the binding so callers can use this as a checked accessor rather
    than remembering to validate separately.
    """
    binding = routing.binding(role)
    provider = routing.provider_for(role)

    if not provider.is_external:
        return binding

    if routing.privacy_mode == "private":
        raise PrivacyViolationError(
            f"privacy_mode=private forbids role '{role}' using provider "
            f"'{binding.provider}' (egress: {provider.egress}). Document excerpts "
            f"would leave the customer boundary. Bind this role to an on-box "
            f"provider, or change privacy_mode deliberately."
        )

    if routing.privacy_mode == "mixed" and not binding.allow_external:
        raise PrivacyViolationError(
            f"privacy_mode=mixed requires an explicit opt-in: role '{role}' is bound "
            f"to external provider '{binding.provider}' but does not set "
            f"allow_external: true. Refusing to infer consent for document egress."
        )

    return binding


def validate_routing(routing: ModelRouting) -> ModelRouting:
    """Validate every configured role. Raises on the first violation."""
    for role in routing.roles:
        check_role_allowed(routing, role)
    return routing


def load_model_routing(path: Path | None = None) -> ModelRouting:
    """Load models.yaml into a validated ModelRouting.

    Falls back to a routing synthesised from Settings when the file is absent, so
    a deployment that predates this config keeps working rather than failing to
    start.
    """
    if path is None:
        path = Path(get_settings().models_yaml_path)

    if not path.exists():
        return _routing_from_settings()

    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}

    providers = {
        name: ProviderSpec(name=name, **spec)
        for name, spec in (data.get("providers") or {}).items()
    }
    roles = {
        role: RoleBinding(role=role, **binding)
        for role, binding in (data.get("roles") or {}).items()
    }
    routing = ModelRouting(
        privacy_mode=data.get("privacy_mode", "private"),
        providers=providers,
        roles=roles,
    )
    return validate_routing(routing)


def _routing_from_settings() -> ModelRouting:
    """Legacy path: derive routing from the flat RFP_INTAKE_* settings.

    Used when config/models.yaml is absent. Marks the backend on-box only when it
    genuinely is, so the privacy invariant still holds for legacy deployments.
    """
    settings = get_settings()
    backend = settings.llm_backend
    egress: Egress = "none" if backend in ("caii", "mock") else "unverifiable"

    per_role = {
        "classify": settings.model_classify,
        "extract": settings.model_extract,
        "adjudicate": settings.model_adjudicate,
    }
    return ModelRouting(
        privacy_mode="open",  # legacy config carried no privacy contract to enforce
        providers={backend: ProviderSpec(name=backend, egress=egress, description="from Settings")},
        roles={
            role: RoleBinding(role=role, provider=backend, model=model)
            for role, model in per_role.items()
        },
    )


@lru_cache(maxsize=1)
def get_model_routing() -> ModelRouting:
    """Singleton access to the loaded routing."""
    return load_model_routing()


def reset_model_routing() -> None:
    """For testing and for admin edits that rewrite models.yaml at runtime."""
    get_model_routing.cache_clear()

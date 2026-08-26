"""Tests for domain/model_routing.py — the privacy invariant above all.

These matter more than most: the failure they guard against is silent. A routing
that wrongly permits an external provider does not crash, it just sends clinical
document excerpts to a third party and returns a perfectly good answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rfp_intake.domain.model_routing import (
    ModelRouting,
    PrivacyViolationError,
    ProviderSpec,
    RoleBinding,
    check_role_allowed,
    load_model_routing,
    validate_routing,
)

ONBOX = ProviderSpec(name="caii", egress="none")
EXTERNAL = ProviderSpec(name="bedrock", egress="external")
UNVERIFIABLE = ProviderSpec(name="litellm", egress="unverifiable")


def _routing(mode: str, provider: ProviderSpec, *, allow_external: bool = False) -> ModelRouting:
    return ModelRouting(
        privacy_mode=mode,  # type: ignore[arg-type]
        providers={provider.name: provider, "caii": ONBOX},
        roles={
            "extract": RoleBinding(
                role="extract", provider=provider.name, model="m", allow_external=allow_external
            )
        },
    )


class TestPrivateMode:
    def test_allows_onbox_provider(self) -> None:
        assert check_role_allowed(_routing("private", ONBOX), "extract").provider == "caii"

    def test_rejects_external_provider(self) -> None:
        with pytest.raises(PrivacyViolationError, match="privacy_mode=private"):
            check_role_allowed(_routing("private", EXTERNAL), "extract")

    def test_rejects_external_even_with_allow_external(self) -> None:
        """allow_external is a mixed-mode opt-in; it must not unlock private mode."""
        with pytest.raises(PrivacyViolationError):
            check_role_allowed(_routing("private", EXTERNAL, allow_external=True), "extract")

    def test_rejects_unverifiable_provider(self) -> None:
        """A dev proxy can route anywhere, so it cannot be assumed on-box."""
        with pytest.raises(PrivacyViolationError):
            check_role_allowed(_routing("private", UNVERIFIABLE), "extract")


class TestMixedMode:
    def test_rejects_external_without_optin(self) -> None:
        with pytest.raises(PrivacyViolationError, match="allow_external"):
            check_role_allowed(_routing("mixed", EXTERNAL), "extract")

    def test_allows_external_with_optin(self) -> None:
        binding = check_role_allowed(_routing("mixed", EXTERNAL, allow_external=True), "extract")
        assert binding.provider == "bedrock"

    def test_onbox_needs_no_optin(self) -> None:
        assert check_role_allowed(_routing("mixed", ONBOX), "extract").provider == "caii"


class TestOpenMode:
    def test_allows_external_without_optin(self) -> None:
        assert check_role_allowed(_routing("open", EXTERNAL), "extract").provider == "bedrock"


class TestExternalServices:
    def test_empty_when_all_onbox(self) -> None:
        assert _routing("private", ONBOX).external_services == []

    def test_lists_external_provider(self) -> None:
        routing = _routing("open", EXTERNAL)
        assert routing.external_services == ["bedrock"]

    def test_counts_unverifiable_as_external(self) -> None:
        assert _routing("open", UNVERIFIABLE).external_services == ["litellm"]


class TestValidateRouting:
    def test_raises_on_first_offending_role(self) -> None:
        routing = ModelRouting(
            privacy_mode="private",
            providers={"caii": ONBOX, "bedrock": EXTERNAL},
            roles={
                "classify": RoleBinding(role="classify", provider="caii", model="m"),
                "extract": RoleBinding(role="extract", provider="bedrock", model="m"),
            },
        )
        with pytest.raises(PrivacyViolationError, match="extract"):
            validate_routing(routing)

    def test_unknown_provider_is_an_error(self) -> None:
        routing = ModelRouting(
            privacy_mode="open",
            providers={"caii": ONBOX},
            roles={"extract": RoleBinding(role="extract", provider="typo", model="m")},
        )
        with pytest.raises(KeyError, match="unknown provider"):
            validate_routing(routing)


class TestLoadModelRouting:
    def test_shipped_config_loads_and_validates(self) -> None:
        """The shipped models.yaml must always satisfy its own privacy invariant.

        Deliberately does not pin privacy_mode — that is an operational choice
        that changes with which endpoints are live. What must never change is
        that every externally-bound role carries recorded consent.
        """
        routing = load_model_routing(Path("config/models.yaml"))
        assert set(routing.roles) == {"classify", "extract", "adjudicate"}
        validate_routing(routing)

        for role, binding in routing.roles.items():
            if routing.providers[binding.provider].is_external:
                assert binding.allow_external, (
                    f"role '{role}' is bound to external provider "
                    f"'{binding.provider}' without allow_external"
                )
                assert routing.privacy_mode != "private"

    def test_load_enforces_privacy(self, tmp_path: Path) -> None:
        """A hand-edited models.yaml must fail at load, not at first LLM call."""
        path = tmp_path / "models.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "privacy_mode": "private",
                    "providers": {"bedrock": {"egress": "external"}},
                    "roles": {"extract": {"provider": "bedrock", "model": "m"}},
                }
            )
        )
        with pytest.raises(PrivacyViolationError):
            load_model_routing(path)

    def test_missing_file_falls_back_to_settings(self, tmp_path: Path) -> None:
        routing = load_model_routing(tmp_path / "absent.yaml")
        assert set(routing.roles) == {"classify", "extract", "adjudicate"}
        assert routing.roles["extract"].provider == "mock"

"""Tests for llm/provider.py — backend switching and role mapping."""

from __future__ import annotations

import pytest

from rfp_intake.llm.mock import MockChatModel
from rfp_intake.llm.provider import get_llm


def test_get_llm_mock_backend() -> None:
    llm = get_llm("extract")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "extract"


def test_get_llm_classify_role() -> None:
    llm = get_llm("classify")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "classify"


def test_get_llm_adjudicate_role() -> None:
    llm = get_llm("adjudicate")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "adjudicate"


def test_mock_returns_deterministic_output() -> None:
    from langchain_core.messages import HumanMessage

    llm = get_llm("extract")
    msg = [HumanMessage(content="Extract visits from this document")]
    r1 = llm.invoke(msg)
    r2 = llm.invoke(msg)
    assert r1.content == r2.content


class TestResolveApiKey:
    """Credential resolution — the piece that decides whether a live endpoint
    answers or 401s. Settings are passed explicitly to keep these off the
    process environment and the settings cache."""

    def test_uses_configured_caii_key(self) -> None:
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import resolve_api_key

        settings = Settings(caii_api_key="token-abc")
        assert resolve_api_key("caii", settings) == "token-abc"

    def test_uses_configured_litellm_key(self) -> None:
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import resolve_api_key

        settings = Settings(litellm_api_key="sk-proxy")
        assert resolve_api_key("litellm", settings) == "sk-proxy"

    def test_backends_do_not_share_credentials(self) -> None:
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import resolve_api_key

        settings = Settings(caii_api_key="caii-token")
        assert resolve_api_key("litellm", settings) != "caii-token"

    def test_key_file_wins_over_key(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A rotated token on disk must beat a stale env var."""
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import resolve_api_key

        token_file = tmp_path / "jwt"
        token_file.write_text("  fresh-jwt\n")
        settings = Settings(caii_api_key="stale", caii_api_key_file=token_file)
        assert resolve_api_key("caii", settings) == "fresh-jwt"

    def test_missing_key_file_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import CredentialError, resolve_api_key

        settings = Settings(caii_api_key_file=tmp_path / "absent")
        with pytest.raises(CredentialError, match="Cannot read"):
            resolve_api_key("caii", settings)

    def test_empty_key_file_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """An empty token file is a silent-401 trap — fail loudly instead."""
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import CredentialError, resolve_api_key

        token_file = tmp_path / "jwt"
        token_file.write_text("   \n")
        settings = Settings(caii_api_key_file=token_file)
        with pytest.raises(CredentialError, match="empty"):
            resolve_api_key("caii", settings)

    def test_falls_back_to_placeholder_for_unsecured_endpoint(self) -> None:
        from rfp_intake.config.settings import Settings
        from rfp_intake.llm.provider import resolve_api_key

        assert resolve_api_key("litellm", Settings()) == "not-needed"


class TestGetLlmHonoursPrivacy:
    """get_llm re-checks the privacy invariant rather than trusting that the
    loader already did — a routing can reach it without passing through load."""

    def _external_routing(self, mode: str):  # type: ignore[no-untyped-def]
        from rfp_intake.domain.model_routing import ModelRouting, ProviderSpec, RoleBinding

        return ModelRouting(
            privacy_mode=mode,  # type: ignore[arg-type]
            providers={"bedrock": ProviderSpec(name="bedrock", egress="external")},
            roles={"extract": RoleBinding(role="extract", provider="bedrock", model="m")},
        )

    def test_private_mode_blocks_external_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake.config.settings import reset_settings
        from rfp_intake.domain.model_routing import PrivacyViolationError
        from rfp_intake.llm import provider as provider_mod

        monkeypatch.setenv("RFP_INTAKE_LLM_BACKEND", "caii")
        reset_settings()
        monkeypatch.setattr(
            provider_mod, "get_model_routing", lambda: self._external_routing("private")
        )
        with pytest.raises(PrivacyViolationError):
            provider_mod.get_llm("extract")

    def test_mixed_mode_blocks_external_without_optin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rfp_intake.config.settings import reset_settings
        from rfp_intake.domain.model_routing import PrivacyViolationError
        from rfp_intake.llm import provider as provider_mod

        monkeypatch.setenv("RFP_INTAKE_LLM_BACKEND", "caii")
        reset_settings()
        monkeypatch.setattr(
            provider_mod, "get_model_routing", lambda: self._external_routing("mixed")
        )
        with pytest.raises(PrivacyViolationError):
            provider_mod.get_llm("extract")

    def test_mock_backend_short_circuits_routing_entirely(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The offline test escape hatch must not consult models.yaml at all."""
        from rfp_intake.llm import provider as provider_mod

        def _boom() -> None:
            raise AssertionError("routing should not be consulted for the mock backend")

        monkeypatch.setattr(provider_mod, "get_model_routing", _boom)
        assert isinstance(provider_mod.get_llm("extract"), MockChatModel)

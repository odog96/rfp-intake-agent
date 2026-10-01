"""Tests for the model-service health check."""

from __future__ import annotations

import pytest

from rfp_intake.llm import health
from rfp_intake.llm.health import (
    ServiceFailure,
    classify_failure,
    describe_failure,
    probe_model_service,
    service_name,
)


class TestClassifyFailure:
    """The vocabularies of both provider stacks map onto one set of kinds.

    The Bedrock strings are the ones the live service actually returned on runs
    r-20260831-150720 to r-20260901-140857; the OpenAI-compatible strings are
    the shapes the openai client raises against Cloudera AI Inference.
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "An error occurred (AccessDeniedException) when calling the "
                "Converse operation: Bearer Token has expired",
                "credential_expired",
            ),
            ("ExpiredTokenException: The security token included in the request is expired",
             "credential_expired"),
            ("AuthenticationError: Error code: 401 - Incorrect API key provided",
             "credential_rejected"),
            ("UnrecognizedClientException: The security token included is invalid",
             "credential_rejected"),
            ("NotFoundError: Error code: 404 - model not found", "model_unavailable"),
            ("ResourceNotFoundException: Could not resolve the foundation model",
             "model_unavailable"),
            ("APIConnectionError: Connection error.", "unreachable"),
            ("ReadTimeout: request timed out", "unreachable"),
            ("ThrottlingException: Too many requests", "rate_limited"),
            ("ProviderUnavailableError: Provider 'bedrock' requires the aws extra: "
             "pip install 'rfp-intake[aws]'", "misconfigured"),
            ("something nobody has seen before", "unknown"),
        ],
    )
    def test_kinds(self, text: str, expected: str) -> None:
        assert classify_failure(text) == expected

    def test_accepts_an_exception(self) -> None:
        exc = RuntimeError("Bearer Token has expired")
        assert classify_failure(exc) == "credential_expired"

    def test_expiry_beats_the_generic_access_denied_it_arrives_with(self) -> None:
        """AWS reports an expired token as an AccessDeniedException, so the more
        specific phrase has to win or every expiry reads as a wrong key."""
        text = "(AccessDeniedException) ... : Bearer Token has expired"
        assert classify_failure(text) == "credential_expired"


class TestDescribeFailure:
    def test_names_the_service_in_words(self) -> None:
        failure = describe_failure(
            "Bearer Token has expired", provider="bedrock", role="classify"
        )
        assert failure.kind == "credential_expired"
        assert failure.service == "AWS Bedrock"
        assert "AWS Bedrock" in failure.reason
        assert "renew" in failure.action

    def test_says_the_documents_are_not_at_fault(self) -> None:
        """The whole point: the analyst must not go back to their files."""
        for provider in ("bedrock", "caii"):
            failure = describe_failure("Error code: 401", provider=provider)
            assert "not a problem with your documents" in failure.action

    def test_detail_carries_the_providers_own_words(self) -> None:
        failure = describe_failure(RuntimeError("boom"), provider="caii")
        assert failure.detail == "RuntimeError: boom"
        assert "boom" not in failure.reason

    def test_unknown_provider_falls_back_to_its_id(self) -> None:
        assert service_name("something-new") == "something-new"


class TestProbe:
    def test_mock_backend_is_never_probed(self) -> None:
        """The offline suite has no service to reach, and must not try."""
        assert probe_model_service() is None

    def test_first_failing_binding_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RFP_INTAKE_LLM_BACKEND", "routed")
        from rfp_intake.config.settings import reset_settings

        reset_settings()

        def boom(role: str) -> None:
            raise RuntimeError("Bearer Token has expired")

        monkeypatch.setattr(health, "get_llm", boom)

        failure = probe_model_service()
        assert isinstance(failure, ServiceFailure)
        assert failure.kind == "credential_expired"
        assert failure.role == "classify"

    def test_a_service_that_answers_reports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RFP_INTAKE_LLM_BACKEND", "routed")
        from rfp_intake.config.settings import reset_settings

        reset_settings()

        calls: list[str] = []

        class Answering:
            def invoke(self, _messages: object) -> str:
                return "ok"

        def fake_get_llm(role: str) -> Answering:
            calls.append(role)
            return Answering()

        monkeypatch.setattr(health, "get_llm", fake_get_llm)

        assert probe_model_service() is None
        # One call per distinct provider-and-model binding, not one per role:
        # config/models.yaml points all three roles at the same model today, so
        # probing per role would triple the cost of every run for nothing.
        from rfp_intake.domain.model_routing import get_model_routing

        distinct = {(b.provider, b.model) for b in get_model_routing().roles.values()}
        assert len(calls) == len(distinct)

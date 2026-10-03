"""Which AWS credential kind a run reports, and that it never prints one.

The behaviour under test is botocore's, not this project's: `_build_bedrock`
passes no credential, so botocore's chain decides. These tests pin the *report*
against what botocore was measured to do on 2026-10-03 (see
`src/rfp_intake/llm/credentials.py`), so that if botocore's precedence ever
changes, the sentence a run prints is what fails rather than quietly misleading
whoever is debugging a rejected credential.
"""

from __future__ import annotations

import pytest

from rfp_intake.llm.credentials import (
    ACCESS_KEY_ENV,
    BEARER_TOKEN_ENV,
    PROFILE_ENV,
    SECRET_KEY_ENV,
    SESSION_TOKEN_ENV,
    bedrock_credential_report,
)

# Values that must never appear in any output. Not real credentials.
FAKE_BEARER = "bearer-abcdef0123456789-not-a-real-token"
FAKE_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
FAKE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_SESSION = "FwoGZXIvYXdzEExampleSessionTokenValue"


def test_bearer_token_is_reported_when_it_is_the_only_credential() -> None:
    report = bedrock_credential_report({BEARER_TOKEN_ENV: FAKE_BEARER})

    assert report.kind == "bearer_token"
    assert report.shadowed == []
    assert BEARER_TOKEN_ENV in report.summary


def test_access_keys_are_reported_when_there_is_no_bearer_token() -> None:
    """The case Angus's environment is in, and the one believed not to work."""
    report = bedrock_credential_report(
        {ACCESS_KEY_ENV: FAKE_KEY_ID, SECRET_KEY_ENV: FAKE_SECRET}
    )

    assert report.kind == "access_keys"
    assert report.shadowed == []
    assert ACCESS_KEY_ENV in report.summary


def test_a_bearer_token_shadows_access_keys_and_the_report_says_so() -> None:
    """The trap. botocore prefers the bearer token per Bedrock operation, so a
    stale one makes a correct set of access keys look rejected."""
    report = bedrock_credential_report(
        {
            BEARER_TOKEN_ENV: FAKE_BEARER,
            ACCESS_KEY_ENV: FAKE_KEY_ID,
            SECRET_KEY_ENV: FAKE_SECRET,
        }
    )

    assert report.kind == "bearer_token"
    assert set(report.shadowed) == {ACCESS_KEY_ENV, SECRET_KEY_ENV}
    assert "ignored" in report.summary


def test_a_profile_is_reported_when_nothing_else_is_set() -> None:
    report = bedrock_credential_report({PROFILE_ENV: "default"})

    assert report.kind == "profile"
    assert PROFILE_ENV in report.summary


def test_an_empty_variable_does_not_count_as_set() -> None:
    """An unset variable and one set to "" or whitespace are the same thing here.
    A deployment that blanks a variable rather than unsetting it must not be
    reported as carrying a credential it does not have."""
    report = bedrock_credential_report(
        {BEARER_TOKEN_ENV: "   ", ACCESS_KEY_ENV: FAKE_KEY_ID, SECRET_KEY_ENV: FAKE_SECRET}
    )

    assert report.kind == "access_keys"


def test_no_credential_at_all_is_reported_as_ambient_with_a_warning_sentence() -> None:
    report = bedrock_credential_report({})

    assert report.kind == "ambient"
    assert "instance or container role" in report.summary


@pytest.mark.parametrize(
    "env",
    [
        {BEARER_TOKEN_ENV: FAKE_BEARER},
        {ACCESS_KEY_ENV: FAKE_KEY_ID, SECRET_KEY_ENV: FAKE_SECRET},
        {
            BEARER_TOKEN_ENV: FAKE_BEARER,
            ACCESS_KEY_ENV: FAKE_KEY_ID,
            SECRET_KEY_ENV: FAKE_SECRET,
            SESSION_TOKEN_ENV: FAKE_SESSION,
            PROFILE_ENV: "default",
        },
    ],
)
def test_no_part_of_any_credential_value_reaches_the_report(env: dict[str, str]) -> None:
    """The point of the whole module: it names variables, never their contents.

    Checked against every field, not just `summary`, and against fragments as
    short as eight characters, because a truncated secret is still a secret.
    """
    report = bedrock_credential_report(env)
    rendered = report.model_dump_json() + report.summary + report.label

    for value in env.values():
        assert value not in rendered
        for start in range(0, max(len(value) - 8, 1)):
            assert value[start : start + 8] not in rendered, (
                f"an 8-character fragment of a credential value appears in the report: "
                f"{value[start : start + 8]!r}"
            )

"""Which kind of AWS credential this run will use — named, never printed.

Measured on 2026-10-03, because the project was believed to work only with
`AWS_BEARER_TOKEN_BEDROCK` and to fail with standard access keys. It does not.
`llm/provider.py` `_build_bedrock` passes no credential at all — it hands
`ChatBedrockConverse` a model and a region and lets botocore's own chain resolve
the rest — so both kinds already work, and no code here chooses between them.

What botocore actually does, probed with dummy values and the request intercepted
before send, reading only the first word of the Authorization header:

    AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY   -> AWS4-HMAC-SHA256
    AWS_BEARER_TOKEN_BEDROCK                    -> Bearer
    both set                                    -> Bearer
    neither                                     -> AWS4-HMAC-SHA256, then fails

The last two lines are the reason this module exists. **A bearer token silently
wins over standard access keys**, so a stale `AWS_BEARER_TOKEN_BEDROCK` left in a
CML project's environment makes a correct set of access keys look broken, and
until now nothing in a run said which of the two had been used. The failure that
reaches the analyst is `credential_rejected` either way.

Nothing here reads a credential's value. The functions report the *names* of the
environment variables that are set, which is what tells an administrator where to
look, and never any part of a secret.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel

# The variable botocore resolves per Bedrock operation, in preference to sigv4.
# Not read by this project's code — named here so a run can report it.
BEARER_TOKEN_ENV = "AWS_BEARER_TOKEN_BEDROCK"
ACCESS_KEY_ENV = "AWS_ACCESS_KEY_ID"
SECRET_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
SESSION_TOKEN_ENV = "AWS_SESSION_TOKEN"
PROFILE_ENV = "AWS_PROFILE"

CredentialKind = Literal["bearer_token", "access_keys", "profile", "ambient"]


class CredentialReport(BaseModel):
    """Which credential kind a Bedrock call will use, in words fit for a log.

    `summary` is one sentence for the run log and for a failure's operator
    detail. It contains environment variable names and nothing from their values.
    """

    kind: CredentialKind
    # Set when a credential of lower precedence is also present and will be
    # ignored. This is the stale-token trap, and the only thing here worth a
    # warning rather than an info line.
    shadowed: list[str] = []
    present: list[str] = []

    @property
    def label(self) -> str:
        return {
            "bearer_token": f"a bearer token ({BEARER_TOKEN_ENV})",
            "access_keys": f"standard AWS access keys ({ACCESS_KEY_ENV})",
            "profile": f"the AWS profile named in {PROFILE_ENV}",
            "ambient": "no credential in the environment",
        }[self.kind]

    @property
    def summary(self) -> str:
        line = f"AWS Bedrock credential: {self.label}."
        if self.kind == "ambient":
            line += (
                " botocore will look for an instance or container role; if there "
                "is none, every model call will fail."
            )
        if self.shadowed:
            line += (
                f" Also set and ignored: {', '.join(self.shadowed)} — botocore "
                f"prefers {BEARER_TOKEN_ENV} for Bedrock, so these have no effect "
                "until it is unset."
            )
        return line


def bedrock_credential_report(env: dict[str, str] | None = None) -> CredentialReport:
    """Which credential kind a Bedrock call made now would use.

    Mirrors botocore's own precedence rather than deciding anything: the bearer
    token first, then access keys, then a named profile, then whatever ambient
    role the instance or container carries. Reads only whether each variable is
    set to a non-empty value.
    """
    source = os.environ if env is None else env

    def is_set(name: str) -> bool:
        return bool((source.get(name) or "").strip())

    every_var = (
        BEARER_TOKEN_ENV,
        ACCESS_KEY_ENV,
        SECRET_KEY_ENV,
        SESSION_TOKEN_ENV,
        PROFILE_ENV,
    )
    present = [name for name in every_var if is_set(name)]

    if is_set(BEARER_TOKEN_ENV):
        shadowed = [
            name for name in (ACCESS_KEY_ENV, SECRET_KEY_ENV, PROFILE_ENV) if is_set(name)
        ]
        return CredentialReport(kind="bearer_token", shadowed=shadowed, present=present)

    if is_set(ACCESS_KEY_ENV) and is_set(SECRET_KEY_ENV):
        return CredentialReport(kind="access_keys", present=present)

    if is_set(PROFILE_ENV):
        return CredentialReport(kind="profile", present=present)

    return CredentialReport(kind="ambient", present=present)

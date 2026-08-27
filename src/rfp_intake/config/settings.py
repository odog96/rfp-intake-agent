"""Environment-driven configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "RFP_INTAKE_"}

    # "mock" short-circuits everything and returns canned fixtures instead of
    # calling a model. Any other value defers to config/models.yaml for per-role
    # provider and model selection; the specific name is kept only for backward
    # compatibility with pre-routing deployments.
    #
    # The default is "routed", not "mock", so a deployment that forgets to set
    # this produces an error it can see rather than a full set of plausible
    # fixture values that look like a successful run. tests/conftest.py sets
    # "mock" explicitly for the offline suite (CLAUDE.md rule 6), so the test
    # suite does not depend on this default.
    llm_backend: Literal["caii", "litellm", "bedrock", "routed", "mock"] = "routed"

    caii_base_url: str = "http://localhost:8000/v1"
    caii_api_key: str | None = None
    # CAII authenticates with a CDP JWT, which CML workloads may expose as a
    # file rather than an env var. When set, this file's contents take
    # precedence over caii_api_key so a rotated token is picked up per run.
    caii_api_key_file: Path | None = None

    litellm_base_url: str = "http://localhost:4000/v1"
    litellm_api_key: str | None = None

    model_classify: str = "default"
    model_extract: str = "default"
    model_adjudicate: str = "default"

    max_concurrency: int = 4
    run_dir: Path = Path("runs")

    # Both bound a single LLM call. Without them a served model that runs away —
    # a reasoning model with no tool-call parser will — hangs the CML job
    # indefinitely, holding a job slot with nothing to reap it. Observed
    # 2026-08-27: 3 of 9 extraction groups never returned at all until capped.
    llm_timeout_s: float = 120.0
    # 8192: a narrating model with no tool-call parser spends budget on prose
    # before the JSON starts, and the widest field group (operational_metrics —
    # the budget drivers) returns several records per field. Both 2048 and 4096
    # truncated real answers mid-object on 2026-08-27.
    llm_max_tokens: int = 8192

    bedrock_region: str = "us-east-1"

    fields_yaml_path: Path = Path("config/fields.yaml")
    models_yaml_path: Path = Path("config/models.yaml")
    precedence_yaml_path: Path = Path("config/precedence.yaml")

    job_name: str = "RFP Pipeline Executor"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """For testing — clear cached settings."""
    global _settings  # noqa: PLW0603
    _settings = None

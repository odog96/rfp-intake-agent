"""Environment-driven configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "RFP_INTAKE_"}

    # "mock" short-circuits everything (offline tests). Any other value defers to
    # config/models.yaml for per-role provider and model selection; the specific
    # name is kept only for backward compatibility with pre-routing deployments.
    llm_backend: Literal["caii", "litellm", "bedrock", "routed", "mock"] = "mock"

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

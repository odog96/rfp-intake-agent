"""Environment-driven configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "RFP_INTAKE_"}

    llm_backend: Literal["caii", "litellm", "mock"] = "mock"

    caii_base_url: str = "http://localhost:8000/v1"
    litellm_base_url: str = "http://localhost:4000/v1"

    model_classify: str = "default"
    model_extract: str = "default"
    model_adjudicate: str = "default"

    max_concurrency: int = 4
    run_dir: Path = Path("runs")

    fields_yaml_path: Path = Path("config/fields.yaml")
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

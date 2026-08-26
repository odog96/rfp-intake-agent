"""LLM provider seam — get_llm(role) dispatches to the configured backend."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel

from rfp_intake.config.settings import get_settings

LLMRole = Literal["classify", "extract", "adjudicate"]


def get_llm(role: LLMRole) -> BaseChatModel:
    """Return an LLM instance for the given role, using the configured backend."""
    settings = get_settings()
    backend = settings.llm_backend

    if backend == "mock":
        from rfp_intake.llm.mock import MockChatModel

        return MockChatModel(role=role)

    model_name = _get_model_for_role(role)

    if backend == "caii":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=settings.caii_base_url,
            model=model_name,
            api_key="not-needed",  # CAII uses internal auth
        )

    if backend == "litellm":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=settings.litellm_base_url,
            model=model_name,
            api_key="not-needed",
        )

    raise ValueError(f"Unknown LLM backend: {backend}")


def _get_model_for_role(role: LLMRole) -> str:
    settings = get_settings()
    mapping = {
        "classify": settings.model_classify,
        "extract": settings.model_extract,
        "adjudicate": settings.model_adjudicate,
    }
    return mapping[role]

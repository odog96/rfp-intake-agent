"""Deterministic mock LLM for offline testing and CI."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class MockChatModel(BaseChatModel):
    """Returns deterministic fixtures keyed by input hash. No network access."""

    role: str = "extract"
    fixtures: dict[str, Any] = {}

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        input_hash = self._hash_messages(messages)
        fixture_key = f"{self.role}:{input_hash[:8]}"

        if fixture_key in self.fixtures:
            response = self.fixtures[fixture_key]
        else:
            response = self._default_fixture()

        content = json.dumps(response) if isinstance(response, dict) else str(response)
        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGeneration]:
        raise NotImplementedError("Streaming not supported in mock")

    def _hash_messages(self, messages: list[BaseMessage]) -> str:
        parts = []
        for m in messages:
            if hasattr(m, "content") and isinstance(m.content, str):
                parts.append(m.content)
        return hashlib.sha256("".join(parts).encode()).hexdigest()

    def _default_fixture(self) -> dict[str, Any]:
        if self.role == "classify":
            return {
                "kind": "protocol",
                "confidence": 0.95,
                "version_label": None,
                "document_date": "2024-01-15",
                "sponsor": "Example Pharma",
                "protocol_id": "TEST-001",
            }
        elif self.role == "extract":
            return {
                "sites_total": [
                    {
                        "raw_value": "75",
                        "quote": "A total of 260 subjects will be enrolled across 75 sites",
                        "status": "found",
                        "confidence": 0.92,
                        "scope": "total",
                        "page": 5,
                        "notes": None,
                    }
                ],
                "subjects_total": [
                    {
                        "raw_value": "260",
                        "quote": "A total of 260 subjects will be enrolled",
                        "status": "found",
                        "confidence": 0.90,
                        "scope": "total",
                        "page": 5,
                        "notes": "enrolled subjects",
                    }
                ],
            }
        elif self.role == "adjudicate":
            return {
                "verdict": "not_a_conflict",
                "explanation": "Values refer to different scopes (total vs. per-country)",
                "resolved_value": None,
                "severity": "low",
            }
        return {}

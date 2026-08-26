"""Structured output — two strategies behind one interface.

Strategy 1: Native tool-calling (with_structured_output)
Strategy 2: JSON-schema-guided decoding for vLLM-backed endpoints

Detection happens once per StructuredOutput instance.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypeVar

import structlog
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    pass


class StructuredOutput:
    """Unified interface for schema-constrained LLM calls."""

    def __init__(self, llm: BaseChatModel, strategy: Literal["native", "guided"] | None = None):
        self._llm = llm
        self.strategy: Literal["native", "guided"] = strategy or self._detect_strategy()
        logger.info("structured_output_initialized", strategy=self.strategy)

    def _detect_strategy(self) -> Literal["native", "guided"]:
        """Detect which strategy to use based on model/endpoint characteristics."""
        model_name = getattr(self._llm, "model_name", "") or ""
        model_lower = model_name.lower()

        # vLLM-backed endpoints → guided decoding is more reliable
        if any(kw in model_lower for kw in ("vllm", "caii")):
            return "guided"

        # Mock backend returns raw JSON — use guided to parse it
        llm_type = getattr(self._llm, "_llm_type", "")
        if llm_type == "mock":
            return "guided"

        # Default: try native if available
        if hasattr(self._llm, "with_structured_output"):
            return "native"

        return "guided"

    def extract(self, schema: type[T], messages: list[BaseMessage]) -> T:
        """Extract structured data from the LLM using the detected strategy."""
        if self.strategy == "native":
            return self._extract_native(schema, messages)
        return self._extract_guided(schema, messages)

    def _extract_native(self, schema: type[T], messages: list[BaseMessage]) -> T:
        """Use with_structured_output (native tool-calling)."""
        structured_llm = self._llm.with_structured_output(schema)
        result = structured_llm.invoke(messages)
        if isinstance(result, schema):
            return result
        # Shouldn't happen, but handle gracefully
        return schema.model_validate(result)  # type: ignore[arg-type]

    def _extract_guided(self, schema: type[T], messages: list[BaseMessage]) -> T:
        """Use JSON schema-guided decoding."""
        json_schema = schema.model_json_schema()

        # Augment the system message with schema instruction
        schema_instruction = (
            "\n\nOUTPUT FORMAT: Respond with valid JSON matching this schema exactly. "
            "No markdown, no explanation, only the JSON object.\n"
            f"```json\n{json.dumps(json_schema, indent=2)}\n```"
        )

        augmented: list[BaseMessage] = []
        system_found = False
        for msg in messages:
            if isinstance(msg, SystemMessage) and not system_found:
                augmented.append(SystemMessage(content=str(msg.content) + schema_instruction))
                system_found = True
            else:
                augmented.append(msg)

        if not system_found:
            augmented.insert(0, SystemMessage(content=schema_instruction.strip()))

        kwargs: dict[str, Any] = {}
        # vLLM guided decoding parameter
        kwargs["extra_body"] = {"guided_json": json_schema}

        response = self._llm.invoke(augmented, **kwargs)

        response_text = str(response.content).strip()
        # Clean common artifacts
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()

        try:
            data = json.loads(response_text)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "structured_output_parse_failure",
                strategy="guided",
                error=str(e),
                response_preview=response_text[:200],
            )
            raise StructuredOutputError(
                f"Failed to parse guided response: {e}"
            ) from e


def get_structured_output(
    llm: BaseChatModel,
    strategy: Literal["native", "guided"] | None = None,
) -> StructuredOutput:
    """Factory for structured output extraction."""
    return StructuredOutput(llm, strategy=strategy)

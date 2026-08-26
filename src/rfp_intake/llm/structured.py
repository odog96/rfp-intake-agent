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
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


def _unwrap_schema_echo(data: Any, schema: type[BaseModel]) -> Any:
    """Unwrap a response that echoed the JSON Schema envelope instead of an instance.

    Prompted with a schema, a model sometimes replies {"properties": {...}} with
    the real values inside. The payload is correct; only the envelope is wrong.
    Unwrapped only when the inner object actually looks like the target schema,
    so a legitimate field named "properties" is never stripped.
    """
    if not isinstance(data, dict):
        return data
    inner = data.get("properties")
    if not isinstance(inner, dict) or "properties" in schema.model_fields:
        return data

    required = {n for n, f in schema.model_fields.items() if f.is_required()}
    top_level_satisfies = required <= data.keys()
    inner_satisfies = required <= inner.keys()
    if inner_satisfies and not top_level_satisfies:
        return inner
    return data


def _decode_json_strings(args: dict[str, Any]) -> dict[str, Any]:
    """Decode values that arrived as JSON text instead of structures.

    Applied only after a direct validation attempt has already failed, so a
    legitimate string field that happens to contain JSON is never rewritten on
    the happy path. A value that does not decode is left exactly as it was —
    this repairs encoding, it never invents content.
    """
    repaired: dict[str, Any] = {}
    for key, value in args.items():
        # Only structures and the null literal. Deliberately not every JSON-parsable
        # string: "75" is a legitimate raw_value and must stay a string.
        if isinstance(value, str) and (
            value.lstrip()[:1] in ("[", "{") or value.strip() == "null"
        ):
            try:
                repaired[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        repaired[key] = value
    return repaired


class StructuredOutputError(Exception):
    pass


class StructuredOutput:
    """Unified interface for schema-constrained LLM calls."""

    def __init__(
        self,
        llm: BaseChatModel,
        strategy: Literal["native", "guided"] | None = None,
        *,
        allow_fallback: bool = True,
    ):
        self._llm = llm
        self._allow_fallback = allow_fallback
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

    def _supports_guided_json(self) -> bool:
        """Whether the backend accepts vLLM's guided_json extra_body parameter.

        Keyed on the client class rather than the model name: the parameter is a
        property of the serving stack's API surface, and only the OpenAI-compatible
        clients tunnel unknown fields through to the server.
        """
        module = type(self._llm).__module__
        return module.startswith("langchain_openai") or module.startswith("rfp_intake.llm.mock")

    def extract(self, schema: type[T], messages: list[BaseMessage]) -> T:
        """Extract structured data from the LLM using the detected strategy.

        A native-strategy failure falls back to guided once. Tool-call adherence
        is a model property, not an endpoint property: Llama 3.1 on Bedrock
        answers a long extraction prompt in prose and emits no tool call at all,
        and Bedrock refuses tool_choice="any" for it, so forcing is not an option.
        Without this fallback the whole field group is dropped for a model that
        is perfectly capable of returning the JSON directly.
        """
        if self.strategy != "native":
            return self._extract_guided(schema, messages)

        try:
            return self._extract_native(schema, messages)
        except StructuredOutputError as exc:
            if not self._allow_fallback:
                raise
            logger.warning(
                "structured_output_native_failed_falling_back",
                error=str(exc)[:200],
                schema=schema.__name__,
            )
        return self._extract_guided(schema, messages)

    def _extract_native(self, schema: type[T], messages: list[BaseMessage]) -> T:
        """Use with_structured_output (native tool-calling).

        Requests the raw message alongside the parsed result so a parse failure
        can be repaired rather than surfacing as a bare None. Some models — Llama
        on Bedrock Converse notably — emit a correct tool call whose nested
        list/object arguments are JSON-encoded *strings* rather than structures.
        The values are right; only the encoding is wrong, so dropping the whole
        group would discard good extractions.
        """
        structured_llm = self._llm.with_structured_output(schema, include_raw=True)
        result = structured_llm.invoke(messages)

        if isinstance(result, schema):  # backend ignored include_raw
            return result

        if not isinstance(result, dict):
            raise StructuredOutputError(
                f"Unexpected structured-output result type: {type(result).__name__}"
            )

        parsed = result.get("parsed")
        if isinstance(parsed, schema):
            return parsed

        args = self._tool_call_args(result.get("raw"))
        if args is None:
            raise StructuredOutputError(
                "Model returned no usable tool call. "
                f"parsing_error={result.get('parsing_error')!r}"
            )

        try:
            return schema.model_validate(args)
        except ValidationError:
            logger.info("structured_output_repairing_stringified_args", schema=schema.__name__)

        try:
            return schema.model_validate(_decode_json_strings(args))
        except ValidationError as exc:
            raise StructuredOutputError(
                f"Tool call arguments do not satisfy {schema.__name__}: {exc}"
            ) from exc

    @staticmethod
    def _tool_call_args(raw: object) -> dict[str, Any] | None:
        """Pull the first tool call's arguments off a raw AIMessage, if any."""
        tool_calls = getattr(raw, "tool_calls", None)
        if not tool_calls:
            return None
        args = tool_calls[0].get("args")
        return args if isinstance(args, dict) else None

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

        # Server-side constrained decoding is a vLLM/OpenAI-server extension. On a
        # backend that lacks it (Bedrock Converse rejects the kwarg outright) the
        # strategy degrades to schema-in-prompt, which is the other half of what
        # "guided" means here — never a crash.
        kwargs: dict[str, Any] = {}
        if self._supports_guided_json():
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
            return schema.model_validate(_unwrap_schema_echo(data, schema))
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
    *,
    allow_fallback: bool = True,
) -> StructuredOutput:
    """Factory for structured output extraction."""
    return StructuredOutput(llm, strategy=strategy, allow_fallback=allow_fallback)


def get_structured_output_for_role(llm: BaseChatModel, role: str) -> StructuredOutput:
    """Factory that honours an explicit `strategy:` pinned on the role in
    config/models.yaml, falling back to auto-detection when none is set.

    Auto-detection keys on the model name, which cannot know that a given
    served model has weak tool-call adherence. Pinning is how an operator
    records what was actually observed against their endpoint.
    """
    from rfp_intake.domain.model_routing import get_model_routing

    try:
        pinned = get_model_routing().binding(role).strategy
    except KeyError:
        pinned = None
    return StructuredOutput(llm, strategy=pinned)

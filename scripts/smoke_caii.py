"""Smoke test one OpenAI-compatible inference endpoint before wiring it to the graph.

Runs four escalating checks and reports which rung fails, so a broken run can be
attributed to the endpoint, the credential, the model, or our own parsing —
rather than surfacing later as an opaque pipeline error:

    1. /models reachable      — URL, TLS and credential are good; prints served model ids
    2. plain completion       — the model answers at all
    3. native structured      — with_structured_output / tool-calling works
    4. guided structured      — vLLM guided_json works

Checks 3 and 4 drive the project's own llm/structured.py, not a parallel
implementation, so a pass here means the pipeline's extraction path works. They
are run independently because ARCHITECTURE.md §5 treats both as supported
strategies and _detect_strategy() picks between them by model-name substring —
this script tells you which one your endpoint actually honours.

Usage:
    python scripts/smoke_caii.py --base-url https://<caii-host>/v1 --model <id>
    python scripts/smoke_caii.py            # uses RFP_INTAKE_* settings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from rfp_intake.config.settings import get_settings  # noqa: E402
from rfp_intake.llm.provider import resolve_api_key  # noqa: E402

TIMEOUT_S = 120.0


class SmokeExtraction(BaseModel):
    """Deliberately shaped like a FieldRecord: a value plus its verbatim evidence."""

    value: int = Field(description="The number of sites stated in the text")
    quote: str = Field(description="The verbatim sentence the number came from")


PROMPT_SYSTEM = "You extract structured data from clinical trial documents."
PROMPT_USER = (
    "Text: 'The study will enrol 240 participants across 75 investigative sites "
    "in 12 countries.'\n\nExtract the number of sites and the sentence it came from."
)


def _ok(label: str, detail: str = "") -> bool:
    print(f"  \033[32mPASS\033[0m  {label}" + (f" — {detail}" if detail else ""))
    return True


def _fail(label: str, exc: object) -> bool:
    text = str(exc).replace("\n", " ")
    print(f"  \033[31mFAIL\033[0m  {label} — {type(exc).__name__}: {text[:300]}")
    return False


def check_models(base_url: str, api_key: str) -> bool:
    print("\n[1/4] GET /models")
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        ids = [m.get("id", "?") for m in resp.json().get("data", [])]
    except Exception as exc:  # noqa: BLE001 - report every failure shape
        return _fail("endpoint unreachable or rejected the credential", exc)
    return _ok("endpoint reachable", f"served model ids: {ids or '(none reported)'}")


def _chat_model(base_url: str, api_key: str, model: str):  # type: ignore[no-untyped-def]
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=base_url,
        model=model,
        api_key=api_key,  # type: ignore[arg-type]
        timeout=TIMEOUT_S,
    )


def check_completion(base_url: str, api_key: str, model: str) -> bool:
    print("\n[2/4] plain chat completion")
    try:
        llm = _chat_model(base_url, api_key, model)
        reply = llm.invoke([HumanMessage(content="Reply with exactly: OK")])
        content = str(reply.content).strip()
    except Exception as exc:  # noqa: BLE001
        return _fail("model did not answer", exc)
    return _ok("model answered", f"{content[:80]!r}")


def check_structured(base_url: str, api_key: str, model: str, strategy: str, rung: str) -> bool:
    print(f"\n[{rung}] structured output — {strategy} strategy")
    from rfp_intake.llm.structured import StructuredOutput

    try:
        llm = _chat_model(base_url, api_key, model)
        structured = StructuredOutput(llm, strategy=strategy)  # type: ignore[arg-type]
        result = structured.extract(
            SmokeExtraction,
            [SystemMessage(content=PROMPT_SYSTEM), HumanMessage(content=PROMPT_USER)],
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{strategy} strategy unusable", exc)

    if result.value != 75:
        return _ok(
            f"{strategy} schema honoured, value WRONG",
            f"got {result.value}, expected 75 — transport fine, model weak",
        )
    return _ok(f"{strategy} strategy works", f"value={result.value} quote={result.quote[:50]!r}")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="defaults to the configured backend's URL")
    parser.add_argument("--model", default=None, help="defaults to RFP_INTAKE_MODEL_EXTRACT")
    parser.add_argument("--api-key", default=None, help="defaults to the resolved credential")
    args = parser.parse_args()

    backend = settings.llm_backend if settings.llm_backend != "mock" else "caii"
    base_url = args.base_url or (
        settings.caii_base_url if backend == "caii" else settings.litellm_base_url
    )
    model = args.model or settings.model_extract
    api_key = args.api_key or resolve_api_key(backend)

    print(f"endpoint : {base_url}")
    print(f"model    : {model}")
    print(f"credential: {'supplied' if api_key != 'not-needed' else 'NONE (placeholder)'}")

    if not check_models(base_url, api_key):
        print("\nStopped at rung 1 — fix the URL or credential before going further.")
        return 1
    if not check_completion(base_url, api_key, model):
        print("\nStopped at rung 2 — endpoint is up but the model id is likely wrong.")
        return 1

    native = check_structured(base_url, api_key, model, "native", "3/4")
    guided = check_structured(base_url, api_key, model, "guided", "4/4")

    print("\n" + "=" * 64)
    if native and guided:
        print("Both strategies work. _detect_strategy()'s choice is safe either way.")
    elif guided:
        print("Only GUIDED works. Force it — see llm/structured.py:38, which would")
        print("pick 'native' for a model id containing neither 'vllm' nor 'caii'.")
    elif native:
        print("Only NATIVE works. Ensure the model id does NOT contain 'vllm'/'caii',")
        print("or _detect_strategy() will wrongly select guided decoding.")
    else:
        print("Neither strategy works. Extraction cannot run against this endpoint.")
    return 0 if (native or guided) else 1


if __name__ == "__main__":
    raise SystemExit(main())

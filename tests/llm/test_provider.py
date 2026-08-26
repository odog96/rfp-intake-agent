"""Tests for llm/provider.py — backend switching and role mapping."""

from __future__ import annotations

from rfp_intake.llm.mock import MockChatModel
from rfp_intake.llm.provider import get_llm


def test_get_llm_mock_backend() -> None:
    llm = get_llm("extract")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "extract"


def test_get_llm_classify_role() -> None:
    llm = get_llm("classify")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "classify"


def test_get_llm_adjudicate_role() -> None:
    llm = get_llm("adjudicate")
    assert isinstance(llm, MockChatModel)
    assert llm.role == "adjudicate"


def test_mock_returns_deterministic_output() -> None:
    from langchain_core.messages import HumanMessage

    llm = get_llm("extract")
    msg = [HumanMessage(content="Extract visits from this document")]
    r1 = llm.invoke(msg)
    r2 = llm.invoke(msg)
    assert r1.content == r2.content

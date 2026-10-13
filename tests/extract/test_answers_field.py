"""EXTRACT's two rejection rules of stage 6 of the 2026-10-12 plan, and the
section heading it records on each kept record.

`answers_field` rejects a record that passed the word-for-word quote check but
does not answer the field: a "not specified" the model rates below 0.50, and a
value the field lists under `quote_must_say` in config/fields.yaml given with a
quote that says none of its words (fix 11: run r-20261012-angus read an IV
infusion from RFP p.1, which names only an antibody). A rejected record goes to
the run's errors with its value, quote and page, and gets no repair retry,
because a retry cannot change what the passage says.

The model is a scripted stand-in, as in tests/extract/test_repair_retry.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from rfp_intake.domain.dynamic import FieldExtractionItem, _field_id_to_attr, build_extraction_model
from rfp_intake.domain.registry import FieldDef, Registry, get_registry
from rfp_intake.domain.schemas import Document, ExtractionTask, Section
from rfp_intake.extract.prompt import quote_section
from rfp_intake.extract.validate import NOT_SPECIFIED_MIN_CONFIDENCE, answers_field

_FORM = "ip.form"
_GROUP = "treatment_dosing"


class _ScriptedModel:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[list[BaseMessage]] = []

    def extract(self, schema: type, messages: list[BaseMessage]) -> Any:
        self.calls.append(list(messages))
        return self.responses.pop(0)


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch, fields_yaml_path: Any) -> Registry:
    monkeypatch.setenv("RFP_INTAKE_FIELDS_YAML_PATH", str(fields_yaml_path))
    get_registry.cache_clear()
    return get_registry()


def _field(registry: Registry, field_id: str) -> FieldDef:
    return next(f for f in registry.fields if f.id == field_id)


def _item(raw_value: str, quote: str, status: str = "found",
          confidence: float = 0.9) -> FieldExtractionItem:
    return FieldExtractionItem(
        raw_value=raw_value, quote=quote, status=status,  # type: ignore[arg-type]
        confidence=confidence, page=1,
    )


class TestNotSpecifiedNeedsConfidence:
    def test_below_the_floor_is_rejected(self, registry: Registry) -> None:
        item = _item("not specified", "No total is given.", "not_specified", 0.49)
        reason = answers_field(item, _field(registry, _FORM))
        assert reason is not None and reason.startswith("not_specified_below_0.50")

    def test_at_the_floor_is_kept(self, registry: Registry) -> None:
        item = _item("not specified", "No total is given.", "not_specified",
                     NOT_SPECIFIED_MIN_CONFIDENCE)
        assert answers_field(item, _field(registry, _FORM)) is None

    def test_a_found_record_at_low_confidence_is_not_this_rule(self, registry: Registry) -> None:
        item = _item("oral tablet", "taken as an oral tablet", confidence=0.3)
        assert answers_field(item, _field(registry, _FORM)) is None

    def test_the_prompt_says_silence_is_left_out_not_not_specified(self) -> None:
        # Fix 4: "not_specified" only where the quoted passage says the item
        # does not apply; a document that does not mention it gets no record.
        from rfp_intake.extract.prompt import EXTRACT_SYSTEM_TEMPLATE

        rules = " ".join(EXTRACT_SYSTEM_TEMPLATE.split())
        assert ('Set status="not_specified" only where the passage you quote itself says the '
                "item does not apply") in rules
        assert 'is not "not specified": omit the field.' in rules


class TestTheQuoteMustSayTheRoute:
    def test_an_infusion_from_a_quote_naming_only_the_drug_is_rejected(
        self, registry: Registry
    ) -> None:
        item = _item("infusion_iv", "NEOD001 is a humanized monoclonal antibody")
        assert answers_field(item, _field(registry, _FORM)) == "quote_does_not_say_infusion_iv"

    @pytest.mark.parametrize("quote", [
        "administered intravenously over 60 minutes",
        "given as an INFUSION every 28 days",
        "24 mg/kg IV",
    ])
    def test_any_listed_word_in_any_case_passes(self, registry: Registry, quote: str) -> None:
        item = _item("infusion_iv", quote)
        assert answers_field(item, _field(registry, _FORM)) is None

    def test_the_value_is_read_as_normalize_reads_it(self, registry: Registry) -> None:
        # "IV infusion" normalizes to infusion_iv, so the rule applies to it too.
        rejected = _item("IV infusion", "NEOD001 is a humanized monoclonal antibody")
        assert answers_field(rejected, _field(registry, _FORM)) == "quote_does_not_say_infusion_iv"

    def test_a_word_inside_another_word_does_not_count(self, registry: Registry) -> None:
        # "IV" inside "derivative" is not the route.
        item = _item("infusion_iv", "a derivative of the antibody")
        assert answers_field(item, _field(registry, _FORM)) is not None

    def test_a_value_with_no_words_listed_is_not_checked(self, registry: Registry) -> None:
        item = _item("injection_sc", "NEOD001 is a humanized monoclonal antibody")
        assert answers_field(item, _field(registry, _FORM)) is None

    def test_a_field_with_no_words_listed_is_not_checked(self, registry: Registry) -> None:
        field = next(f for f in registry.fields if not f.quote_must_say and not f.derived)
        assert answers_field(_item("anything", "a quote"), field) is None


_PAGE_1 = (
    "1 SYNOPSIS\nNEOD001 is a humanized monoclonal antibody.\n"
    "6 STUDY DRUG\nNEOD001 will be administered by intravenous infusion every 28 days."
)
_ANTIBODY = "NEOD001 is a humanized monoclonal antibody."
_ROUTE = "NEOD001 will be administered by intravenous infusion every 28 days."


def _run(
    monkeypatch: pytest.MonkeyPatch, registry: Registry, items: list[FieldExtractionItem],
) -> tuple[Any, Any, _ScriptedModel]:
    schema = build_extraction_model(_GROUP, registry)
    model = _ScriptedModel([schema(**{_field_id_to_attr(_FORM): items})])

    import rfp_intake.extract as extract

    monkeypatch.setattr(extract, "get_llm", lambda role: None)
    monkeypatch.setattr(extract, "get_structured_output_for_role", lambda llm, role: model)

    split = _PAGE_1.index("6 STUDY DRUG")
    doc = Document(
        id="doc-1", path="/tmp/protocol.pdf", kind="protocol", pages=1,
        page_texts={1: _PAGE_1},
        sections=[
            Section(id="s1", heading="1 SYNOPSIS", page_start=1, page_end=1, end_offset=split),
            Section(id="s6", heading="6 STUDY DRUG", page_start=1, page_end=1,
                    start_offset=split),
        ],
    )
    task = ExtractionTask(doc_id="doc-1", group=_GROUP, page_window=(1, 1),
                          section_ids=["s1", "s6"])
    records, errors = extract.extract_group(task, doc, registry)
    return records, errors, model


class TestExtractGroup:
    def test_a_rejected_record_is_saved_with_its_page_and_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, registry: Registry
    ) -> None:
        records, errors, model = _run(monkeypatch, registry, [_item("infusion_iv", _ANTIBODY)])

        assert len(model.calls) == 1, "no repair retry for a rejected record"
        assert [r for r in records if r.field_id == _FORM] == []
        [error] = errors
        assert error.error == "Rejected: quote_does_not_say_infusion_iv"
        assert error.task_id == f"doc-1:{_GROUP}:{_FORM}"
        assert error.rejected_quote == _ANTIBODY
        assert error.rejected_page == 1
        assert error.rejected_value == "infusion_iv"

    def test_a_kept_record_carries_its_section_heading(
        self, monkeypatch: pytest.MonkeyPatch, registry: Registry
    ) -> None:
        records, errors, _ = _run(monkeypatch, registry, [_item("infusion_iv", _ROUTE)])

        assert errors == []
        [record] = [r for r in records if r.field_id == _FORM]
        assert record.provenance.section == "6 STUDY DRUG"


class TestQuoteSection:
    def _doc_and_task(self) -> tuple[Document, ExtractionTask]:
        split = _PAGE_1.index("6 STUDY DRUG")
        doc = Document(
            id="d", path="/tmp/p.pdf", kind="protocol", pages=1, page_texts={1: _PAGE_1},
            sections=[
                Section(id="a", heading=" 1 SYNOPSIS ", page_start=1, page_end=1,
                        end_offset=split),
                Section(id="b", heading="6 STUDY DRUG", page_start=1, page_end=1,
                        start_offset=split),
            ],
        )
        return doc, ExtractionTask(doc_id="d", group=_GROUP, page_window=(1, 1),
                                   section_ids=["a", "b"])

    def test_the_section_holding_the_quote_is_named(self) -> None:
        doc, task = self._doc_and_task()
        assert quote_section(task, doc, _ANTIBODY, 1) == "1 SYNOPSIS"
        assert quote_section(task, doc, "administered by   intravenous\ninfusion", 1) == (
            "6 STUDY DRUG"
        )

    def test_a_straight_apostrophe_finds_the_section_that_prints_a_curly_one(self) -> None:
        # The section is matched as validate_quote matches (item 5 of 2026-10-13),
        # so a quote that validated is not left without its heading.
        doc, task = self._doc_and_task()
        doc = doc.model_copy(update={"page_texts": {1: _PAGE_1.replace(
            "humanized", "subject’s humanized")}})
        assert quote_section(task, doc, "NEOD001 is a subject's humanized", 1) == "1 SYNOPSIS"

    def test_a_quote_on_another_page_or_in_no_section_is_none(self) -> None:
        doc, task = self._doc_and_task()
        assert quote_section(task, doc, _ANTIBODY, 2) is None
        assert quote_section(task, doc, "not on the page", 1) is None

    def test_a_task_with_no_sections_names_none(self) -> None:
        doc, task = self._doc_and_task()
        bare = task.model_copy(update={"section_ids": []})
        assert quote_section(bare, doc, _ANTIBODY, 1) is None

    def test_an_unchosen_section_is_not_named(self) -> None:
        doc, task = self._doc_and_task()
        only_b = task.model_copy(update={"section_ids": ["b"]})
        assert quote_section(only_b, doc, _ANTIBODY, 1) is None


@pytest.mark.slow
class TestOnTheRealDocuments:
    """The route rule on the sentences of the two sample PDFs run r-20261012-angus read."""

    def _sentence(self, samples_dir: Any, pdf: str, kind: str, page: int, words: str) -> str:
        from tests.plan.test_plan_sections_samples import _document

        path = samples_dir / pdf
        if not path.exists():
            pytest.skip(f"{pdf} not available")
        text = " ".join(_document(path, f"doc-{kind}", kind).page_texts[page].split())
        start = text.index(words)
        start = text.rfind(". ", 0, start) + 2 if ". " in text[:start] else 0
        return text[start:text.index(".", text.index(words)) + 1]

    def test_the_rfp_sentence_naming_only_the_antibody_is_rejected(
        self, registry: Registry, samples_dir: Any
    ) -> None:
        quote = self._sentence(samples_dir, "Synthetic_RFP_NEOD001.pdf", "rfp", 1,
                               "monoclonal antibody")
        item = _item("infusion_iv", quote)
        assert answers_field(item, _field(registry, _FORM)) == "quote_does_not_say_infusion_iv"

    def test_the_protocol_sentence_stating_the_route_is_kept(
        self, registry: Registry, samples_dir: Any
    ) -> None:
        quote = self._sentence(samples_dir, "Example protocol 2.pdf", "protocol", 36,
                               "administered by intravenous (IV) infusion")
        item = _item("infusion_iv", quote)
        assert answers_field(item, _field(registry, _FORM)) is None

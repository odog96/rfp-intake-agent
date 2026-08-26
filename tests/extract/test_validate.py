"""Tests for post-call extraction validation."""

from __future__ import annotations

from rfp_intake.domain.dynamic import FieldExtractionItem
from rfp_intake.domain.registry import FieldDef
from rfp_intake.extract.validate import (
    validate_numeric,
    validate_page,
    validate_quote,
    validate_record,
)


def _make_field_def(field_type: str = "text", values: list[str] | None = None) -> FieldDef:
    return FieldDef(
        id="test.field",
        group="test_group",
        label="Test Field",
        type=field_type,
        values=values,
    )


def _make_item(
    raw_value: str = "test",
    quote: str = "test quote",
    page: int = 5,
) -> FieldExtractionItem:
    return FieldExtractionItem(
        raw_value=raw_value,
        quote=quote,
        confidence=0.9,
        page=page,
    )


class TestValidateQuote:
    def test_exact_match(self) -> None:
        assert validate_quote("hello world", "the hello world is here")

    def test_whitespace_normalized(self) -> None:
        assert validate_quote("hello  world", "the hello world is here")

    def test_newline_normalized(self) -> None:
        assert validate_quote("hello\nworld", "the hello world is here")

    def test_not_found(self) -> None:
        assert not validate_quote("missing text", "the document says something else")

    def test_empty_quote_fails(self) -> None:
        assert not validate_quote("", "some excerpt")

    def test_case_sensitive(self) -> None:
        assert not validate_quote("Hello World", "the hello world is here")


class TestValidatePage:
    def test_in_window(self) -> None:
        assert validate_page(5, (3, 8))

    def test_at_start(self) -> None:
        assert validate_page(3, (3, 8))

    def test_at_end(self) -> None:
        assert validate_page(8, (3, 8))

    def test_before_window(self) -> None:
        assert not validate_page(2, (3, 8))

    def test_after_window(self) -> None:
        assert not validate_page(9, (3, 8))


class TestValidateNumeric:
    def test_int_with_digits(self) -> None:
        field_def = _make_field_def("int")
        assert validate_numeric("75 sites", field_def)

    def test_int_no_digits(self) -> None:
        field_def = _make_field_def("int")
        assert not validate_numeric("many sites", field_def)

    def test_non_numeric_field_passes(self) -> None:
        field_def = _make_field_def("text")
        assert validate_numeric("no digits here", field_def)

    def test_number_with_decimal(self) -> None:
        field_def = _make_field_def("number")
        assert validate_numeric("3.5 mg/kg", field_def)


class TestValidateRecord:
    def test_all_valid(self) -> None:
        excerpt = "A total of 260 subjects will be enrolled across 75 sites"
        item = _make_item(
            raw_value="75",
            quote="A total of 260 subjects will be enrolled across 75 sites",
            page=5,
        )
        field_def = _make_field_def("int")
        valid, reason = validate_record(item, excerpt, (3, 8), field_def)
        assert valid
        assert reason is None

    def test_quote_not_found(self) -> None:
        excerpt = "Some other text entirely"
        item = _make_item(quote="this is not in the excerpt", page=5)
        field_def = _make_field_def("text")
        valid, reason = validate_record(item, excerpt, (3, 8), field_def)
        assert not valid
        assert reason == "quote_not_found_in_excerpt"

    def test_page_out_of_window(self) -> None:
        excerpt = "the test quote is here"
        item = _make_item(quote="test quote", page=10)
        field_def = _make_field_def("text")
        valid, reason = validate_record(item, excerpt, (3, 8), field_def)
        assert not valid
        assert "page_10_outside_window" in reason  # type: ignore[operator]

    def test_numeric_no_digits(self) -> None:
        excerpt = "the document says many patients"
        item = _make_item(raw_value="many", quote="many patients", page=5)
        field_def = _make_field_def("int")
        valid, reason = validate_record(item, excerpt, (3, 8), field_def)
        assert not valid
        assert "numeric_field_no_digits" in reason  # type: ignore[operator]

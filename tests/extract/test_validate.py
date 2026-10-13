"""Tests for post-call extraction validation."""

from __future__ import annotations

from rfp_intake.domain.dynamic import FieldExtractionItem
from rfp_intake.domain.registry import FieldDef
from rfp_intake.extract.validate import (
    nearest_passage,
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


# Item 5 of Oliver's 2026-10-13 prompt: curly and straight apostrophes are one
# character to the quote check, and so are curly and straight quotation marks.
# Nothing else is loosened. The sentences are Example protocol 2.pdf's own.
P12 = (
    "The subject’s health status, as well as details of any hospitalizations "
    "will be collected"
)
P112 = "Conduct a vital status phone call per Section 7"


class TestQuoteMarks:
    def test_a_straight_apostrophe_matches_the_pdfs_curly_one(self) -> None:
        # p.12 and p.79 print "subject’s"; the model types "subject's".
        assert validate_quote(
            "The subject's health status, as well as details of any hospitalizations",
            P12,
        )

    def test_a_curly_apostrophe_matches_a_straight_one(self) -> None:
        assert validate_quote("the subject’s last visit", "from the subject's last visit")

    def test_a_left_single_mark_matches_a_straight_apostrophe(self) -> None:
        assert validate_quote("the ‘Unblinded’ staff", "the 'Unblinded' staff only")

    def test_straight_quotation_marks_match_curly_ones(self) -> None:
        assert validate_quote(
            'labelled "Study Drug" on the kit',
            "labelled “Study Drug” on the kit",
        )

    def test_curly_quotation_marks_match_straight_ones(self) -> None:
        assert validate_quote(
            "labelled “Study Drug” on the kit",
            'labelled "Study Drug" on the kit',
        )

    def test_a_quote_that_dropped_a_word_is_still_rejected(self) -> None:
        # p.112: the model dropped "a" from the sentence.
        assert validate_quote(P112, P112)
        assert not validate_quote("Conduct vital status phone call per Section 7", P112)

    def test_a_quote_that_dropped_a_word_beside_a_curly_mark_is_still_rejected(self) -> None:
        assert not validate_quote("The subject's status, as well as details", P12)

    def test_an_apostrophe_does_not_match_a_quotation_mark(self) -> None:
        assert not validate_quote("the 'Study Drug' kit", "the “Study Drug” kit")

    def test_other_marks_are_not_straightened(self) -> None:
        # A prime and a backtick are not apostrophes.
        assert not validate_quote("the subject's visit", "the subject′s visit")
        assert not validate_quote("the subject's visit", "the subject`s visit")

    def test_a_missing_apostrophe_is_still_rejected(self) -> None:
        assert not validate_quote("the subjects health status", P12)


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


# The synthetic RFP's Site Qualification row as the excerpt holds it: the
# label's second line, "Time on Site", falls between the value's words.
_TWO_COLUMN_EXCERPT = (
    "--- Page 2 ---\nEnrolment will be completed in 9 months across 75 sites.\n\n"
    "--- Page 3 ---\n"
    "Site Qualification Visits &      1 visit per site; a total of 80 visits to \n \n"
    "Time on Site                     qualify 75 sites; an average of 5 hours \n \n"
    "                                 per visit\n"
    "Site Initiation Visits           1 visit per site"
)


class TestNearestPassage:
    """`nearest_passage` words the repair retry. It is never used as a quote."""

    def test_finds_the_row_a_two_column_layout_split(self) -> None:
        quote = "a total of 80 visits to qualify 75 sites"
        assert not validate_quote(quote, _TWO_COLUMN_EXCERPT)

        passage = nearest_passage(quote, _TWO_COLUMN_EXCERPT)

        assert passage is not None
        assert "80 visits to Time on Site qualify 75 sites" in passage

    def test_the_passage_is_text_the_quote_check_accepts(self) -> None:
        # So a quote copied from it validates — and one that is not, does not.
        passage = nearest_passage("a total of 80 visits to qualify 75 sites", _TWO_COLUMN_EXCERPT)
        assert passage is not None
        assert validate_quote(passage, _TWO_COLUMN_EXCERPT)

    def test_never_crosses_a_page_marker(self) -> None:
        passage = nearest_passage("75 sites Site Qualification Visits", _TWO_COLUMN_EXCERPT)
        assert passage is not None
        assert "--- Page" not in passage

    def test_never_crosses_a_table_marker(self) -> None:
        excerpt = (
            "--- Page 3 ---\nThe last line of page text reads 80 visits to\n\n"
            "--- Table (page 3) ---\n| qualify 75 sites |"
        )
        passage = nearest_passage("80 visits to qualify 75 sites", excerpt)
        assert passage is not None
        assert "Table" not in passage

    def test_an_unrelated_quote_gets_no_passage(self) -> None:
        assert nearest_passage("placebo matched in colour and taste", _TWO_COLUMN_EXCERPT) is None

    def test_an_empty_quote_gets_no_passage(self) -> None:
        assert nearest_passage("  ;  ", _TWO_COLUMN_EXCERPT) is None

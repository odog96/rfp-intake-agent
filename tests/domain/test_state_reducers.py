"""Reducer semantics for RunState.records."""

from rfp_intake.domain.schemas import Replace, append_or_replace


class TestAppendOrReplace:
    def test_plain_list_appends_for_fan_in(self) -> None:
        # EXTRACT runs one branch per (document, group); each returns only its own.
        assert append_or_replace([1, 2], [3]) == [1, 2, 3]

    def test_replace_overwrites(self) -> None:
        # NORMALIZE rewrites every record it was handed.
        assert append_or_replace([1, 2], Replace([9])) == [9]

    def test_replace_with_empty_clears(self) -> None:
        assert append_or_replace([1, 2], Replace([])) == []

    def test_replace_result_is_a_plain_list(self) -> None:
        # The marker must not leak into state and turn a later append into a replace.
        out = append_or_replace([1], Replace([9]))
        assert type(out) is list

    def test_normalize_does_not_double_records(self) -> None:
        # The regression itself: NORMALIZE returning everything it was given
        # under an append reducer produced each record twice.
        extracted = ["a", "b", "c"]
        assert append_or_replace(extracted, Replace(list(extracted))) == extracted

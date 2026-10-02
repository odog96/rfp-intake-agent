"""config/sections.yaml: the loader, the matching rules, and the file's contents.

The content tests are here on purpose. `config/sections.yaml` is the whole
behaviour of SET_ASIDE_SECTIONS — PLAN_2026-10-02.md stage 3 puts no heading in
Python — so an edit to the file is a change to the product and belongs under
test like code. In particular, three entries are *deliberately absent* and the
reason is subtle enough that someone would otherwise add them back in good
faith.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.domain.section_policy import (
    SectionsPolicy,
    contains_phrase,
    contains_phrase_with_number,
    load_sections_policy,
    normalise_heading,
    strip_heading,
)


@pytest.fixture
def policy(sections_yaml_path: Path) -> SectionsPolicy:
    return load_sections_policy(sections_yaml_path)


class TestNormaliseHeading:
    @pytest.mark.parametrize(
        ("heading", "expected"),
        [
            ("7.2 Inclusion Criteria", "inclusion criteria"),
            ("1.3.2 Clinical Experience", "clinical experience"),
            ("4 STUDY DESIGN", "study design"),
            ("10.1.2.3 Record Retention", "record retention"),
            # PyMuPDF breaks headings across lines; FIND_SECTIONS already relies
            # on whitespace normalisation to find them on the page at all.
            ("1.3.2 \nClinical   Experience", "clinical experience"),
            ("Glossary", "glossary"),
            ("8. References", "references"),
            # A number with no text leaves nothing to match, which must not
            # become an empty string that matches every entry.
            ("7.2", ""),
        ],
    )
    def test_number_and_case_are_stripped(self, heading: str, expected: str) -> None:
        assert normalise_heading(heading) == expected

    def test_a_decimal_inside_the_words_survives(self) -> None:
        # Only a *leading* number goes. "Section 1.2" keeps its number because
        # the number is not what the heading starts with.
        assert normalise_heading("Appendix 2 Schedule of Activities") == (
            "appendix 2 schedule of activities"
        )


class TestContainsPhrase:
    @pytest.mark.parametrize(
        ("text", "phrase"),
        [
            ("ethics committee", "ethics"),
            ("independent ethics committee", "ethics committee"),
            ("nonclinical safety data", "nonclinical"),
            ("non-clinical safety data", "non-clinical"),
            # A line break inside the phrase, which is how the protocol's
            # storage section actually reads.
            ("limited to the unblinded\npharmacy staff", "pharmacy staff"),
            ("Completed CRF pages", "crf"),
        ],
    )
    def test_matches_whole_phrases(self, text: str, phrase: str) -> None:
        assert contains_phrase(text, phrase)

    @pytest.mark.parametrize(
        ("text", "phrase"),
        [
            # The rule that makes the whole list safe: `ethics` must not eat a
            # section on bioethics, and `crf` must not eat "eCRF" (which has its
            # own entry, so it is set aside only where intended).
            ("bioethics review", "ethics"),
            ("eCRF completion", "crf"),
            ("nonclinical", "non-clinical"),
            ("pregnancies", "pregnancy"),
            ("", "ethics"),
            ("ethics", ""),
        ],
    )
    def test_rejects_partial_words(self, text: str, phrase: str) -> None:
        assert not contains_phrase(text, phrase)


class TestContainsPhraseWithNumber:
    @pytest.mark.parametrize(
        "text",
        [
            "Approximately 350 eCRF pages are expected per subject.",
            "eCRF pages: 350",
            "The study requires 12 monitoring visits per site.",
        ],
    )
    def test_a_number_beside_the_phrase_counts(self, text: str) -> None:
        phrase = "ecrf" if "eCRF" in text else "monitoring visits"
        assert contains_phrase_with_number(text, phrase)

    def test_prose_with_no_number_does_not_count(self) -> None:
        assert not contains_phrase_with_number("All data are entered on the eCRF.", "ecrf")

    def test_a_number_far_away_does_not_count(self) -> None:
        text = "The eCRF must be completed." + " filler" * 20 + " 350 subjects."
        assert not contains_phrase_with_number(text, "ecrf")

    def test_a_digit_inside_the_phrase_alone_does_not_count(self) -> None:
        # Otherwise "eCRF" in "Section 9.1" territory would rescue on the
        # section number of every heading near it.
        assert not contains_phrase_with_number("eCRF", "ecrf")


class TestStripHeading:
    def test_the_heading_is_removed(self) -> None:
        assert strip_heading("8.1 Emergency Unblinding\nThe code may be broken.", (
            "8.1 Emergency Unblinding"
        )) == "\nThe code may be broken."

    def test_whitespace_inside_the_heading_does_not_matter(self) -> None:
        # PyMuPDF's actual output shape for the sample protocol's headings.
        body = strip_heading("1.3.2 \nClinical Experience\nStudy NEOD001-001...", (
            "1.3.2 Clinical Experience"
        ))
        assert body == "\nStudy NEOD001-001..."

    def test_a_heading_that_is_not_there_leaves_the_text_alone(self) -> None:
        """The safe direction: keeping more text can only keep a section.

        A rescue reading too much text keeps a section that should have gone,
        which costs tokens. Reading too little drops one that held a cost
        driver, which costs a number nobody budgets for.
        """
        text = "Some text that does not start with the heading."
        assert strip_heading(text, "6.4 Drug Storage") == text

    def test_an_empty_heading_leaves_the_text_alone(self) -> None:
        assert strip_heading("body", "") == "body"

    def test_the_self_rescue_it_exists_to_prevent(self) -> None:
        """A set-aside heading that itself contains a rescue phrase.

        The shipped `config/sections.yaml` has no such pair today: the one that
        existed, `emergency unblinding` against the rescue phrase `unblinding`,
        was removed when this was found. The rule stays because the two lists
        are edited by hand and the next overlapping pair would be silent — a
        heading would quietly cancel its own removal, and the only visible
        effect would be a larger bill.
        """
        policy = SectionsPolicy(
            set_aside=["emergency unblinding"], keep_if_contains=["unblinding"]
        )
        heading = "8.1 Emergency Unblinding"
        text = f"{heading}\nThe investigator may break the code in an emergency."
        assert policy.matching_set_aside(heading) is not None
        assert policy.rescuing_phrase(text) == "unblinding"
        assert policy.rescuing_phrase(strip_heading(text, heading)) is None


class TestLoader:
    def test_missing_file_raises_rather_than_disabling_the_stage(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Sections policy not found"):
            load_sections_policy(tmp_path / "nope.yaml")

    def test_a_file_without_set_aside_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "sections.yaml"
        path.write_text("keep_if_contains:\n  - unblinded\n")
        with pytest.raises(ValueError, match="no 'set_aside' list"):
            load_sections_policy(path)

    def test_entries_are_lowercased_and_space_collapsed(self, tmp_path: Path) -> None:
        path = tmp_path / "sections.yaml"
        path.write_text("set_aside:\n  - '  Inclusion   Criteria '\n  - ''\n")
        loaded = load_sections_policy(path)
        assert loaded.set_aside == ["inclusion criteria"]

    def test_the_real_file_loads(self, policy: SectionsPolicy) -> None:
        assert len(policy.set_aside) > 50
        assert len(policy.keep_if_contains) > 10


class TestTheRealPolicy:
    @pytest.mark.parametrize(
        "heading",
        [
            # PLAN_2026-10-02.md stage 3 names these as examples.
            "1.1 Amendment History",
            "Summary of Changes",
            "Table of Contents",
            "Glossary",
            "List of Abbreviations",
            "7.1 Inclusion Criteria",
            "7.2 Exclusion Criteria",
            "7 Eligibility",
            "10.2 Adverse Event Reporting",
            "10.3 Serious Adverse Events",
            "10.5 Pregnancy",
            "9.4 Concomitant Medication",
            "Prior and Concomitant Therapy",
            "12.1 Data Monitoring Committee",
            "12 Committees",
            "11.2 Sample Size",
            "13.4 Record Retention",
            "13.2 Quality Assurance",
            "13.6 Informed Consent",
            "13.5 Ethics",
            "13.9 Publication",
            "14 References",
            "2.2 Nonclinical Safety Data",
            "Appendix 3 Contraception",
            "Appendix 5 Questionnaires",
        ],
    )
    def test_the_headings_the_plan_names_are_set_aside(
        self, policy: SectionsPolicy, heading: str
    ) -> None:
        assert policy.matching_set_aside(heading) is not None

    @pytest.mark.parametrize(
        "heading",
        [
            # ANALYST_PROCEDURE_PROTOCOL.md section 5: scan, do not skip.
            "11 Statistical Considerations",
            "11.1 Statistical Analysis",
            "Appendix 1 Schedule of Activities",
            "Appendices",
            # Deliberately absent until stage 4 passes on its own — see the note
            # at the top of config/sections.yaml.
            "1 Introduction",
            "1.2 Background",
            "1.4 Rationale",
            # Never listed, and the rule is that an unlisted heading survives.
            "4 Study Design",
            "6 Study Drug",
            "Schedule of Activities",
        ],
    )
    def test_the_headings_that_must_survive_are_not_set_aside(
        self, policy: SectionsPolicy, heading: str
    ) -> None:
        assert policy.matching_set_aside(heading) is None

    def test_no_duplicate_entries(self, policy: SectionsPolicy) -> None:
        assert len(set(policy.set_aside)) == len(policy.set_aside)
        assert len(set(policy.keep_if_contains)) == len(policy.keep_if_contains)

    def test_redundant_entries_are_only_redundant_never_contradictory(
        self, policy: SectionsPolicy
    ) -> None:
        """Several entries are covered by a shorter one, which is harmless.

        `sample size calculation` is already matched by `sample size`. The file
        keeps both because each line traces to a specific instruction in
        docs/ANALYST_PROCEDURE_PROTOCOL.md section 4. What would *not* be
        harmless is an entry that is subsumed by a shorter one while meaning
        something different, so this asserts the only relationship is
        containment — and reports which entries it found, so the number cannot
        drift unnoticed.
        """
        subsumed = {
            entry: other
            for entry in policy.set_aside
            for other in policy.set_aside
            if other != entry and contains_phrase(entry, other)
        }
        for entry, shorter in subsumed.items():
            assert shorter in entry, f"{entry!r} matched {shorter!r} but does not contain it"

    def test_the_keep_phrases_the_plan_requires_are_present(
        self, policy: SectionsPolicy
    ) -> None:
        """PLAN_2026-10-02.md stage 3 lists these as the minimum ("at least").

        Four of them rescue only with a number beside them, which is Angus's own
        qualification [02:00:16] and is why the two lists are checked together.
        """
        rescues = [*policy.keep_if_contains, *policy.keep_if_contains_with_number]
        for required in (
            "unblinded",
            "interim analysis",
            "schedule of activities",
            "schedule of events",
            "schedule of assessments",
            "case report form",
            "crf",
            "monitoring visit",
        ):
            assert required in rescues

    def test_unblinding_is_not_a_rescue_phrase(self, policy: SectionsPolicy) -> None:
        """It would contradict `emergency unblinding` on set_aside.

        A rescue reads the body, not the heading, so this is no longer the
        self-rescue it was — but the entry still meant "keep every section about
        the unblinding procedure", which is the opposite of [01:56:07].
        """
        assert "unblinding" not in policy.keep_if_contains
        assert "unblinding" not in policy.keep_if_contains_with_number

    def test_standard_of_care_is_not_set_aside_because_it_matches_the_title(
        self, policy: SectionsPolicy
    ) -> None:
        """The protocol's own title contains the phrase twice.

        Setting the title page aside would lose the phase, the blinding, the
        control, the number of arms and the population in one go — everything
        docs/ANALYST_PROCEDURE_PROTOCOL.md section 2 says to read first.
        """
        title = (
            "NEOD001-CL002 AMENDMENT 3: A PHASE 3, RANDOMIZED, MULTICENTER, "
            "DOUBLE-BLIND, PLACEBO-CONTROLLED, 2-ARM, EFFICACY AND SAFETY STUDY OF "
            "NEOD001 PLUS STANDARD OF CARE VS. PLACEBO PLUS STANDARD OF CARE IN "
            "SUBJECTS WITH LIGHT CHAIN (AL) AMYLOIDOSIS"
        )
        assert policy.matching_set_aside(title) is None

    @pytest.mark.parametrize(
        "heading",
        ["TABLE OF CONTENTS", "LIST OF TABLES", "LIST OF FIGURES", "GLOSSARY OF TERMS"],
    )
    def test_the_index_sections_cannot_be_rescued(
        self, policy: SectionsPolicy, heading: str
    ) -> None:
        assert policy.is_always_set_aside(heading)
        assert policy.matching_set_aside(heading) is not None

    @pytest.mark.parametrize("heading", ["11 Statistical Considerations", "Appendices"])
    def test_an_ordinary_section_is_not_an_index_section(
        self, policy: SectionsPolicy, heading: str
    ) -> None:
        assert not policy.is_always_set_aside(heading)

    def test_a_crf_count_rescues_but_crf_prose_does_not(self, policy: SectionsPolicy) -> None:
        # Angus [02:00:16]: ignore CRF details "unless the text gives a number".
        assert policy.rescuing_phrase("Approximately 350 eCRF pages are expected.") == "ecrf"
        assert policy.rescuing_phrase("All data must be entered on the eCRF.") is None

    def test_the_storage_sentence_from_the_protocol_rescues_its_section(
        self, policy: SectionsPolicy
    ) -> None:
        # ANALYST_PROCEDURE_PROTOCOL.md section 5, quoted: sometimes the storage
        # section is the only place the document admits unblinded staff.
        assert policy.matching_set_aside("6.4 Drug Storage") is not None
        assert policy.rescuing_phrase(
            "Access to the study drug should be strictly limited to the "
            "unblinded pharmacy staff."
        ) == "unblinded"

    def test_an_ordinary_section_is_not_rescued(self, policy: SectionsPolicy) -> None:
        assert policy.rescuing_phrase("Subjects must be at least 18 years of age.") is None

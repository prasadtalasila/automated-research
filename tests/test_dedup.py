"""src/dedup.py: near-duplicate citekey detection by shared DOI or
near-identical title. Advisory only -- see module docstring for why a
shared title isn't treated as certain proof of duplication."""

from src import dedup

from tests.conftest import make_reference


class TestNormalizeTitle:
    def test_strips_braces_and_lowercases(self):
        assert dedup._normalize_title("Digital {Twin} {Platform}") == "digital twin platform"

    def test_collapses_punctuation_and_whitespace(self):
        assert dedup._normalize_title("A Paper: Part I -- Overview!") == "a paper part i overview"


class TestNormalizeDoi:
    def test_strips_url_prefix(self):
        assert dedup._normalize_doi("https://doi.org/10.1000/xyz123") == "10.1000/xyz123"

    def test_strips_doi_colon_prefix(self):
        assert dedup._normalize_doi("doi:10.1000/xyz123") == "10.1000/xyz123"

    def test_lowercases_and_strips_whitespace(self):
        assert dedup._normalize_doi("  10.1000/XYZ123  ") == "10.1000/xyz123"


class TestFindDuplicates:
    def test_no_duplicates_among_distinct_references(self):
        refs = [
            make_reference(citekey="a2024", title="Digital Twins"),
            make_reference(citekey="b2024", title="Composable Architectures"),
        ]
        assert dedup.find_duplicates(refs) == []

    def test_shared_doi_is_flagged(self):
        refs = [
            make_reference(citekey="a2024", title="Title A", doi="10.1000/xyz"),
            make_reference(citekey="b2024", title="Title B (preprint)", doi="https://doi.org/10.1000/xyz"),
        ]
        groups = dedup.find_duplicates(refs)
        assert len(groups) == 1
        assert {r.citekey for r in groups[0]} == {"a2024", "b2024"}

    def test_shared_title_different_case_and_braces_is_flagged(self):
        refs = [
            make_reference(citekey="a2024", title="Digital {Twin} Platform"),
            make_reference(citekey="b2024", title="digital twin platform"),
        ]
        groups = dedup.find_duplicates(refs)
        assert len(groups) == 1
        assert {r.citekey for r in groups[0]} == {"a2024", "b2024"}

    def test_three_way_title_match_grouped_together(self):
        refs = [
            make_reference(citekey="a2024", title="Same Title"),
            make_reference(citekey="b2024", title="Same Title"),
            make_reference(citekey="c2024", title="Same Title"),
        ]
        groups = dedup.find_duplicates(refs)
        assert len(groups) == 1
        assert {r.citekey for r in groups[0]} == {"a2024", "b2024", "c2024"}

    def test_doi_match_not_double_reported_via_title(self):
        refs = [
            make_reference(citekey="a2024", title="Same Title", doi="10.1000/xyz"),
            make_reference(citekey="b2024", title="Same Title", doi="10.1000/xyz"),
        ]
        groups = dedup.find_duplicates(refs)
        assert len(groups) == 1

    def test_missing_doi_and_title_do_not_crash(self):
        refs = [
            make_reference(citekey="a2024", title="", doi=None),
            make_reference(citekey="b2024", title="", doi=None),
        ]
        assert dedup.find_duplicates(refs) == []

    def test_empty_reference_list(self):
        assert dedup.find_duplicates([]) == []

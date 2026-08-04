"""src/passages.py: where a citekey's supporting text comes from, and
whether it may be quoted.

These cases moved here from tests/test_citation_provenance.py when the
ladder was extracted -- they were never really about provenance
reporting, and src/retrieval.py is about to become the second consumer.
The invariant they exist to pin is the one the whole module is for: a
source with no reading order yields a page number and never a quotation.
"""

import json

import pytest

from src import config, ledger, passages


def _add_item(citekey, parsed_text=None, pdf_path=None, title="T"):
    """Insert a ledger row, optionally with parsed text on disk."""
    parsed_path = None
    if parsed_text is not None:
        config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
        parsed_path = config.PARSED_DIR / f"{citekey}.txt"
        parsed_path.write_text(parsed_text, encoding="utf-8")
        parsed_path = str(parsed_path)
    con = ledger.connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO items (citekey, title, status, parsed_path, pdf_path, last_synced)"
            " VALUES (?, ?, 'parsed', ?, ?, '2026-01-01')",
            (citekey, title, parsed_path, pdf_path),
        )
        con.commit()
    finally:
        con.close()


def _sidecar(citekey, records):
    config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCLING_DIR / f"{citekey}.passages.json").write_text(json.dumps(records))


class TestDistinctive:
    def test_drops_stopwords_and_short_words(self, isolated_config):
        assert passages.distinctive("The cat is on a mat") == {"cat", "mat"}

    def test_is_case_insensitive(self, isolated_config):
        assert passages.distinctive("Digital TWIN") == passages.distinctive("digital twin")


class TestQuotable:
    def test_a_passage_with_text_is_quotable(self, isolated_config):
        assert passages.Passage(page=1, words={"a"}, text="Real paragraph.").quotable

    def test_a_passage_without_text_is_not(self, isolated_config):
        """`text is None` is the whole guarantee: a page-level passage
        cannot be quoted because there is nothing there to quote."""
        assert not passages.Passage(page=1, words={"a"}).quotable


class TestSourcePassages:
    def test_prefers_the_docling_sidecar(self, isolated_config):
        _add_item("a_2024", parsed_text="page one\fpage two")
        _sidecar("a_2024", [{"text": "A real reading-ordered paragraph.",
                             "label": "text", "page": 4}])
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert len(found) == 1
        assert found[0].quotable
        assert found[0].page == 4
        assert found[0].label == "text"

    def test_falls_back_to_form_feed_pages_and_refuses_to_quote(self, isolated_config):
        _add_item("a_2024", parsed_text="first page text\fsecond page text")
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert len(found) == 2
        assert [p.page for p in found] == [1, 2]
        assert not any(p.quotable for p in found), (
            "page-level passages must never be quoted -- column splicing "
            "makes any excerpt a two-argument collage"
        )

    def test_unknown_citekey_reports_why(self, isolated_config):
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "ghost_2024")
        finally:
            con.close()
        assert found == []
        assert "ledger" in reason

    def test_no_parsed_text_and_no_pdf_reports_why(self, isolated_config):
        _add_item("a_2024")
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert found == []
        assert "no readable PDF" in reason

    def test_corrupt_sidecar_falls_through_instead_of_raising(self, isolated_config):
        _add_item("a_2024", parsed_text="page one\fpage two")
        config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
        (config.DOCLING_DIR / "a_2024.passages.json").write_text("{not json")
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert reason is None
        assert len(found) == 2  # fell back to pages

    def test_blank_pages_are_dropped(self, isolated_config):
        """A trailing form feed would otherwise contribute an empty page
        that matches nothing and shifts no numbering."""
        _add_item("a_2024", parsed_text="first page\f   \fthird page")
        con = ledger.connect()
        try:
            found, _ = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert [p.page for p in found] == [1, 3], (
            "page numbers stay tied to the source's own pagination"
        )


class TestSidecarRobustness:
    """A hand-edited or partially-written sidecar must degrade, not crash."""

    @pytest.mark.parametrize("payload", ['{"not": "a list"}', "[]", '["not a dict"]',
                                         '[{"text": "   "}]', '[{"no_text_key": 1}]'])
    def test_unusable_sidecar_shapes_fall_through_to_pages(self, isolated_config, payload):
        _add_item("a_2024", parsed_text="page one\fpage two")
        config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
        (config.DOCLING_DIR / "a_2024.passages.json").write_text(payload)
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert reason is None
        assert [p.page for p in found] == [1, 2]

    def test_truncated_utf8_sidecar_falls_through_instead_of_raising(self, isolated_config):
        """A process killed mid-write can split a multi-byte character,
        which fails to decode before json ever sees it."""
        _add_item("a_2024", parsed_text="page one\fpage two")
        config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
        # Valid JSON prefix, then a lone UTF-8 continuation byte.
        (config.DOCLING_DIR / "a_2024.passages.json").write_bytes(
            b'[{"text": "Real paragraph ' + b"\xe2\x82"
        )
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert reason is None
        assert [p.page for p in found] == [1, 2]

    def test_mixed_sidecar_keeps_the_usable_records(self, isolated_config):
        _add_item("a_2024", parsed_text="ignored\fignored")
        _sidecar("a_2024", ["junk", {"text": ""}, {"text": "Real paragraph here.", "page": 3}])
        con = ledger.connect()
        try:
            found, _ = passages.source_passages(con, "a_2024")
        finally:
            con.close()
        assert len(found) == 1
        assert found[0].text == "Real paragraph here."


class TestPdfFallback:
    def test_parsed_text_without_page_breaks_falls_through_to_the_pdf(
        self, isolated_config, monkeypatch, tmp_path
    ):
        """A docling-parsed .txt has no form feeds, so page numbers would
        all be 1 -- go back to the PDF rather than report that."""
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        _add_item("a_2024", parsed_text="one continuous document, no form feeds",
                  pdf_path=str(pdf))

        class FakeRun:
            stdout = "page one hysteresis\fpage two relay"

        monkeypatch.setattr(passages.subprocess, "run", lambda *a, **k: FakeRun())
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert [p.page for p in found] == [1, 2]

    def test_pdftotext_failure_is_reported_not_raised(self, isolated_config, monkeypatch, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        _add_item("a_2024", parsed_text="no form feeds here", pdf_path=str(pdf))

        def boom(*a, **k):
            raise OSError("pdftotext not on PATH")

        monkeypatch.setattr(passages.subprocess, "run", boom)
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()

        assert found == []
        assert "pdftotext" in reason

    def test_missing_parsed_file_falls_through_to_the_pdf(
        self, isolated_config, monkeypatch, tmp_path
    ):
        """The ledger records a parsed_path; the file behind it can still
        be gone (a cleaned content/ against a kept ledger)."""
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        _add_item("a_2024", parsed_text="page one\fpage two", pdf_path=str(pdf))
        config.PARSED_DIR.joinpath("a_2024.txt").unlink()

        class FakeRun:
            stdout = "from the pdf\fsecond page"

        monkeypatch.setattr(passages.subprocess, "run", lambda *a, **k: FakeRun())
        con = ledger.connect()
        try:
            found, reason = passages.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert [p.page for p in found] == [1, 2]


class TestSeamWithCitationProvenance:
    def test_citation_provenance_re_exports_the_ladder(self, isolated_config):
        """`citation_provenance` is a consumer of this module now, not the
        owner -- but it stays the import site its own callers already use,
        so the extraction isn't a breaking change for them."""
        from src import citation_provenance as cp

        assert cp.source_passages is passages.source_passages
        assert cp.Passage is passages.Passage
        assert cp.distinctive is passages.distinctive

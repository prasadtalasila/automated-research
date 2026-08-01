"""src/citation_provenance.py: what in a cited source supports the claim
citing it.

The interesting cases here are the ones a synthetic fixture makes easy to
get wrong: hard-wrapped prose (every draft this project writes), a source
with no reading order, and a citekey with nothing readable behind it.
"""

import json
import sqlite3

import pytest

from src import citation_provenance as cp
from src import config, ledger


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
        assert cp.distinctive("The cat is on a mat") == {"cat", "mat"}

    def test_is_case_insensitive(self, isolated_config):
        assert cp.distinctive("Digital TWIN") == cp.distinctive("digital twin")


class TestClaims:
    def test_reconstructs_a_sentence_across_wrapped_lines(self, isolated_config):
        """The regression that made the first build useless: drafts are
        hard-wrapped, so reading only the citation's own line yields a
        fragment that matches nothing."""
        draft = (
            "Simulation has become a cornerstone of developing\n"
            "and validating these systems\n"
            "[@zampetti_continuous_2023].\n"
        )
        (line, citekey, claim), = cp.claims(draft)

        assert citekey == "zampetti_continuous_2023"
        assert line == 3, "line number still points at the citation itself"
        assert claim.startswith("Simulation has become a cornerstone")
        assert "validating these systems" in claim

    def test_picks_the_citing_sentence_not_the_whole_paragraph(self, isolated_config):
        draft = (
            "Twins support what-if analysis [@a_2024]. Testing is a\n"
            "separate concern entirely [@b_2024].\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert "what-if" in found["a_2024"]
        assert "what-if" not in found["b_2024"]
        assert "separate concern" in found["b_2024"]

    def test_does_not_split_on_abbreviations(self, isolated_config):
        draft = "As Fig. 1 shows, the loop closes [@a_2024].\n"
        (_, _, claim), = cp.claims(draft)
        assert claim.startswith("As Fig. 1 shows")

    def test_strips_citation_markup_from_the_claim(self, isolated_config):
        draft = "Digital twins close the loop [@a_2024].\n"
        (_, _, claim), = cp.claims(draft)
        assert "[@" not in claim
        assert claim == "Digital twins close the loop."

    def test_closes_the_gap_the_marker_leaves(self, isolated_config):
        """This text is quoted back to a reviewer, so "processes , or"
        reads as sloppiness in the draft rather than in this tool."""
        draft = "Systems integrate computation [@a_2024], or so it is claimed.\n"
        (_, _, claim), = cp.claims(draft)
        assert claim == "Systems integrate computation, or so it is claimed."
        assert "  " not in claim

    def test_no_citations_yields_nothing(self, isolated_config):
        assert cp.claims("Plain prose with no citations.\n") == []


class TestSourcePassages:
    def test_prefers_the_docling_sidecar(self, isolated_config):
        _add_item("a_2024", parsed_text="page one\fpage two")
        _sidecar("a_2024", [{"text": "A real reading-ordered paragraph.",
                             "label": "text", "page": 4}])
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert len(passages) == 1
        assert passages[0].quotable
        assert passages[0].page == 4

    def test_falls_back_to_form_feed_pages_and_refuses_to_quote(self, isolated_config):
        _add_item("a_2024", parsed_text="first page text\fsecond page text")
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert len(passages) == 2
        assert [p.page for p in passages] == [1, 2]
        assert not any(p.quotable for p in passages), (
            "page-level passages must never be quoted -- column splicing "
            "makes any excerpt a two-argument collage"
        )

    def test_unknown_citekey_reports_why(self, isolated_config):
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "ghost_2024")
        finally:
            con.close()
        assert passages == []
        assert "ledger" in reason

    def test_no_parsed_text_and_no_pdf_reports_why(self, isolated_config):
        _add_item("a_2024")
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()
        assert passages == []
        assert "no readable PDF" in reason

    def test_corrupt_sidecar_falls_through_instead_of_raising(self, isolated_config):
        _add_item("a_2024", parsed_text="page one\fpage two")
        config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
        (config.DOCLING_DIR / "a_2024.passages.json").write_text("{not json")
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()
        assert reason is None
        assert len(passages) == 2  # fell back to pages


class TestScoring:
    def test_overlap_survives_paraphrase(self, isolated_config):
        """The reason this uses overlap rather than verbatim n-grams:
        a paraphrase keeps content words while changing order and
        function words, and would score zero under exact matching."""
        passage = cp.Passage(page=1, words=cp.distinctive(
            "The digital twin supports what-if analysis of environmental changes"))
        score, best = cp.score_claim(
            "What-if analysis of environmental change is supported by the twin",
            [passage])
        assert score > 0.5
        assert best is passage

    def test_unrelated_claim_scores_low(self, isolated_config):
        passage = cp.Passage(page=1, words=cp.distinctive("ontology metamodel safety resilience"))
        score, _ = cp.score_claim("Bang-bang controllers need hysteresis bands", [passage])
        assert score == 0.0

    def test_no_passages_scores_zero(self, isolated_config):
        assert cp.score_claim("anything at all", []) == (0.0, None)

    def test_claim_with_only_stopwords_scores_zero(self, isolated_config):
        passage = cp.Passage(page=1, words={"anything"})
        assert cp.score_claim("it is the", [passage]) == (0.0, None)


class TestReport:
    def test_orders_worst_match_first(self, isolated_config):
        _add_item("good_2024", parsed_text="hysteresis band relay switching\fpage two")
        _add_item("poor_2024", parsed_text="entirely unrelated ontology material\fpage two")
        draft = (
            "The hysteresis band stops relay switching [@good_2024].\n"
            "\n"
            "The hysteresis band stops relay switching [@poor_2024].\n"
        )
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft)

        report = cp.build_report(path)

        assert [f.citekey for f in report.findings] == ["poor_2024", "good_2024"]
        assert report.findings[0].score < report.findings[1].score

    def test_markdown_states_it_is_not_a_gate(self, isolated_config):
        _add_item("a_2024", parsed_text="hysteresis band\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The hysteresis band matters [@a_2024].\n")

        text = cp.render_markdown(cp.build_report(path))

        assert "review aid, not a gate" in text
        assert "does not adjudicate" in text

    def test_markdown_quotes_only_when_reading_order_exists(self, isolated_config):
        _add_item("quotable_2024", parsed_text="ignored\fignored")
        _sidecar("quotable_2024", [{"text": "Hysteresis prevents relay chatter.",
                                    "label": "text", "page": 7}])
        _add_item("paged_2024", parsed_text="hysteresis relay chatter\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "Hysteresis prevents relay chatter [@quotable_2024].\n"
            "\n"
            "Hysteresis prevents relay chatter [@paged_2024].\n"
        )

        text = cp.render_markdown(cp.build_report(path))

        assert "> Hysteresis prevents relay chatter." in text
        assert "Best match is on **page 1**" in text

    def test_unreadable_source_is_explained_not_reported_as_unsupported(self, isolated_config):
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("A claim about something [@ghost_2024].\n")

        report = cp.build_report(path)
        text = cp.render_markdown(report)

        assert "ghost_2024" in report.unreadable
        assert "Sources that could not be read" in text
        assert "not because the claim is unsupported" in text

    def test_draft_without_citations_says_so(self, isolated_config):
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Prose with no citations at all.\n")
        assert "No citations found" in cp.render_markdown(cp.build_report(path))


class TestWriteReportAndCli:
    def test_writes_markdown_into_provenance_dir(self, isolated_config):
        _add_item("a_2024", parsed_text="hysteresis band\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The hysteresis band matters [@a_2024].\n")

        written = cp.write_report(path, ["md"])

        assert written["md"] == config.PROVENANCE_DIR / "d.provenance.md"
        assert written["md"].exists()

    def test_missing_render_binary_warns_and_still_returns_md(self, isolated_config, monkeypatch, capsys):
        from src.heavy import render_output

        def raise_missing(*a, **k):
            raise render_output.MissingBinary("pandoc not found")

        monkeypatch.setattr(render_output, "render", raise_missing)
        _add_item("a_2024", parsed_text="hysteresis band\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The hysteresis band matters [@a_2024].\n")

        written = cp.write_report(path, ["md", "pdf"])

        assert "md" in written and "pdf" not in written
        assert "pandoc not found" in capsys.readouterr().err

    def test_cli_reports_missing_draft(self, isolated_config, capsys):
        assert cp.main([str(config.CONTENT_DIR / "nope.md")]) == 1
        assert "No such draft" in capsys.readouterr().err

    def test_cli_writes_and_lists_outputs(self, isolated_config, capsys):
        _add_item("a_2024", parsed_text="hysteresis band\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The hysteresis band matters [@a_2024].\n")

        assert cp.main([str(path), "--formats", "md"]) == 0
        assert "d.provenance.md" in capsys.readouterr().out


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
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()
        assert reason is None
        assert [p.page for p in passages] == [1, 2]

    def test_mixed_sidecar_keeps_the_usable_records(self, isolated_config):
        _add_item("a_2024", parsed_text="ignored\fignored")
        _sidecar("a_2024", ["junk", {"text": ""}, {"text": "Real paragraph here.", "page": 3}])
        con = ledger.connect()
        try:
            passages, _ = cp.source_passages(con, "a_2024")
        finally:
            con.close()
        assert len(passages) == 1
        assert passages[0].text == "Real paragraph here."


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

        monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: FakeRun())
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()

        assert reason is None
        assert [p.page for p in passages] == [1, 2]

    def test_pdftotext_failure_is_reported_not_raised(self, isolated_config, monkeypatch, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        _add_item("a_2024", parsed_text="no form feeds here", pdf_path=str(pdf))

        def boom(*a, **k):
            raise OSError("pdftotext not on PATH")

        monkeypatch.setattr(cp.subprocess, "run", boom)
        con = ledger.connect()
        try:
            passages, reason = cp.source_passages(con, "a_2024")
        finally:
            con.close()

        assert passages == []
        assert "pdftotext" in reason


class TestBands:
    @pytest.mark.parametrize("score,expected", [
        (0.0, "no support found"), (0.19, "no support found"),
        (0.20, "weak"), (0.49, "weak"),
        (0.50, "supported"), (1.0, "supported"),
    ])
    def test_band_boundaries(self, isolated_config, score, expected):
        assert cp._band(score) == expected

    def test_thresholds_are_configurable(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PROVENANCE_WEAK_SCORE", 0.9)
        assert cp._band(0.5) == "no support found"


class TestEdgeShapes:
    def test_draft_ending_without_a_trailing_blank_line(self, isolated_config):
        """The last paragraph has no blank line closing it, so the span
        builder has to flush what it is still holding."""
        _add_item("a_2024", parsed_text="hysteresis relay\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Intro paragraph.\n\nThe hysteresis relay matters [@a_2024].")

        (_, _, claim), = cp.claims(path.read_text())
        assert claim == "The hysteresis relay matters."

    def test_citekey_not_in_any_sentence_falls_back_to_the_paragraph(self, isolated_config):
        """extract_citekeys found it, but sentence splitting put it in no
        part -- return the tidied paragraph rather than nothing."""
        assert cp._sentence_around("no marker here at all", "ghost_2024") == "no marker here at all"

    def test_claim_with_no_matching_words_reports_no_passage(self, isolated_config):
        _add_item("a_2024", parsed_text="ontology metamodel\fresilience safety")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Hysteresis prevents chatter [@a_2024].\n")

        text = cp.render_markdown(cp.build_report(path))
        assert "No passage in the source matched" in text

    def test_md_only_request_skips_the_render_import(self, isolated_config):
        _add_item("a_2024", parsed_text="hysteresis\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Hysteresis matters [@a_2024].\n")
        assert set(cp.write_report(path, ["md"])) == {"md"}

    def test_renders_tex_when_the_renderer_succeeds(self, isolated_config, monkeypatch, tmp_path):
        from src.heavy import render_output

        out = tmp_path / "r.tex"
        out.write_text("tex")
        monkeypatch.setattr(render_output, "render", lambda *a, **k: out)
        _add_item("a_2024", parsed_text="hysteresis\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Hysteresis matters [@a_2024].\n")

        written = cp.write_report(path, ["md", "tex"])
        assert written["tex"] == out

    def test_blank_lines_between_paragraphs_close_each_span(self, isolated_config):
        """Exercises the span builder's blank-line branch with content on
        both sides, not just a trailing flush."""
        spans = cp._paragraph_spans(["one", "", "two", "", "three"])
        assert spans == [(1, 1, "one"), (3, 3, "two"), (5, 5, "three")]

    def test_leading_blank_lines_are_not_a_paragraph(self, isolated_config):
        assert cp._paragraph_spans(["", "", "body"]) == [(3, 3, "body")]

    def test_trailing_blank_line_closes_the_last_span(self, isolated_config):
        assert cp._paragraph_spans(["body", ""]) == [(1, 1, "body")]

    def test_same_citekey_cited_twice_reads_the_source_once(self, isolated_config, monkeypatch):
        """The passage cache: re-reading a 40-page source per citation
        would make a heavily-cited draft needlessly slow."""
        _add_item("a_2024", parsed_text="hysteresis relay chatter\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "Hysteresis prevents chatter [@a_2024].\n"
            "\n"
            "The relay depends on hysteresis [@a_2024].\n"
        )

        calls = []
        real = cp.source_passages
        monkeypatch.setattr(cp, "source_passages",
                            lambda con, key: calls.append(key) or real(con, key))

        report = cp.build_report(path)

        assert len(report.findings) == 2
        assert calls == ["a_2024"], "second citation must reuse the cached passages"

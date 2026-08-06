"""src/citation_provenance.py: what in a cited source supports the claim
citing it.

The interesting cases here are the ones a synthetic fixture makes easy to
get wrong: hard-wrapped prose (every draft this project writes), and a
claim that scores against nothing.

Where the passages themselves come from -- sidecar, form-feed pages, or
`pdftotext` -- is tests/test_passages.py's, since src/passages.py owns
that ladder. What stays here is what this module still decides: which
sentence carries a citation, how it scores, and how the report reads.
"""

import json

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


class TestBlockShapedClaims:
    """A citation inside a table or a list is not inside a sentence.

    Both are one blank-line-separated block, so the paragraph reading
    that fixed hard-wrapped prose quoted the *entire* table back as the
    claim -- once per citekey in it -- and scored every row against the
    whole table's vocabulary (GitHub issue #19).
    """

    TABLE = (
        "Models fall into three families.\n"
        "\n"
        "| Model type | What it is built from | Typical use |\n"
        "|---|---|---|\n"
        "| Physics-based | Known structural equations [@a_2024] | Simulating a bridge |\n"
        "| Statistical | Measured hysteresis data [@b_2024] | Identifying behaviour |\n"
    )

    def test_a_table_row_is_the_claim_not_the_whole_table(self, isolated_config):
        found = {k: c for _, k, c in cp.claims(self.TABLE)}

        assert "Known structural equations" in found["a_2024"]
        assert "Measured hysteresis data" not in found["a_2024"], "the other row leaked in"
        assert "Model type" not in found["a_2024"], "the header row leaked in"

    def test_table_cells_read_as_prose(self, isolated_config):
        found = {k: c for _, k, c in cp.claims(self.TABLE)}

        assert found["a_2024"] == "Physics-based -- Known structural equations -- Simulating a bridge"
        assert "|" not in found["a_2024"], "a raw pipe becomes a wall of \\textbar{} in the tex render"
        assert "---" not in found["b_2024"], "the |---|---| separator row is not content"

    def test_two_citations_in_one_table_are_told_apart(self, isolated_config):
        """The whole point of scoring a sentence rather than a paragraph:
        a reviewer has to see *which* citation is the weak one."""
        found = {k: c for _, k, c in cp.claims(self.TABLE)}
        assert found["a_2024"] != found["b_2024"]

    def test_a_row_scores_on_its_own_words(self, isolated_config):
        """Whole-table claims inflate the denominator -- measured at ~4x on
        a real draft -- pushing a genuinely supported citation under the
        band thresholds."""
        _add_item("a_2024", parsed_text="known structural equations simulating a bridge deck\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.TABLE)

        report = cp.build_report(path)
        row = next(f for f in report.findings if f.citekey == "a_2024")
        assert row.score >= config.PROVENANCE_GOOD_SCORE

    def test_the_report_quotes_the_row_not_the_table(self, isolated_config):
        _add_item("a_2024", parsed_text="known structural equations\fpage two")
        _add_item("b_2024", parsed_text="measured hysteresis data\fpage two")
        path = config.CONTENT_DIR / "d.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.TABLE)

        quoted = [line for line in cp.render_markdown(cp.build_report(path)).splitlines()
                  if line.startswith("> ")]
        assert quoted, "the report quotes the citing claim"
        assert not any("|" in line for line in quoted)

    def test_a_list_item_is_the_claim_not_the_whole_list(self, isolated_config):
        """Bullets carry no sentence boundary the splitter recognises --
        a marker is not a capital letter -- so the list collapsed the same
        way a table did, with or without terminal punctuation."""
        draft = (
            "The building blocks are:\n"
            "\n"
            "- data, from sensors on the asset [@a_2024]\n"
            "- models, fitted to that data [@b_2024]\n"
            "- algorithms that act on both\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"] == "data, from sensors on the asset"
        assert "models" not in found["a_2024"]

    def test_a_wrapped_list_item_keeps_its_continuation_lines(self, isolated_config):
        """The hard-wrap fix still applies *within* a block: an item that
        runs over two lines is one claim, not a fragment."""
        draft = (
            "- data, gathered from sensors placed on the asset itself and\n"
            "  sampled continuously [@a_2024]\n"
            "- models, fitted to that data [@b_2024]\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"].startswith("data, gathered from sensors")
        assert "sampled continuously" in found["a_2024"]
        assert "models" not in found["a_2024"]

    def test_numbered_list_items_split_the_same_way(self, isolated_config):
        draft = (
            "1. First, calibrate the model [@a_2024]\n"
            "2. Then validate it against measurements [@b_2024]\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"] == "First, calibrate the model"
        assert found["b_2024"] == "Then validate it against measurements"

    def test_a_heading_is_not_part_of_the_sentence_below_it(self, isolated_config):
        draft = (
            "## Standards and interoperability [@a_2024]\n"
            "The prose beneath it, with no blank line between [@b_2024].\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"] == "Standards and interoperability"
        assert found["b_2024"] == "The prose beneath it, with no blank line between."

    def test_an_escaped_pipe_stays_inside_its_cell(self, isolated_config):
        draft = "| Notation | a \\| b means either [@a_2024] |\n"
        (_, _, claim), = cp.claims(draft)
        assert claim == "Notation -- a \\| b means either"

    def test_prose_paragraphs_are_untouched_by_block_splitting(self, isolated_config):
        """The regression guard for the fix itself: ordinary hard-wrapped
        prose must still be read a whole paragraph at a time."""
        draft = (
            "Simulation has become a cornerstone of developing\n"
            "and validating these systems\n"
            "[@a_2024]. A second sentence follows it.\n"
        )
        (_, _, claim), = cp.claims(draft)
        assert claim == "Simulation has become a cornerstone of developing and validating these systems."


class TestLatexBlockShapedClaims:
    """The same defect in the other syntax every skill also exports.

    A `tabular` row ends at `\\\\`, not at a newline, and `\\item` opens a
    list item -- neither is a sentence boundary, so a LaTeX draft
    collapsed exactly as a markdown one did. It was worse in one way:
    `\\begin{tabular}{lll}`, `\\toprule` and `\\midrule` reached the score
    as though `begin`, `tabular`, `lll` and `toprule` were content words
    the source ought to contain.
    """

    TABULAR = (
        "Approaches differ in how they compose twins.\n"
        "\n"
        "\\begin{tabular}{lll}\n"
        "\\toprule\n"
        "Approach & Composition mechanism & Limitation \\\\\n"
        "\\midrule\n"
        "Ontology-driven & Shared semantic model \\citep{a_2024} & Heavy modelling effort \\\\\n"
        "Service-oriented & Message contracts \\citep{b_2024} & Weak behavioural guarantees \\\\\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
    )

    def test_a_tabular_row_is_the_claim_not_the_whole_table(self, isolated_config):
        found = {k: c for _, k, c in cp.claims(self.TABULAR)}

        assert "Shared semantic model" in found["a_2024"]
        assert "Message contracts" not in found["a_2024"], "the next row leaked in"
        assert found["a_2024"] != found["b_2024"]

    def test_table_markup_is_not_scored_as_content(self, isolated_config):
        found = {k: c for _, k, c in cp.claims(self.TABULAR)}

        for noise in ("begin", "tabular", "lll", "toprule", "midrule", "&", "\\\\"):
            assert noise not in found["a_2024"], f"{noise!r} is markup, not a claim"
        assert found["a_2024"] == "Ontology-driven -- Shared semantic model -- Heavy modelling effort"

    def test_a_row_wrapped_across_lines_is_one_claim(self, isolated_config):
        """A LaTeX row ends at `\\\\`, so unlike a markdown row it can span
        source lines -- and the hard-wrap fix has to keep applying."""
        draft = (
            "\\begin{tabular}{ll}\n"
            "Ontology-driven & Shared semantic model with an agreed\n"
            "vocabulary \\citep{a_2024} \\\\\n"
            "Service-oriented & Message contracts \\citep{b_2024} \\\\\n"
            "\\end{tabular}\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"] == "Ontology-driven -- Shared semantic model with an agreed vocabulary"
        assert "Message contracts" not in found["a_2024"]

    def test_an_escaped_ampersand_stays_inside_its_cell(self, isolated_config):
        draft = "Research \\& development & funded separately \\citep{a_2024} \\\\\n"
        (_, _, claim), = cp.claims(draft)
        assert claim == "Research \\& development -- funded separately"

    def test_an_itemize_item_is_the_claim_not_the_whole_list(self, isolated_config):
        draft = (
            "\\begin{itemize}\n"
            "  \\item data, gathered from sensors on the asset \\citep{a_2024}\n"
            "  \\item models, fitted to that data \\citep{b_2024}\n"
            "\\end{itemize}\n"
        )
        found = {k: c for _, k, c in cp.claims(draft)}

        assert found["a_2024"] == "data, gathered from sensors on the asset"
        assert "models" not in found["a_2024"]

    def test_a_row_scores_on_its_own_words(self, isolated_config):
        _add_item("a_2024", parsed_text="shared semantic model heavy modelling effort\fpage two")
        path = config.CONTENT_DIR / "chapter.tex"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.TABULAR)

        report = cp.build_report(path)
        row = next(f for f in report.findings if f.citekey == "a_2024")
        assert row.score >= config.PROVENANCE_GOOD_SCORE

    def test_an_item_opened_on_the_environment_line_drops_its_marker(self, isolated_config):
        draft = "\\begin{itemize} \\item data from sensors \\citep{a_2024}\n"
        (_, _, claim), = cp.claims(draft)
        assert claim == "data from sensors"

    def test_a_sectioning_command_is_not_part_of_the_paragraph_below(self, isolated_config):
        draft = (
            "\\section{Standards and interoperability}\n"
            "The prose beneath it, with no blank line between \\citep{a_2024}.\n"
        )
        (_, _, claim), = cp.claims(draft)
        assert claim == "The prose beneath it, with no blank line between."

    def test_latex_prose_is_untouched_by_block_splitting(self, isolated_config):
        """The regression guard on the other side: a hard-wrapped LaTeX
        paragraph is still read whole, then split into sentences."""
        draft = (
            "Simulation has become a cornerstone of developing\n"
            "and validating these systems \\citep{a_2024}. A second\n"
            "sentence follows it.\n"
        )
        (_, _, claim), = cp.claims(draft)
        assert claim == "Simulation has become a cornerstone of developing and validating these systems."


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
        from src import render_output

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
        from src import render_output

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

"""src/citation_coverage.py: how much of retrieval's candidates actually
made it into a draft's citations. Informational only, not a gate."""

from src import citation_coverage, ledger

from tests.conftest import make_reference


class TestCitedCitekeys:
    def test_extracts_pandoc_and_latex_citations(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Digital twins [@smith_2024] enable X. \\citep{jones_2023}\n")
        assert citation_coverage.cited_citekeys(draft) == {"smith_2024", "jones_2023"}

    def test_empty_draft_has_no_citations(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("No citations here.\n")
        assert citation_coverage.cited_citekeys(draft) == set()


class TestComputeCoverage:
    def test_full_coverage_when_every_candidate_cited(self, ledger_con, tmp_path):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        draft = tmp_path / "draft.md"
        draft.write_text("Composable twins [@a2024] are useful.\n")

        result = citation_coverage.compute_coverage(draft, ["digital twin composability"])

        assert result.candidates == {"a2024": "Digital Twin Composability"}
        assert result.cited_candidates == {"a2024"}
        assert result.uncited_candidates == set()
        assert result.coverage_pct == 100.0

    def test_partial_coverage_lists_uncited_candidates(self, ledger_con, tmp_path):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="b2024", title="Digital Twin Simulation"))
        draft = tmp_path / "draft.md"
        draft.write_text("Composable twins [@a2024] are useful.\n")

        result = citation_coverage.compute_coverage(draft, ["digital twin"])

        assert result.cited_candidates == {"a2024"}
        assert result.uncited_candidates == {"b2024"}
        assert result.coverage_pct == 50.0

    def test_no_candidates_gives_none_coverage(self, ledger_con, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Nothing relevant [@a2024].\n")

        result = citation_coverage.compute_coverage(draft, ["completely unrelated nonsense query"])

        assert result.candidates == {}
        assert result.coverage_pct is None
        # still reports the citation even though no candidate surfaced it
        assert result.cited_outside_candidates == {"a2024"}

    def test_cited_outside_candidates_tracked_separately(self, ledger_con, tmp_path):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        draft = tmp_path / "draft.md"
        draft.write_text("[@a2024] and also [@never_retrieved_2024].\n")

        result = citation_coverage.compute_coverage(draft, ["digital twin composability"])

        assert result.cited_outside_candidates == {"never_retrieved_2024"}
        assert result.uncited_candidates == set()


class TestFormatReport:
    def test_reports_coverage_percentage_and_gaps(self, ledger_con, tmp_path):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="b2024", title="Digital Twin Simulation"))
        draft = tmp_path / "draft.md"
        draft.write_text("Composable twins [@a2024] are useful.\n")

        result = citation_coverage.compute_coverage(draft, ["digital twin"])
        report = citation_coverage.format_report(draft, ["digital twin"], result)

        assert "Coverage: 50%" in report
        assert "b2024: Digital Twin Simulation" in report

    def test_reports_citations_outside_candidates(self, ledger_con, tmp_path):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        draft = tmp_path / "draft.md"
        draft.write_text("[@a2024] and also [@never_retrieved_2024].\n")

        result = citation_coverage.compute_coverage(draft, ["digital twin composability"])
        report = citation_coverage.format_report(draft, ["digital twin composability"], result)

        assert "Cited but not surfaced by these queries" in report
        assert "never_retrieved_2024" in report

    def test_reports_no_candidates_message(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Nothing here.\n")
        result = citation_coverage.CoverageResult()
        report = citation_coverage.format_report(draft, ["x"], result)
        assert "No candidates found" in report


class TestMain:
    def test_main_prints_report_and_returns_zero(self, ledger_con, tmp_path, capsys):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        draft = tmp_path / "draft.md"
        draft.write_text("Composable twins [@a2024] are useful.\n")

        rc = citation_coverage.main([str(draft), "--query", "digital twin composability"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Coverage: 100%" in out

    def test_main_supports_repeated_query_and_custom_k(self, ledger_con, tmp_path, capsys):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin Composability"))
        draft = tmp_path / "draft.md"
        draft.write_text("[@a2024]\n")

        rc = citation_coverage.main([
            str(draft), "--query", "digital twin", "--query", "composability", "--k", "1",
        ])

        assert rc == 0

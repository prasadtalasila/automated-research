"""src/citation_gate.py: the hard invariant (CLAUDE.md) -- a citekey may
only be used if it's actually in the ledger. This is the single most
important module in the repo to test thoroughly."""

import subprocess
import sys
from pathlib import Path

import pytest

from src import citation_gate, ledger

from tests.conftest import make_reference


class TestExtractLatexCitations:
    @pytest.mark.parametrize("cmd", [
        "cite", "citep", "citet", "parencite", "textcite", "autocite", "citeauthor", "citeyear",
    ])
    def test_all_recognized_commands(self, cmd):
        assert citation_gate.extract_citekeys_from_line(f"\\{cmd}{{smith2024}}") == ["smith2024"]

    def test_starred_variant(self):
        assert citation_gate.extract_citekeys_from_line("\\citep*{smith2024}") == ["smith2024"]

    def test_multiple_keys_comma_separated(self):
        assert citation_gate.extract_citekeys_from_line("\\cite{a2024,b2024}") == ["a2024", "b2024"]

    def test_multiple_keys_with_spaces(self):
        assert citation_gate.extract_citekeys_from_line("\\cite{a2024, b2024}") == ["a2024", "b2024"]

    def test_optional_bracket_args_before_key(self):
        assert citation_gate.extract_citekeys_from_line("\\citep[see][p.\\ 5]{smith2024}") == ["smith2024"]

    def test_unrelated_command_not_matched(self):
        assert citation_gate.extract_citekeys_from_line("\\section{Introduction}") == []

    def test_plain_text_no_match(self):
        assert citation_gate.extract_citekeys_from_line("Just some prose.") == []


class TestExtractPandocCitations:
    def test_bracketed_single_key(self):
        assert citation_gate.extract_citekeys_from_line("Some claim [@smith2024].") == ["smith2024"]

    def test_bracketed_multiple_keys(self):
        assert citation_gate.extract_citekeys_from_line("[@a2024; @b2024]") == ["a2024", "b2024"]

    def test_bare_at_key(self):
        assert citation_gate.extract_citekeys_from_line("As @smith2024 showed...") == ["smith2024"]

    def test_suppress_author_form(self):
        assert citation_gate.extract_citekeys_from_line("Smith (-@smith2024) showed...") == ["smith2024"]

    def test_key_with_hyphens_and_underscores(self):
        assert citation_gate.extract_citekeys_from_line(
            "[@jacoby_open-source_2023]"
        ) == ["jacoby_open-source_2023"]

    def test_key_with_double_hyphen(self):
        assert citation_gate.extract_citekeys_from_line(
            "[@zech_digital-twins-as--service_2024]"
        ) == ["zech_digital-twins-as--service_2024"]

    def test_email_address_not_mistaken_for_citation(self):
        assert citation_gate.extract_citekeys_from_line(
            "Contact us at name@example.com for details."
        ) == []

    def test_email_alongside_real_citation(self):
        line = "See [@smith2024] or email name@example.com."
        assert citation_gate.extract_citekeys_from_line(line) == ["smith2024"]

    def test_mixed_latex_and_pandoc_on_one_line(self):
        line = "As shown \\citep{a2024} and also [@b2024]."
        assert citation_gate.extract_citekeys_from_line(line) == ["a2024", "b2024"]


class TestCheckDocument:
    def test_reports_unknown_with_correct_line_numbers(self, tmp_path):
        path = tmp_path / "draft.md"
        path.write_text("Line one [@known2024].\nLine two [@unknown2024].\n")
        result = citation_gate.check_document(path, known_citekeys={"known2024"})

        assert result.total_citations == 2
        assert result.unknown == [(2, "unknown2024")]
        assert result.ok is False

    def test_all_known_is_ok(self, tmp_path):
        path = tmp_path / "draft.md"
        path.write_text("[@a2024] and [@b2024]\n")
        result = citation_gate.check_document(path, known_citekeys={"a2024", "b2024"})
        assert result.ok is True
        assert result.total_citations == 2

    def test_no_citations_is_ok_with_zero_total(self, tmp_path):
        path = tmp_path / "draft.md"
        path.write_text("Just prose, no citations at all.\n")
        result = citation_gate.check_document(path, known_citekeys=set())
        assert result.ok is True
        assert result.total_citations == 0


class TestRun:
    def test_empty_ledger_warns_and_fails(self, isolated_config, tmp_path, capsys):
        ledger.connect().close()
        path = tmp_path / "draft.md"
        path.write_text("[@smith2024]\n")

        rc = citation_gate.run([str(path)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "WARNING: ledger is empty" in captured.err
        assert "FAIL" in captured.out
        assert "@smith2024 not found in ledger" in captured.out

    def test_known_citekey_passes(self, isolated_config, tmp_path, capsys):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        path = tmp_path / "draft.md"
        path.write_text("[@smith2024]\n")
        rc = citation_gate.run([str(path)])
        out = capsys.readouterr().out

        assert rc == 0
        assert "OK" in out
        assert "1 citation(s), all verified" in out

    def test_multiple_files_mixed_result(self, isolated_config, tmp_path, capsys):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        good = tmp_path / "good.md"
        good.write_text("[@smith2024]\n")
        bad = tmp_path / "bad.md"
        bad.write_text("[@fabricated2024]\n")

        rc = citation_gate.run([str(good), str(bad)])
        out = capsys.readouterr().out

        assert rc == 1
        assert f"OK    {good}" in out
        assert f"FAIL  {bad}" in out


class TestCliEntrypoint:
    def test_no_args_prints_usage_and_exits_2(self, isolated_config):
        result = subprocess.run(
            [sys.executable, "-m", "src.citation_gate"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "usage:" in result.stderr

    def test_runs_with_bare_system_python3_no_bibtexparser(self, system_python, isolated_config, tmp_path):
        """CLAUDE.md's hard requirement: citation_gate must run with the
        bare system interpreter, no bibtexparser/venv needed."""
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text("[@smith2024]\n")

        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [system_python, "-m", "src.citation_gate", str(draft)],
            cwd=str(repo_root),
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "CONTENT_DIR": str(isolated_config.CONTENT_DIR)},
        )
        assert "bibtexparser" not in (result.stderr or "").lower()
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

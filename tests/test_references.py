"""src/references.py: auto-generated References sections, built only
from citekeys a draft already cites (never inventing one)."""

import subprocess
import sys
from pathlib import Path

import pytest

from src import ledger, references

from tests.conftest import make_reference


class TestUsedCitekeys:
    def test_dedupes_and_sorts(self):
        text = "[@zebra2024] and \\citep{apple2024} and [@zebra2024] again"
        assert references.used_citekeys(text) == ["apple2024", "zebra2024"]

    def test_no_citations(self):
        assert references.used_citekeys("just prose") == []


class TestHasSection:
    @pytest.mark.parametrize("heading", [
        "## References", "# References", "###### References",
        "## 6. References", "## 6) References", "## references",
    ])
    def test_matches_various_heading_styles(self, heading):
        assert references.has_section(f"Some text\n\n{heading}\n\nmore\n")

    def test_no_match_for_unrelated_heading(self):
        assert not references.has_section("## Introduction\n\nSome text.\n")

    def test_no_match_for_references_mentioned_in_prose(self):
        assert not references.has_section("See the references cited above.\n")


class TestBuildSection:
    def test_builds_formatted_entries_in_given_order(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="b2024", title="B Paper", year="2024"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="A Paper", year="2023"))

        section = references.build_section(["b2024", "a2024"], ledger_con)
        lines = section.splitlines()
        assert lines[0] == "## References"
        assert "**b2024** -- B Paper (2024)." in section
        assert "**a2024** -- A Paper (2023)." in section
        # order follows the input list, not alphabetical
        assert section.index("b2024") < section.index("a2024")

    def test_custom_heading(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024"))
        section = references.build_section(["a2024"], ledger_con, heading="6. References")
        assert section.startswith("## 6. References")

    def test_missing_citekey_raises_keyerror(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024"))
        with pytest.raises(KeyError, match="fabricated2024"):
            references.build_section(["a2024", "fabricated2024"], ledger_con)


class TestApply:
    def test_no_citekeys_returns_message_and_leaves_file_untouched(self, isolated_config, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Just prose, nothing cited.\n")
        result = references.apply(draft)
        assert "nothing to do" in result
        assert draft.read_text() == "Just prose, nothing cited.\n"

    def test_appends_section_when_none_exists(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="A Paper", year="2024"))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text("Body text citing [@smith2024].\n")
        result = references.apply(draft)

        text = draft.read_text()
        assert "wrote References section with 1 citekey(s)" in result
        assert "## References" in text
        assert "smith2024** -- A Paper (2024)" in text
        assert text.index("Body text") < text.index("## References")

    def test_replaces_existing_section_idempotently(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="A Paper", year="2024"))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text(
            "Body text citing [@smith2024].\n\n## References\n\n- stale entry\n"
        )
        references.apply(draft)
        first_pass = draft.read_text()
        assert first_pass.count("## References") == 1
        assert "stale entry" not in first_pass

        # Re-running is idempotent: still exactly one section, same content.
        references.apply(draft)
        second_pass = draft.read_text()
        assert second_pass.count("## References") == 1
        assert second_pass == first_pass


class TestMainCli:
    def test_success_prints_result_and_returns_0(self, isolated_config, tmp_path, capsys):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text("[@smith2024]\n")

        sys.argv = ["references.py", str(draft)]
        rc = references.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert "wrote References section" in out

    def test_missing_citekey_prints_error_and_returns_1(self, isolated_config, tmp_path, capsys):
        draft = tmp_path / "draft.md"
        draft.write_text("[@fabricated2024]\n")

        sys.argv = ["references.py", str(draft)]
        rc = references.main()
        err = capsys.readouterr().err
        assert rc == 1
        assert "[error]" in err
        assert "fabricated2024" in err

    def test_runs_with_bare_system_python3(self, system_python, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="A Paper", year="2024"))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text("Citing [@smith2024] here.\n")

        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [system_python, "-m", "src.references", str(draft)],
            cwd=str(repo_root),
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "CONTENT_DIR": str(isolated_config.CONTENT_DIR)},
        )
        assert result.returncode == 0, result.stderr
        assert "wrote References section" in result.stdout

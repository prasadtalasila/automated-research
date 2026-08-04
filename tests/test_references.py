"""src/references.py: auto-generated References sections, built only
from citekeys a draft already cites (never inventing one)."""

import subprocess
import sys
from pathlib import Path

import pytest

from src import ledger, references

from tests.conftest import make_reference


class TestUsedCitekeys:
    def test_dedupes_and_keeps_first_appearance_order(self):
        # Not sorted: the numbers this list gets have to match the ones
        # pandoc's citeproc assigns, and citeproc numbers by first
        # appearance. Sorted order would put apple2024 first here and
        # disagree with the rendered PDF.
        text = "[@zebra2024] and \\citep{apple2024} and [@zebra2024] again"
        assert references.used_citekeys(text) == ["zebra2024", "apple2024"]

    def test_no_citations(self):
        assert references.used_citekeys("just prose") == []

    def test_ignores_a_citekey_inside_a_code_span(self):
        # This is what lets build_section label each entry with `key`
        # without those labels reading back as citations on a re-run.
        assert references.used_citekeys("[1] A Paper, 2024. `ghost2024`") == []


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

    def test_no_match_for_a_heading_inside_a_code_fence(self):
        # A tutorial that shows a "## References" line in an example
        # would otherwise have everything below it replaced by apply()
        # and stripped from the render.
        draft = "# Lesson\n\n```markdown\n## References\n- an example\n```\n\nMore lesson.\n"
        assert not references.has_section(draft)

    def test_finds_a_real_heading_after_a_code_fence(self):
        draft = "# Lesson\n\n```markdown\n## References\n```\n\n## References\n\n[1] X. `k`\n"
        lines = draft.splitlines(keepends=True)
        assert lines[references.section_start(lines)].strip() == "## References"


class TestFormatEntry:
    def test_article_is_quoted_inside_an_italic_journal(self):
        entry = references.format_entry(
            "k", "A Study", "2024",
            {"author": "Doe, Jane", "journal": "J. Things", "volume": "2", "pages": "1--9"},
        )
        # One comma between title and journal, not two -- IEEE's comma
        # lives inside the closing quote.
        assert entry == 'J. Doe, "A Study," *J. Things*, vol. 2, pp. 1–9, 2024.'

    def test_work_with_no_container_is_italic_and_unquoted(self):
        entry = references.format_entry("k", "A Whole Book", "2020", {"author": "Doe, Jane", "publisher": "MIT Press"})
        assert entry == "J. Doe, *A Whole Book*, MIT Press, 2020."

    def test_proceedings_paper_gets_in_prefix(self):
        entry = references.format_entry("k", "A Paper", "2021", {"author": "Doe, Jane", "booktitle": "Proc. Conf."})
        assert 'in *Proc. Conf.*' in entry

    @pytest.mark.parametrize("author,expected", [
        ("Doe, Jane", "J. Doe"),
        ("Jane Doe", "J. Doe"),
        ("Doe, Jane Mary", "J. M. Doe"),
        # IEEE initializes both halves of a hyphenated given name.
        ("Smith, Jean-Paul", "J.-P. Smith"),
        ("A, X and B, Y", "X. A and Y. B"),
        ("A, X and B, Y and C, Z", "X. A, Y. B, and Z. C"),
        # A braced corporate author is one unit, never initialized.
        ("{IEEE Standards Association}", "IEEE Standards Association"),
    ])
    def test_author_lists(self, author, expected):
        assert references.format_entry("k", "T", "2024", {"author": author, "journal": "J"}).startswith(expected + ",")

    def test_more_than_six_authors_collapses_to_et_al(self):
        author = " and ".join(f"Last{i}, First{i}" for i in range(7))
        entry = references.format_entry("k", "T", "2024", {"author": author, "journal": "J"})
        assert entry.startswith("F. Last0 et al.,")

    def test_editor_is_used_when_there_is_no_author(self):
        entry = references.format_entry("k", "A Volume", "2015", {"editor": "Ed, One", "publisher": "Springer"})
        assert entry.startswith("O. Ed, Eds.,")

    def test_single_page_uses_p_not_pp(self):
        assert "p. 7," in references.format_entry("k", "T", "2024", {"journal": "J", "pages": "7"})

    def test_markdown_emphasis_in_a_value_is_escaped(self):
        # An unescaped underscore or asterisk would italicize part of the
        # reference list, making the rendered entry differ from the bib.
        entry = references.format_entry("k", "The C_str_ and A*B Problem", "2024", {})
        assert r"C\_str\_" in entry and r"A\*B" in entry

    def test_entry_with_nothing_but_a_citekey_still_renders(self):
        assert references.format_entry("k", "", "", {}) == "k."


class TestBuildSection:
    def test_builds_formatted_entries_in_given_order(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="b2024", title="B Paper", year="2024"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="A Paper", year="2023"))

        section = references.build_section(["b2024", "a2024"], ledger_con)
        lines = section.splitlines()
        assert lines[0] == "## References"
        assert "[1] *B Paper*, 2024. `b2024`" in section
        assert "[2] *A Paper*, 2023. `a2024`" in section
        # order follows the input list, not alphabetical
        assert section.index("b2024") < section.index("a2024")

    def test_entry_uses_the_full_bib_fields_when_the_ledger_has_them(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(
            citekey="doe2024", title="Digital Twins as a Service", year="2024",
            fields={
                "author": "Doe, Jane and Roe, Richard",
                "journal": "IEEE Trans. Testing",
                "volume": "3", "number": "2", "pages": "11--20",
                # Dropped by ledger._BIB_FIELDS_KEPT rather than formatted.
                "abstract": "Not part of a reference entry.",
            },
        ))
        section = references.build_section(["doe2024"], ledger_con)
        assert (
            '[1] J. Doe and R. Roe, "Digital Twins as a Service," '
            "*IEEE Trans. Testing*, vol. 3, no. 2, pp. 11–20, 2024. `doe2024`"
        ) in section
        assert "abstract" not in section.lower()

    def test_entry_falls_back_to_title_and_year_without_bib_fields(self, ledger_con):
        # A row synced before the bib_fields column existed: thinner, but
        # still a true entry, and the next sync fills it in.
        ledger.upsert_reference(ledger_con, make_reference(citekey="bare2024", title="A Paper", year="2024"))
        ledger_con.execute("UPDATE items SET bib_fields = NULL WHERE citekey = 'bare2024'")
        section = references.build_section(["bare2024"], ledger_con)
        assert "[1] *A Paper*, 2024. `bare2024`" in section

    def test_entry_survives_unparseable_bib_fields(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="bad2024", title="A Paper", year="2024"))
        ledger_con.execute("UPDATE items SET bib_fields = 'not json' WHERE citekey = 'bad2024'")
        section = references.build_section(["bad2024"], ledger_con)
        assert "[1] *A Paper*, 2024. `bad2024`" in section

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
        assert "[1] *A Paper*, 2024. `smith2024`" in text
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

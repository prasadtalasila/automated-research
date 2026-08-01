"""src/bib_reader.py: the only module that reads bibliography.bib, and
the only place a citekey should ever originate from (AGENTS.md)."""

from pathlib import Path

import pytest

from src import bib_reader


def write_bib(path, body):
    path.write_text(body, encoding="utf-8")


class TestParseAuthors:
    def test_last_comma_first(self):
        assert bib_reader._parse_authors("Smith, Jane") == [("Jane", "Smith")]

    def test_first_last_no_comma(self):
        assert bib_reader._parse_authors("Jane Smith") == [("Jane", "Smith")]

    def test_single_name_no_space(self):
        assert bib_reader._parse_authors("Cher") == [("", "Cher")]

    def test_multiple_authors(self):
        result = bib_reader._parse_authors("Smith, Jane and Doe, John")
        assert result == [("Jane", "Smith"), ("John", "Doe")]

    def test_empty_field(self):
        assert bib_reader._parse_authors("") == []

    def test_stray_whitespace_and_empty_segments(self):
        assert bib_reader._parse_authors("Smith, Jane and  and Doe, John") == [
            ("Jane", "Smith"), ("John", "Doe"),
        ]


class TestCleanTitle:
    def test_strips_braces(self):
        assert bib_reader._clean_title("{Digital} Twins in {P4} Medicine") == "Digital Twins in P4 Medicine"

    def test_no_braces_unchanged(self):
        assert bib_reader._clean_title("Plain Title") == "Plain Title"


class TestResolvePdfPath:
    def test_single_pdf_attachment_relative(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        field = f"paper.pdf:paper.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (str(pdf), bib_reader.PDF_RESOLVED)

    def test_absolute_path(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        field = f"paper.pdf:{pdf}:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path / "unrelated") == (
            str(pdf), bib_reader.PDF_RESOLVED,
        )

    def test_nonexistent_pdf_reports_pdf_path_gone(self, tmp_path):
        field = "paper.pdf:missing.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (None, bib_reader.PDF_PATH_GONE)

    def test_path_pointing_at_a_directory_reports_pdf_path_gone(self, tmp_path):
        # Regression (PR #6 review): Path.exists() is also true for
        # directories, not just files -- a bib export with an
        # empty/incorrect path that happens to resolve to an existing
        # directory must not be classified as PDF_RESOLVED (sync would
        # then try to run pdftotext on a directory).
        a_dir = tmp_path / "not_a_file.pdf"
        a_dir.mkdir()
        field = "paper.pdf:not_a_file.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (None, bib_reader.PDF_PATH_GONE)

    def test_non_pdf_mime_reports_non_pdf_attachment(self, tmp_path):
        html = tmp_path / "page.html"
        html.write_text("<html></html>")
        field = "page.html:page.html:text/html"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (
            None, bib_reader.PDF_NON_PDF_ATTACHMENT,
        )

    def test_non_html_non_pdf_mime_also_reports_non_pdf_attachment(self, tmp_path):
        # Regression (PR #6 review): detection only ever checked for the
        # *absence* of a pdf-mime entry, never the presence of text/html
        # specifically -- any other non-PDF mime (plain text here) must
        # land in the same bucket, not be silently unclassified.
        note = tmp_path / "notes.txt"
        note.write_text("some notes")
        field = "notes.txt:notes.txt:text/plain"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (
            None, bib_reader.PDF_NON_PDF_ATTACHMENT,
        )

    def test_multiple_attachments_picks_the_pdf(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        html = tmp_path / "page.html"
        html.write_text("<html></html>")
        field = f"page.html:page.html:text/html;paper.pdf:paper.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (str(pdf), bib_reader.PDF_RESOLVED)

    def test_malformed_field_too_few_parts_reports_malformed(self, tmp_path):
        assert bib_reader._resolve_pdf_path("just-a-filename.pdf", tmp_path) == (
            None, bib_reader.PDF_MALFORMED_FILE_FIELD,
        )

    def test_pdf_mime_but_missing_file_wins_over_non_pdf_attachment(self, tmp_path):
        # A pdf-mime attachment whose file has since moved/been deleted is
        # a more actionable signal (this item once had a real PDF) than
        # "only ever had a non-PDF attachment" -- when both are present,
        # report the former.
        html = tmp_path / "page.html"
        html.write_text("<html></html>")
        field = "page.html:page.html:text/html;paper.pdf:missing.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (None, bib_reader.PDF_PATH_GONE)

    def test_path_containing_colons_is_reassembled(self, tmp_path, monkeypatch):
        # Windows-style or otherwise colon-bearing paths: the middle
        # segment must be rejoined with ":", not just taken as parts[1].
        # Can't literally create a file named "a:b.pdf" to prove this
        # against -- a bare colon in a filename is illegal on Windows
        # (reserved for the drive-letter/ADS syntax), not just in the
        # "C:fakepath" drive-letter-shaped case this comment used to call
        # out (confirmed by this repo's own Windows CI leg). Monkeypatch
        # Path.is_file instead of relying on a real file on disk, which
        # exercises the exact same split/rejoin logic portably.
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        pdf = tmp_path / "a:b.pdf"
        field = "desc:a:b.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == (str(pdf), bib_reader.PDF_RESOLVED)


class TestCountRawEntries:
    def test_counts_each_entry_block(self):
        text = """
@article{one_2024,
  title = {One},
}

@misc{two_2024,
  title = {Two},
}
"""
        assert bib_reader._count_raw_entries(text) == 2

    def test_excludes_comment_string_and_preamble_blocks(self):
        text = """
@comment{just a note, not an entry}

@string{someabbrev = {Some Journal}}

@preamble{"some latex preamble"}

@article{real_entry_2024,
  title = {Real Entry},
}
"""
        assert bib_reader._count_raw_entries(text) == 1

    def test_entry_type_matching_is_case_insensitive(self):
        text = "@ARTICLE{shouty_2024,\n  title = {Shouty},\n}\n"
        assert bib_reader._count_raw_entries(text) == 1

    def test_counts_paren_delimited_entries_too(self):
        # Regression (PR #8 review): BibTeX allows `@type(...)` as well as
        # `@type{...}` -- bibtexparser accepts both -- so a brace-only
        # pattern would under-count a file using the paren form, which
        # could hide a genuine drop instead of just risking a
        # false-positive on a good file.
        text = "@article(paren_2024,\n  title = {Paren Form},\n)\n"
        assert bib_reader._count_raw_entries(text) == 1

    def test_empty_text_counts_zero(self):
        assert bib_reader._count_raw_entries("") == 0


class TestReadLibrary:
    def test_missing_bib_file_raises(self, isolated_config):
        with pytest.raises(FileNotFoundError, match="No bib file"):
            bib_reader.read_library()

    def test_parses_basic_entry(self, isolated_config):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{smith_example_2024,
  title = {An {Example} Paper},
  author = {Smith, Jane and Doe, John},
  year = {2024},
  doi = {10.1234/example},
  url = {https://example.com/paper},
}
""",
        )
        refs = bib_reader.read_library()
        assert len(refs) == 1
        ref = refs[0]
        assert ref.citekey == "smith_example_2024"
        assert ref.item_type == "article"
        assert ref.title == "An Example Paper"
        assert ref.authors == [("Jane", "Smith"), ("John", "Doe")]
        assert ref.year == "2024"
        assert ref.doi == "10.1234/example"
        assert ref.url == "https://example.com/paper"
        assert ref.pdf_path is None

    def test_entry_without_author_has_empty_authors(self, isolated_config):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@misc{noauthor_page_nodate,
  title = {Some Web Page},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].authors == []
        assert refs[0].year == "n.d."

    def test_entry_with_pdf_file_field(self, isolated_config):
        pdf = isolated_config.BIB_FILE_PATH.parent / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{smith_example_2024,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
  file = {paper.pdf:paper.pdf:application/pdf},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].pdf_path == str(pdf)
        assert refs[0].pdf_resolution == bib_reader.PDF_RESOLVED

    def test_entry_without_file_field_reports_no_file_field(self, isolated_config):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{smith_example_2024,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].pdf_path is None
        assert refs[0].pdf_resolution == bib_reader.PDF_NO_FILE_FIELD

    def test_entry_with_html_only_snapshot_reports_non_pdf_attachment(self, isolated_config):
        html = isolated_config.BIB_FILE_PATH.parent / "page.html"
        html.write_text("<html></html>")
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{smith_example_2024,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
  file = {page.html:page.html:text/html},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].pdf_path is None
        assert refs[0].pdf_resolution == bib_reader.PDF_NON_PDF_ATTACHMENT

    def test_entry_with_pdf_path_gone_reports_pdf_path_gone(self, isolated_config):
        # bib file references a PDF that isn't actually on disk (moved,
        # deleted, or synced from a different machine's file layout).
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{smith_example_2024,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
  file = {paper.pdf:paper.pdf:application/pdf},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].pdf_path is None
        assert refs[0].pdf_resolution == bib_reader.PDF_PATH_GONE

    def test_multiple_entries(self, isolated_config):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{one_2024,
  title = {One},
  author = {A, One},
  year = {2024},
}

@article{two_2024,
  title = {Two},
  author = {B, Two},
  year = {2024},
}
""",
        )
        refs = bib_reader.read_library()
        assert {r.citekey for r in refs} == {"one_2024", "two_2024"}

    def test_no_warning_when_parsed_count_matches_raw_count(self, isolated_config, capsys):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{one_2024,
  title = {One},
  author = {A, One},
  year = {2024},
}

@article{two_2024,
  title = {Two},
  author = {B, Two},
  year = {2024},
}
""",
        )
        refs = bib_reader.read_library()
        out = capsys.readouterr().out
        assert len(refs) == 2
        assert "WARNING" not in out

    def test_no_warning_for_a_real_export_using_string_abbreviations(self, isolated_config, capsys):
        # read_library parses with common_strings=True, so a real export
        # legitimately using @string (a journal-name abbreviation, say)
        # is plausible -- it must not be miscounted as a dropped entry
        # just because _count_raw_entries and bibtexparser need to agree
        # on what "an entry" is.
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@string{jmlr = {Journal of Machine Learning Research}}

@article{one_2024,
  title = {One},
  author = {A, One},
  year = {2024},
  journal = jmlr,
}

@article{two_2024,
  title = {Two},
  author = {B, Two},
  year = {2024},
}
""",
        )
        refs = bib_reader.read_library()
        out = capsys.readouterr().out
        assert len(refs) == 2
        assert "WARNING" not in out

    def test_warns_when_a_malformed_entry_is_silently_dropped(self, isolated_config, capsys):
        # Regression: bibtexparser drops an entry it can't parse (here,
        # unbalanced braces in the title) with no exception and no trace
        # in bib_database.entries -- read_library must notice the raw
        # @entry count doesn't match what actually got parsed.
        write_bib(
            isolated_config.BIB_FILE_PATH,
            """
@article{good_2024,
  title = {Good Entry},
  author = {Smith, Jane},
  year = {2024},
}

@article{bad_2024,
  title = {Unbalanced {Braces},
  author = {Doe, John},
  year = {2023},
}
""",
        )
        refs = bib_reader.read_library()
        out = capsys.readouterr().out
        assert len(refs) == 1
        assert "WARNING: bibtexparser parsed 1 entries but" in out
        assert "bibliography.bib has 2 @entry block(s)" in out
        assert "1 may have been silently dropped" in out

    def test_unicode_conversion_applied(self, isolated_config):
        write_bib(
            isolated_config.BIB_FILE_PATH,
            r"""
@article{muller_2024,
  title = {{\"U}ber Zwillinge},
  author = {M{\"u}ller, Hans},
  year = {2024},
}
""",
        )
        refs = bib_reader.read_library()
        assert refs[0].authors == [("Hans", "Müller")]

"""src/bib_reader.py: the only module that reads bibliography.bib, and
the only place a citekey should ever originate from (AGENTS.md)."""

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
        assert bib_reader._resolve_pdf_path(field, tmp_path) == str(pdf)

    def test_absolute_path(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        field = f"paper.pdf:{pdf}:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path / "unrelated") == str(pdf)

    def test_nonexistent_pdf_returns_none(self, tmp_path):
        field = "paper.pdf:missing.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) is None

    def test_non_pdf_mime_skipped(self, tmp_path):
        html = tmp_path / "page.html"
        html.write_text("<html></html>")
        field = "page.html:page.html:text/html"
        assert bib_reader._resolve_pdf_path(field, tmp_path) is None

    def test_multiple_attachments_picks_the_pdf(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        html = tmp_path / "page.html"
        html.write_text("<html></html>")
        field = f"page.html:page.html:text/html;paper.pdf:paper.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == str(pdf)

    def test_malformed_field_too_few_parts_skipped(self, tmp_path):
        assert bib_reader._resolve_pdf_path("just-a-filename.pdf", tmp_path) is None

    def test_path_containing_colons_is_reassembled(self, tmp_path):
        # Windows-style or otherwise colon-bearing paths: the middle
        # segment must be rejoined with ":", not just taken as parts[1].
        sub = tmp_path / "C:fakepath"
        # Can't literally create a "C:fakepath" dir with a colon safely
        # on all filesystems; instead just prove the split/rejoin math
        # directly against a path with an extra ':' in a fields context
        # that still resolves against a real file.
        pdf = tmp_path / "a:b.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        field = f"desc:a:b.pdf:application/pdf"
        assert bib_reader._resolve_pdf_path(field, tmp_path) == str(pdf)


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

"""scripts/verbatim_check.py: the ad-hoc verbatim-overlap/page-locator
review aid (not part of the deterministic pipeline). REPO/BIB are
module-level constants computed from Path(__file__) at import time;
tests monkeypatch them directly to point at a throwaway fixture tree."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.verbatim_check as vc


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(vc, "REPO", tmp_path)
    monkeypatch.setattr(vc, "BIB", tmp_path / "bibliography.bib")
    monkeypatch.setattr(vc, "PARSED_DIR", tmp_path / "content" / "parsed")
    return tmp_path


class TestBibEntry:
    def test_finds_entry_by_citekey(self, fixture_repo):
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {A Paper},\n}\n"
            "@article{doe_2023,\n  title = {Another},\n}\n"
        )
        entry = vc.bib_entry("smith_2024")
        assert "smith_2024" in entry
        assert "A Paper" in entry
        assert "doe_2023" not in entry

    def test_missing_citekey_returns_empty(self, fixture_repo):
        vc.BIB.write_text("@article{smith_2024,\n  title = {A Paper},\n}\n")
        assert vc.bib_entry("nonexistent_2024") == ""

    def test_missing_bib_file_returns_empty_rather_than_raising(self, fixture_repo):
        assert not vc.BIB.exists()
        assert vc.bib_entry("anything_2024") == ""


class TestPdfPath:
    def test_resolves_pdf_from_file_field(self, fixture_repo):
        pdf = fixture_repo / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n  file = {paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_multiple_attachments_picks_the_pdf(self, fixture_repo):
        pdf = fixture_repo / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n"
            "  file = {page.html:page.html:text/html;paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_no_file_field_returns_none(self, fixture_repo):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        assert vc.pdf_path("smith_2024") is None

    def test_file_field_with_no_pdf_attachment_returns_none(self, fixture_repo):
        html = fixture_repo / "page.html"
        html.write_text("<html></html>")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n"
            "  file = {page.html:page.html:text/html},\n}\n"
        )
        assert vc.pdf_path("smith_2024") is None

    def test_pdf_referenced_but_missing_on_disk_returns_none(self, fixture_repo):
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n  file = {paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") is None

    def test_field_after_a_multiline_value_is_still_found(self, fixture_repo):
        """The second regression: bib_entry() stopped at the first "\\n}".
        A field whose closing brace sits at the start of a line -- an
        `annote` holding a URL is the real case -- truncated the entry
        there, hiding every later field including `file`. Braces are
        balanced; only the naive delimiter search was fooled. Cost 40
        papers, each of which did have a PDF on disk.
        """
        pdf = fixture_repo / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n"
            "\ttitle = {T},\n"
            "\tannote = {codebase: https://example.invalid/x\n"
            "},\n"
            "\tfile = {paper.pdf:paper.pdf:application/pdf},\n"
            "}\n"
        )
        assert "file =" in vc.bib_entry("smith_2024")
        assert vc.pdf_path("smith_2024") == pdf

    def test_html_attachment_before_pdf_still_finds_the_pdf(self, fixture_repo):
        """Mirrors the real export: an arXiv HTML snapshot is listed
        first, the PDF second."""
        sub = fixture_repo / "pdfs" / "159"
        sub.mkdir(parents=True)
        pdf = sub / "Lu et al. - 2023 - EvoCLINICAL.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n\tfile = {arXiv.org Snapshot:pdfs/158/2309.html:text/html;"
            "Submitted Version:pdfs/159/Lu et al. - 2023 - EvoCLINICAL.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_unbalanced_braces_returns_what_it_has(self, fixture_repo):
        """A truncated/corrupt .bib shouldn't hang or raise -- hand back
        the remainder and let the caller find no `file` field."""
        vc.BIB.write_text("@article{smith_2024,\n\ttitle = {T},\n")
        assert vc.bib_entry("smith_2024").startswith("@article{smith_2024,")
        assert vc.pdf_path("smith_2024") is None

    def test_description_differs_from_path(self, fixture_repo):
        """The regression: this project's export writes
        `Desc.pdf:real/path.pdf:application/pdf`. Taking the first
        segment ending in `.pdf` picks the description, which only
        resolves when it coincides with the path -- as it does in a flat
        fixture dir, which is why every other test here missed this.
        Lost 196 of 501 real PDFs."""
        sub = fixture_repo / "pdfs" / "21"
        sub.mkdir(parents=True)
        pdf = sub / "Smith - 2024 - Title.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n"
            "  file = {Smith - 2024 - Title.pdf:pdfs/21/Smith - 2024 - Title.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_absolute_path_in_file_field(self, fixture_repo, tmp_path):
        pdf = tmp_path / "abs.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n"
            f"  file = {{abs.pdf:{pdf}:application/pdf}},\n}}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_malformed_attachment_segment_is_skipped(self, fixture_repo):
        pdf = fixture_repo / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n"
            "  file = {junk;paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf

    def test_resolves_relative_to_bib_file_directory_not_repo_root(self, tmp_path, monkeypatch):
        # Regression: a relative attachment path must be anchored to
        # wherever BIB (== config.BIB_FILE_PATH, honoring a BIB_FILE
        # override) actually lives -- matching
        # src.bib_reader._resolve_pdf_path -- not the checked-out repo
        # root. A BIB_FILE pointing outside the repo used to silently fail
        # to find PDFs sitting right next to it.
        bib_dir = tmp_path / "elsewhere"
        bib_dir.mkdir()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        monkeypatch.setattr(vc, "REPO", repo_dir)
        monkeypatch.setattr(vc, "BIB", bib_dir / "bibliography.bib")

        pdf = bib_dir / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n  file = {paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        assert vc.pdf_path("smith_2024") == pdf


class TestPages:
    def test_falls_back_to_parsed_text_when_no_pdf(self, fixture_repo):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        parsed_dir = fixture_repo / "content" / "parsed"
        parsed_dir.mkdir(parents=True)
        (parsed_dir / "smith_2024.txt").write_text("page one text\x00\x01\fpage two text")

        result = vc.pages("smith_2024")
        assert result == ["page one text  page two text"] or len(result) == 2

    def test_no_pdf_and_no_parsed_text_returns_empty(self, fixture_repo):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        assert vc.pages("smith_2024") == []

    def test_parsed_fallback_respects_parsed_dir_override(self, tmp_path, monkeypatch):
        # Regression: the parsed-text fallback must look wherever
        # config.PARSED_DIR actually points (a CONTENT_DIR override) --
        # not a hardcoded REPO/content/parsed that ignores it.
        monkeypatch.setattr(vc, "REPO", tmp_path / "unrelated-repo")
        monkeypatch.setattr(vc, "BIB", tmp_path / "bibliography.bib")
        custom_parsed_dir = tmp_path / "custom-content" / "parsed"
        monkeypatch.setattr(vc, "PARSED_DIR", custom_parsed_dir)
        custom_parsed_dir.mkdir(parents=True)
        (custom_parsed_dir / "smith_2024.txt").write_text("page one text")

        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        assert vc.pages("smith_2024") == ["page one text"]

    @pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
    def test_real_pdf_via_pdftotext(self, fixture_repo):
        pandoc, pdflatex = shutil.which("pandoc"), shutil.which("pdflatex")
        if not (pandoc and pdflatex):
            pytest.skip("pandoc/pdflatex not installed")

        md = fixture_repo / "doc.md"
        md.write_text("# Title\n\nSome distinctive verbatim content here.\n")
        pdf = fixture_repo / "paper.pdf"
        subprocess.run(["pandoc", str(md), "-o", str(pdf), "--pdf-engine=pdflatex"], check=True, capture_output=True)

        vc.BIB.write_text(
            "@article{smith_2024,\n  title = {T},\n  file = {paper.pdf:paper.pdf:application/pdf},\n}\n"
        )
        result = vc.pages("smith_2024")
        assert any("distinctive verbatim content" in p for p in result)


class TestNorm:
    def test_tokenizes_lowercase_alnum(self):
        assert vc.norm("Hello, World! 123") == ["hello", "world", "123"]


class TestSentencesCiting:
    def test_returns_paragraphs_mentioning_citekey(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text(
            "Paragraph one mentions smith_2024 here.\n\n"
            "Paragraph two does not.\n\n"
            "Paragraph three also cites smith_2024 again.\n"
        )
        result = vc.sentences_citing(str(draft), "smith_2024")
        assert len(result) == 2
        assert all("smith_2024" in p for p in result)

    def test_no_matching_paragraphs(self, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("Nothing relevant here.\n")
        assert vc.sentences_citing(str(draft), "smith_2024") == []


class TestCmdOverlap:
    def test_no_source_text_prints_message(self, fixture_repo, capsys):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        draft = fixture_repo / "draft.md"
        draft.write_text("Some claim citing smith_2024.\n")
        vc.cmd_overlap(str(draft), "smith_2024")
        out = capsys.readouterr().out
        assert "no source text for smith_2024" in out

    def test_detects_verbatim_overlap_run(self, fixture_repo, capsys):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        parsed_dir = fixture_repo / "content" / "parsed"
        parsed_dir.mkdir(parents=True)
        shared_phrase = "the quick brown fox jumps over the lazy dog repeatedly"
        (parsed_dir / "smith_2024.txt").write_text(f"Intro text. {shared_phrase}. More text.")

        draft = fixture_repo / "draft.md"
        draft.write_text(f"As discussed [@smith_2024], {shared_phrase} in the study.\n")

        vc.cmd_overlap(str(draft), "smith_2024", n=4)
        out = capsys.readouterr().out
        assert "words, pdf p." in out

    def test_overlap_run_extending_to_end_of_sentence(self, fixture_repo, capsys):
        # The matching run must still be open (not yet flushed by a
        # non-matching n-gram) when the word list ends, exercising the
        # post-loop flush rather than the else-branch one.
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        parsed_dir = fixture_repo / "content" / "parsed"
        parsed_dir.mkdir(parents=True)
        shared_phrase = "the quick brown fox jumps over the lazy dog"
        (parsed_dir / "smith_2024.txt").write_text(f"Intro text. {shared_phrase}.")

        draft = fixture_repo / "draft.md"
        draft.write_text(f"As discussed [@smith_2024], {shared_phrase}\n")

        vc.cmd_overlap(str(draft), "smith_2024", n=4)
        out = capsys.readouterr().out
        assert "words, pdf p." in out

    def test_no_overlap_found(self, fixture_repo, capsys):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        parsed_dir = fixture_repo / "content" / "parsed"
        parsed_dir.mkdir(parents=True)
        (parsed_dir / "smith_2024.txt").write_text("completely different vocabulary entirely")

        draft = fixture_repo / "draft.md"
        draft.write_text("An original sentence mentioning smith_2024 with unrelated words.\n")

        vc.cmd_overlap(str(draft), "smith_2024", n=8)
        out = capsys.readouterr().out
        assert "no verbatim run of >= 8 words found" in out


class TestCmdLocate:
    def test_reports_best_matching_pages(self, fixture_repo, capsys):
        vc.BIB.write_text("@article{smith_2024,\n  title = {T},\n}\n")
        parsed_dir = fixture_repo / "content" / "parsed"
        parsed_dir.mkdir(parents=True)
        (parsed_dir / "smith_2024.txt").write_text("nothing relevant\fdigital twin simulation platform")

        vc.cmd_locate("smith_2024", "digital twin simulation")
        out = capsys.readouterr().out
        assert "smith_2024: 2 pdf pages" in out
        assert "digital twin simulation" in out


class TestCliDispatch:
    def test_overlap_mode_via_subprocess(self, tmp_path):
        repo_root = Path(__file__).resolve().parent.parent
        draft = tmp_path / "draft.md"
        draft.write_text("Some claim citing nonexistent_key_2024.\n")

        result = subprocess.run(
            [sys.executable, "scripts/verbatim_check.py", "overlap", str(draft), "nonexistent_key_2024"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "no source text for nonexistent_key_2024" in result.stdout

    def test_unknown_mode_prints_docstring(self, tmp_path):
        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "scripts/verbatim_check.py", "bogus-mode"],
            cwd=str(repo_root), capture_output=True, text=True,
        )
        assert "Ad-hoc plagiarism" in result.stdout

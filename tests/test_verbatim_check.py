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

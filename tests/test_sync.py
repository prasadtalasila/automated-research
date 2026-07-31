"""src/sync.py: the deterministic bib -> ledger -> parsed-text entrypoint
("job 1" -- CLAUDE.md). No LLM calls, must be idempotent."""

import subprocess

import pytest

from src import config, ledger, pdf_text, sync


def write_bib(path, body):
    path.write_text(body, encoding="utf-8")


BASIC_BIB = """
@article{smith_example_2024,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
  file = {paper.pdf:paper.pdf:application/pdf},
}

@misc{noauthor_page_nodate,
  title = {A Page With No Author},
}

@article{doe_broken_2023,
  title = {A Paper That Fails to Parse},
  author = {Doe, John},
  year = {2023},
  file = {broken.pdf:broken.pdf:application/pdf},
}
"""


@pytest.fixture
def basic_corpus(isolated_config):
    write_bib(isolated_config.BIB_FILE_PATH, BASIC_BIB)
    bib_dir = isolated_config.BIB_FILE_PATH.parent
    (bib_dir / "paper.pdf").write_bytes(b"%PDF-1.4 good content")
    (bib_dir / "broken.pdf").write_bytes(b"%PDF-1.4 broken content")
    return isolated_config


def fake_extract_text_factory(fail_citekeys=()):
    def fake_extract_text(pdf_path, citekey):
        if citekey in fail_citekeys:
            raise subprocess.CalledProcessError(1, ["pdftotext"], stderr=f"{citekey}: bad PDF")
        out_path = config.PARSED_DIR / f"{citekey}.txt"
        config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(f"extracted text for {citekey}")
        return out_path
    return fake_extract_text


class TestRun:
    def test_full_run_counts_and_return_code(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 1  # one failure -> nonzero exit
        assert "1 parsed, 0 unchanged, 1 without a PDF attachment, 1 failed" in out
        assert "parsed  smith_example_2024" in out

        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        assert rows["smith_example_2024"]["status"] == "parsed"
        assert rows["noauthor_page_nodate"]["status"] == "no_pdf"
        assert rows["doe_broken_2023"]["status"] == "parse_failed"

    def test_warns_about_missing_author_metadata(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        out = capsys.readouterr().out
        assert "WARNING: 1 item(s) have no author metadata" in out
        assert "noauthor_page_nodate" in out

    def test_second_run_is_idempotent_and_skips_unchanged(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()  # clear

        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "0 parsed, 2 unchanged, 1 without a PDF attachment, 0 failed" in out

    def test_previously_failed_parse_is_not_retried_while_pdf_unchanged(
        self, basic_corpus, monkeypatch, capsys
    ):
        # Documents current (if perhaps surprising) behavior: pdf_hash is
        # recorded on the *first* attempt regardless of parse outcome, so
        # a second run with the same PDF content sees needs_parse=False
        # and never retries -- only a change to the PDF's bytes does.
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        capsys.readouterr()

        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "0 parsed, 2 unchanged, 1 without a PDF attachment, 0 failed" in out
        con = ledger.connect()
        try:
            row = {r["citekey"]: r for r in ledger.all_items(con)}["doe_broken_2023"]
        finally:
            con.close()
        assert row["status"] == "parse_failed"

    def test_changed_pdf_bytes_triggers_reparse(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        (basic_corpus.BIB_FILE_PATH.parent / "paper.pdf").write_bytes(b"%PDF-1.4 NEW content, changed")
        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "1 parsed, 1 unchanged, 1 without a PDF attachment, 0 failed" in out

    def test_empty_bibliography(self, isolated_config, capsys):
        write_bib(isolated_config.BIB_FILE_PATH, "")
        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "found 0 bibliographic item(s)" in out
        assert "0 parsed, 0 unchanged, 0 without a PDF attachment, 0 failed" in out

"""src/sync.py: the deterministic bib -> ledger -> parsed-text entrypoint
("job 1" -- AGENTS.md). No LLM calls, must be idempotent."""

import subprocess
import sys
from pathlib import Path

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

    def test_warns_about_duplicate_titles(self, isolated_config, monkeypatch, capsys):
        write_bib(isolated_config.BIB_FILE_PATH, BASIC_BIB + """
@misc{smith_example_2024_dup,
  title = {An Example Paper},
  author = {Smith, Jane},
  year = {2024},
}
""")
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        out = capsys.readouterr().out
        assert "WARNING: 1 possible duplicate group(s)" in out
        assert "smith_example_2024" in out and "smith_example_2024_dup" in out

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

    def test_default_mode_reports_stale_citekey_but_does_not_remove_it(
        self, basic_corpus, monkeypatch, capsys
    ):
        # Default (no --remove-stale) must not delete anything -- a bib
        # file coming back short a citekey is far more often a mistake
        # (botched re-export, BIB_FILE pointing at the wrong path) than
        # an intentional removal, so the default is to report and let a
        # human confirm with --remove-stale rather than delete on every
        # routine sync.
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        write_bib(basic_corpus.BIB_FILE_PATH, BASIC_BIB.replace(
            "@misc{noauthor_page_nodate,\n  title = {A Page With No Author},\n}\n\n", ""
        ))
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 0
        assert "stale   noauthor_page_nodate" in out
        assert "1 stale (not removed)" in out
        assert "Review the 1 stale item(s) above" in out
        assert "--remove-stale to delete them" in out
        assert "pruned" not in out
        con = ledger.connect()
        try:
            known = ledger.known_citekeys(con)
        finally:
            con.close()
        assert known == {"smith_example_2024", "noauthor_page_nodate", "doe_broken_2023"}

    def test_remove_stale_flag_deletes_the_stale_citekey(self, basic_corpus, monkeypatch, capsys):
        # Without this, a citekey removed from bibliography.bib (the
        # source of truth) stays "known" to citation_gate forever --
        # AGENTS.md's fabricated-citekey invariant, just arriving via
        # deletion instead of invention. Only happens when a human opts in
        # via --remove-stale, though (see the default-mode test above).
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        write_bib(basic_corpus.BIB_FILE_PATH, BASIC_BIB.replace(
            "@misc{noauthor_page_nodate,\n  title = {A Page With No Author},\n}\n\n", ""
        ))
        rc = sync.run(remove_stale=True)
        out = capsys.readouterr().out

        assert rc == 0
        assert "pruned  noauthor_page_nodate" in out
        assert "1 pruned" in out
        con = ledger.connect()
        try:
            known = ledger.known_citekeys(con)
        finally:
            con.close()
        assert "noauthor_page_nodate" not in known
        assert known == {"smith_example_2024", "doe_broken_2023"}

    def test_no_removed_citekeys_prunes_nothing(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        rc = sync.run(remove_stale=True)
        out = capsys.readouterr().out
        assert rc == 0
        assert "0 pruned" in out
        assert "  pruned  " not in out

    def test_no_removed_citekeys_reports_nothing_stale_in_default_mode(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "0 stale (not removed)" in out
        assert "  stale   " not in out

    def test_bib_yielding_zero_refs_warns_instead_of_suggesting_remove_stale(
        self, basic_corpus, monkeypatch, capsys
    ):
        # Default mode never deletes, so a bib file that comes back
        # completely empty (truncated/corrupted re-export, BIB_FILE
        # pointing at the wrong path) must not be reported with the
        # ordinary "re-run with --remove-stale" hint -- following that
        # advice would hit prune_missing's guard and raise. Must instead
        # warn that this looks like a bad export, without ever
        # recommending the flag for this specific shape.
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        write_bib(basic_corpus.BIB_FILE_PATH, "")
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 0
        assert "SUSPICIOUS" in out
        assert "3 ledger item(s)" in out
        assert "Review the" not in out
        assert "re-run with --remove-stale" not in out
        assert "3 stale (not removed)" in out
        con = ledger.connect()
        try:
            known = ledger.known_citekeys(con)
        finally:
            con.close()
        assert known == {"smith_example_2024", "noauthor_page_nodate", "doe_broken_2023"}

    def test_remove_stale_refuses_to_wipe_a_populated_ledger_on_zero_refs(
        self, basic_corpus, monkeypatch, capsys
    ):
        # A bib file that exists and parses cleanly but yields 0 entries
        # (truncated/corrupted re-export, BIB_FILE pointing at the wrong
        # path) must not be treated the same as "every citekey was
        # legitimately removed" -- see ledger.prune_missing's guard. Without
        # it, --remove-stale would silently empty the ledger and
        # citation_gate would report every citekey in every existing draft
        # as fabricated.
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        write_bib(basic_corpus.BIB_FILE_PATH, "")
        with pytest.raises(RuntimeError, match="Refusing to prune"):
            sync.run(remove_stale=True)

        con = ledger.connect()
        try:
            known = ledger.known_citekeys(con)
        finally:
            con.close()
        assert known == {"smith_example_2024", "noauthor_page_nodate", "doe_broken_2023"}


    def test_missing_pdftotext_is_reported_not_raised(self, basic_corpus, monkeypatch, capsys):
        # Regression: a host without poppler-utils installed used to
        # propagate subprocess.run's bare FileNotFoundError as an
        # uncaught traceback (only CalledProcessError was ever caught
        # here) instead of being probed and reported honestly, the way
        # every src/heavy/* stage already handles a missing binary.
        monkeypatch.setattr(pdf_text, "is_available", lambda: False)
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 1  # items needed parsing but couldn't -- not a silent success
        assert "WARNING: 'pdftotext' not found on PATH" in out
        assert "2 skipped (pdftotext not installed)" in out
        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        # Bibliographic metadata is still synced even though parsing was skipped.
        assert rows["smith_example_2024"]["status"] == "discovered"
        assert rows["doe_broken_2023"]["status"] == "discovered"

    def test_missing_pdftotext_with_nothing_needing_parse_is_a_clean_run(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        monkeypatch.setattr(pdf_text, "is_available", lambda: False)
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 0  # nothing actually needed pdftotext this run
        assert "WARNING: 'pdftotext' not found on PATH" in out
        assert "0 skipped (pdftotext not installed)" not in out
        assert "skipped (pdftotext not installed)" not in out


class TestCliEntrypoint:
    def test_remove_stale_flag_is_registered(self, isolated_config):
        result = subprocess.run(
            [sys.executable, "-m", "src.sync", "--help"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--remove-stale" in result.stdout

    def test_unknown_flag_is_rejected(self, isolated_config):
        result = subprocess.run(
            [sys.executable, "-m", "src.sync", "--bogus-flag"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr

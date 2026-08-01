"""src/sync.py: the deterministic bib -> ledger -> parsed-text entrypoint
("job 1" -- AGENTS.md). No LLM calls, must be idempotent."""

import subprocess
import sys
from pathlib import Path

import pytest

from src import config, ledger, pdf_text, sync


def write_bib(path, body):
    path.write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def _pdftotext_present_by_default(monkeypatch):
    # Every test in this file exercises sync's own parse-loop logic via a
    # mocked pdf_text.extract_text, not the real pdftotext binary -- but
    # sync.run() now probes pdf_text.is_available() before that loop
    # (src/pdf_text.py's missing-binary handling), so without this these
    # tests would silently depend on pdftotext actually being on PATH on
    # whatever host runs them (true here, but os-deps -- the stage that
    # installs poppler-utils -- is explicitly opt-in per AGENTS.md, and
    # test_pdf_text.py already contemplates hosts where it isn't). The
    # dedicated test_missing_pdftotext_* tests below override this back
    # to False afterward -- monkeypatch is last-write-wins within a test.
    monkeypatch.setattr(pdf_text, "is_available", lambda: True)


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
            raise pdf_text.ExtractionError(f"{citekey}: bad PDF")
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

    def test_warns_when_bib_reader_drops_a_malformed_entry(self, isolated_config, monkeypatch, capsys):
        # bib_reader.read_library() prints this warning itself (it's the
        # only place with both the raw file text and the parsed count) --
        # pin that it actually reaches sync's own output, not just
        # read_library()'s own direct tests.
        write_bib(isolated_config.BIB_FILE_PATH, BASIC_BIB + """
@article{malformed_2024,
  title = {Unbalanced {Braces},
  author = {Roe, Jan},
  year = {2022},
}
""")
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        out = capsys.readouterr().out
        assert "WARNING: bibtexparser parsed 3 entries but" in out
        assert "1 may have been silently dropped" in out

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

    def test_no_pdf_breakdown_distinguishes_the_failure_reasons(
        self, isolated_config, monkeypatch, capsys
    ):
        # Regression: all of "no file field", "PDF path gone", "non-PDF
        # attachment only", and "malformed file field" used to collapse
        # into one opaque "N without a PDF attachment" bucket -- masking
        # which items were silently missing a PDF the bib file still
        # claims to have, and which were invisible to retrieval because
        # only a non-PDF (e.g. HTML) attachment was ever saved.
        write_bib(isolated_config.BIB_FILE_PATH, """
@misc{no_file_field_2024,
  title = {No File Field At All},
}

@article{pdf_gone_2024,
  title = {PDF Path No Longer Exists},
  author = {Smith, Jane},
  year = {2024},
  file = {paper.pdf:paper.pdf:application/pdf},
}

@article{html_only_2024,
  title = {Only An HTML Snapshot},
  author = {Doe, John},
  year = {2024},
  file = {page.html:page.html:text/html},
}

@article{malformed_2024,
  title = {Malformed File Field},
  author = {Roe, Jan},
  year = {2024},
  file = {just-a-filename-no-colons},
}
""")
        html = isolated_config.BIB_FILE_PATH.parent / "page.html"
        html.write_text("<html></html>")

        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 0
        assert "4 without a PDF attachment" in out
        assert "no-pdf  no_file_field_2024: no file field in bib entry" in out
        assert "no-pdf  pdf_gone_2024: PDF path no longer exists on disk" in out
        assert "no-pdf  html_only_2024: non-PDF attachment only (e.g. an HTML snapshot)" in out
        assert "no-pdf  malformed_2024: malformed file field" in out
        assert (
            "no-PDF breakdown: 1 no file field in bib entry, "
            "1 PDF path no longer exists on disk, "
            "1 non-PDF attachment only (e.g. an HTML snapshot), "
            "1 malformed file field (couldn't parse mime/path)"
        ) in out

    def test_no_pdf_breakdown_omitted_when_everything_resolves(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        write_bib(basic_corpus.BIB_FILE_PATH, BASIC_BIB.replace(
            "@misc{noauthor_page_nodate,\n  title = {A Page With No Author},\n}\n\n", ""
        ))
        sync.run()
        out = capsys.readouterr().out
        assert "no-PDF breakdown" not in out

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
        assert "2 skipped (pdftotext unavailable)" in out
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
        assert "0 skipped (pdftotext unavailable)" not in out
        assert "skipped (pdftotext unavailable)" not in out

    def test_missing_binary_raised_mid_run_is_reported_not_crashed(
        self, basic_corpus, monkeypatch, capsys
    ):
        # Regression (PR #6 review): the up-front probe can pass but
        # pdf_text.extract_text() itself still raise MissingBinary --
        # e.g. pdftotext vanishing from PATH between the probe and this
        # specific item -- and sync.run()'s try block only ever caught
        # CalledProcessError, so this would crash uncaught, defeating the
        # whole point of probing in the first place.
        def raise_missing_binary(pdf_path, citekey):
            raise pdf_text.MissingBinary("pdftotext vanished mid-run")

        monkeypatch.setattr(pdf_text, "extract_text", raise_missing_binary)
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 1
        assert "2 skipped (pdftotext unavailable)" in out
        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        assert rows["smith_example_2024"]["status"] == "discovered"


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

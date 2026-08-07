"""src/sync.py: the deterministic bib -> ledger -> parsed-text entrypoint
(the corpus layer -- AGENTS.md). No LLM calls, must be idempotent."""

import contextlib
import logging
import logging.handlers
import multiprocessing
import subprocess
import sys
from pathlib import Path

import pytest

from src import bib_reader, config, ledger, pdf_text, sync
from tests.conftest import make_reference


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


def make_ref(citekey, tmp_path):
    pdf = tmp_path / f"{citekey}.pdf"
    pdf.write_bytes(b"%PDF")
    return make_reference(citekey=citekey, pdf_path=str(pdf))


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

    def test_summary_reports_pages_per_second(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        out = capsys.readouterr().out
        # The rate itself isn't asserted -- it's real wall-clock time over
        # a fake parse and has no fixed value -- but backend and worker
        # count do, since a bare rate has no tuning use without them.
        assert "page(s) parsed in" in out
        assert "pages/s, 1 worker(s), pdftotext)." in out

    def test_summary_omits_pages_per_second_on_a_no_op_run(self, basic_corpus, monkeypatch, capsys):
        """A run that parses nothing (the common case once a corpus is
        caught up) must not print a meaningless '0 pages/s'."""
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()  # clear

        rc = sync.run()
        out = capsys.readouterr().out
        assert rc == 0
        assert "pages/s" not in out

    def test_warns_when_parsed_text_looks_like_fused_words(
        self, basic_corpus, monkeypatch, capsys, caplog
    ):
        """The guard has to fire from sync, not just exist in pdf_text:
        a backend losing word boundaries is invisible otherwise until it
        shows up as bad retrieval much later."""
        def fused(pdf_path, citekey):
            out_path = config.PARSED_DIR / f"{citekey}.txt"
            config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
            out_path.write_text(" ".join(["isaninputtooranoutputfromafunction"] * 300))
            return out_path

        monkeypatch.setattr(pdf_text, "extract_text", fused)
        sync.run()
        captured = capsys.readouterr()

        assert "losing spaces" in caplog.text
        assert "look like the parser lost word boundaries" in captured.out
        assert "smith_example_2024" in captured.out

    def test_no_quality_warning_for_healthy_text(self, basic_corpus, monkeypatch, capsys, caplog):
        def healthy(pdf_path, citekey):
            out_path = config.PARSED_DIR / f"{citekey}.txt"
            config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
            out_path.write_text(" ".join(["the quick brown fox jumps over a lazy dog"] * 40))
            return out_path

        monkeypatch.setattr(pdf_text, "extract_text", healthy)
        sync.run()
        captured = capsys.readouterr()

        assert "losing spaces" not in caplog.text
        assert "look like the parser lost word boundaries" not in captured.out

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

    def test_a_deterministic_failure_is_reported_every_run_without_re_parsing(
        self, basic_corpus, monkeypatch, capsys
    ):
        # Was the opposite until v1.2.0, and the test that asserted it
        # called the behaviour "perhaps surprising" -- pdf_hash is
        # recorded on the *first* attempt regardless of outcome, so a
        # failed document was skipped forever unless its bytes changed.
        # Harmless while failures were per-document and permanent; not
        # harmless once one dead pool worker can mark every in-flight
        # document parse_failed, because an unattended run would then
        # drop them from the corpus for good.
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        capsys.readouterr()

        rc = sync.run()
        out = capsys.readouterr().out
        # Narrowed in 2.0.0: the PDF is unreadable, so re-parsing it every
        # run would waste the same minutes forever. It is not retried --
        # but it still keeps the run nonzero and still says what to do.
        assert rc == 1
        assert "0 parsed, 2 unchanged" in out
        assert "needs attention" in out
        con = ledger.connect()
        try:
            row = {r["citekey"]: r for r in ledger.all_items(con)}["doe_broken_2023"]
        finally:
            con.close()
        assert row["status"] == "parse_failed"

    def test_reparse_recovers_a_document_written_off_as_deterministic(
        self, basic_corpus, monkeypatch, capsys
    ):
        """The escape hatch for a misclassification, or for a PDF that has
        since been fixed in place."""
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        capsys.readouterr()

        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        assert sync.run(reparse=True) == 0
        con = ledger.connect()
        try:
            row = {r["citekey"]: r for r in ledger.all_items(con)}["doe_broken_2023"]
        finally:
            con.close()
        assert row["status"] == "parsed"

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
        # every src/enrich/* stage already handles a missing binary.
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


class TestConfigureLogging:
    """_configure_logging() is CLI-entrypoint-only (see its own
    docstring for why), so unlike everything else in this file it isn't
    exercised through run() -- it has to be called directly, which means
    the two handlers it attaches to the root logger have to be removed
    again afterwards, or they'd leak into every other test in the
    process."""

    @pytest.fixture(autouse=True)
    def _cleanup_root_handlers(self):
        root = logging.getLogger()
        before = list(root.handlers)
        yield
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        logging.getLogger("src").setLevel(logging.NOTSET)

    def test_creates_the_log_file_and_sets_the_configured_level(
        self, isolated_config, monkeypatch
    ):
        monkeypatch.setattr(config, "LOGGING_LEVEL", "WARNING")
        root_level_before = logging.getLogger().level
        sync._configure_logging()
        assert (config.LOGS_DIR / "sync.log").exists()
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        # LOGGING_LEVEL is a handler-level filter on file_handler alone
        # -- not a logger level -- see test_console_output_ignores_
        # logging_level below for why that distinction is load-bearing.
        assert file_handlers[0].level == logging.WARNING
        assert logging.getLogger().level == root_level_before

    def test_console_output_ignores_logging_level(
        self, isolated_config, monkeypatch, capsys
    ):
        """The bug this guards against: setting LOGGING_LEVEL on the
        "src" logger tree (an earlier version of _configure_logging()
        did) gates whether a record is created at all, before any
        handler is reached -- so WARNING would have silently suppressed
        the [n/N] progress line (INFO) on the console too, contradicting
        "only affects the file". Confirmed here at the strictest
        setting: an INFO record must still reach the console even when
        LOGGING_LEVEL is CRITICAL."""
        monkeypatch.setattr(config, "LOGGING_LEVEL", "CRITICAL")
        sync._configure_logging()
        sync.logger.info("progress line")

        log_text = (config.LOGS_DIR / "sync.log").read_text()
        assert "progress line" not in log_text  # correctly filtered out of the file

        err = capsys.readouterr().err
        assert "progress line" in err  # but not off the console

    def test_a_third_partys_warning_reaches_the_file_but_not_the_console(
        self, isolated_config, monkeypatch, capsys
    ):
        """docling and torch already use stdlib logging. Their WARNING+
        should land in logs/sync.log "for free" -- but not flood the
        console, which is exactly the "docling's own OCR chatter"
        problem the [n/N] progress line's own comment describes."""
        monkeypatch.setattr(config, "LOGGING_LEVEL", "INFO")
        sync._configure_logging()
        logging.getLogger("docling").warning("a third-party warning")

        log_text = (config.LOGS_DIR / "sync.log").read_text()
        assert "a third-party warning" in log_text

        err = capsys.readouterr().err
        assert "a third-party warning" not in err

    def test_a_file_only_record_reaches_the_file_but_not_the_console(
        self, isolated_config, monkeypatch, capsys
    ):
        """Confirmed against a real `python -m src.sync` run: without
        this filter, run()'s summary line -- already printed to stdout
        -- prints a second time via the console handler, once for each
        handler on the same logger call."""
        monkeypatch.setattr(config, "LOGGING_LEVEL", "INFO")
        sync._configure_logging()
        sync.logger.info("ordinary message")
        sync.logger.info("file only message", extra={"file_only": True})

        log_text = (config.LOGS_DIR / "sync.log").read_text()
        assert "ordinary message" in log_text
        assert "file only message" in log_text

        err = capsys.readouterr().err
        assert "ordinary message" in err
        assert "file only message" not in err

    def test_the_log_file_is_utf8(self, isolated_config, monkeypatch):
        """Explicit encoding, not the platform default: a citekey or
        title carrying an accented or non-Latin name (this corpus has
        real ones -- Schroder-with-an-umlaut, Greek in formulae) must
        not depend on whatever locale the host happens to be in."""
        monkeypatch.setattr(config, "LOGGING_LEVEL", "INFO")
        sync._configure_logging()
        sync.logger.info("Wüllnerstraße αβγ")
        log_text = (config.LOGS_DIR / "sync.log").read_text(encoding="utf-8")
        assert "Wüllnerstraße αβγ" in log_text


MANY_BIB = "".join(f"""
@article{{doc_{i}_2024,
  title = {{Paper {i}}},
  author = {{Author, A}},
  year = {{2024}},
  file = {{p{i}.pdf:p{i}.pdf:application/pdf}},
}}
""" for i in range(6))


@pytest.fixture
def many_corpus(isolated_config):
    """Six PDFs of deliberately different sizes, so submission order is
    observable (sync submits biggest-first)."""
    write_bib(isolated_config.BIB_FILE_PATH, MANY_BIB)
    bib_dir = isolated_config.BIB_FILE_PATH.parent
    for i in range(6):
        (bib_dir / f"p{i}.pdf").write_bytes(b"%PDF" + b"x" * (100 * i))
    return isolated_config


def _thread_executor(workers):
    from concurrent.futures import ThreadPoolExecutor

    return ThreadPoolExecutor(max_workers=workers)


def _recording_executor(submitted):
    """A thread executor that records each job's citekey when it is
    *submitted*.

    Recording inside the worker instead -- which is what this used to do
    -- observes completion order, not submission order. With more than one
    worker the two differ by whichever job happens to finish first, so an
    assertion about submission order written that way passes or fails on a
    race. `submit()` is called from one thread in a loop, so this is
    deterministic.
    """
    from concurrent.futures import ThreadPoolExecutor

    class RecordingExecutor(ThreadPoolExecutor):
        def submit(self, fn, job, *args, **kwargs):
            submitted.append(job[1])  # (pdf_path, citekey, threads)
            return super().submit(fn, job, *args, **kwargs)

    return lambda workers: RecordingExecutor(max_workers=workers)


def fake_extract_one_factory(record=None, fail_citekeys=()):
    """Stands in for pdf_text.extract_one, the picklable pool entry point.
    Returns the (citekey, out_path, exception) triple it does."""
    def fake_extract_one(job):
        pdf_path, citekey, threads = job
        if record is not None:
            record.append((citekey, threads))
        if citekey in fail_citekeys:
            return citekey, None, pdf_text.ExtractionError(f"{citekey}: bad PDF")
        config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = config.PARSED_DIR / f"{citekey}.txt"
        out_path.write_text(f"extracted text for {citekey}")
        return citekey, str(out_path), None
    return fake_extract_one


class TestWorkerCount:
    def test_default_of_one_never_builds_a_pool(self, many_corpus, monkeypatch):
        """The whole point of defaulting to 1: a default run must be the
        historical serial path, with no executor, no pickling and no
        subprocesses -- not a pool that happens to have one worker."""
        monkeypatch.setattr(config, "PARSER_WORKERS", 1)
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())

        def refuse(workers):
            raise AssertionError(f"built a pool of {workers} for a serial run")

        monkeypatch.setattr(sync, "_executor_for", refuse)
        assert sync.run() == 0

    def test_oversized_request_is_clamped_and_warned(self, many_corpus, monkeypatch, caplog):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 64)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 8)
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

        sync.run()
        assert "[parser].workers=64" in caplog.text
        assert "using 2" in caplog.text


class TestParallelParsing:
    @pytest.fixture(autouse=True)
    def _four_workers(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        # A real ProcessPoolExecutor would run pdf_text.extract_one in a
        # *child interpreter*, where this process's monkeypatches don't
        # exist -- the fake would silently not be used and these tests
        # would drive the real docling. Swapping the executor (the seam
        # TestExecutorChoice covers separately) keeps the concurrency
        # real while leaving the patches visible.
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

    def test_every_document_is_parsed_and_recorded(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        rc = sync.run()

        assert rc == 0
        assert "6 parsed" in capsys.readouterr().out
        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        assert all(rows[f"doc_{i}_2024"]["status"] == "parsed" for i in range(6))

    def test_reporting_order_follows_the_bib_not_completion_order(
        self, many_corpus, monkeypatch, capsys
    ):
        """Futures complete in whatever order they finish. If that leaked
        into stdout, two identical runs would print different output and
        no one could diff them."""
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        printed = [ln.split()[-1] for ln in capsys.readouterr().out.splitlines()
                   if ln.startswith("  parsed  ")]
        assert printed == [f"doc_{i}_2024" for i in range(6)]

    def test_largest_pdf_is_submitted_first(self, many_corpus, monkeypatch):
        """One 675-page document in this project's real corpus is 5% of
        all its pages. Picked up last, it alone defines the wall clock --
        so submission is biggest-first (by file size, which needs no PDF
        library to measure).

        Observed at the executor rather than inside the worker: see
        _recording_executor for why the worker-side version was a race.
        """
        submitted = []
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        monkeypatch.setattr(sync, "_executor_for", _recording_executor(submitted))
        sync.run()
        assert submitted == [f"doc_{i}_2024" for i in reversed(range(6))]

    def test_one_bad_pdf_does_not_take_down_the_others(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(
            pdf_text, "extract_one", fake_extract_one_factory(fail_citekeys={"doc_3_2024"})
        )
        rc = sync.run()
        captured = capsys.readouterr()
        out = captured.out

        assert rc == 1
        assert "5 parsed" in out and "1 failed" in out
        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        assert rows["doc_3_2024"]["status"] == "parse_failed"
        assert rows["doc_2_2024"]["status"] == "parsed"

    def test_each_worker_is_told_its_thread_budget(self, many_corpus, monkeypatch):
        """workers x threads has to fit the host, so the parent works out
        the per-worker thread count and passes it down."""
        record = []
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory(record=record))
        sync.run()
        assert {threads for _, threads in record} == {pdf_text.docling_threads(4)}

    def test_work_finished_before_the_pool_died_is_not_thrown_away(
        self, many_corpus, monkeypatch, capsys
    ):
        """The trap in reporting results in input order: submission is
        largest-first, so if the pool dies while the biggest document is
        still running, every smaller one that already finished would be
        discarded and reported as a failure -- real work thrown away, and
        parsed documents mislabelled. Results are recorded as they land,
        so only what was genuinely in flight is lost."""
        from concurrent.futures.process import BrokenProcessPool

        done = fake_extract_one_factory()

        def die_on_the_biggest(job):
            _, citekey, _ = job
            # doc_5_2024 has the largest file, so it is submitted first.
            if citekey == "doc_5_2024":
                raise BrokenProcessPool("a worker died")
            return done(job)

        monkeypatch.setattr(pdf_text, "extract_one", die_on_the_biggest)
        rc = sync.run()
        out = capsys.readouterr().out

        assert rc == 1
        assert "5 parsed" in out and "1 failed" in out
        con = ledger.connect()
        try:
            rows = {r["citekey"]: r for r in ledger.all_items(con)}
        finally:
            con.close()
        assert rows["doc_5_2024"]["status"] == "parse_failed"
        assert all(rows[f"doc_{i}_2024"]["status"] == "parsed" for i in range(5))

    def test_a_pool_already_broken_at_submit_time_is_handled_too(
        self, many_corpus, monkeypatch, capsys, caplog
    ):
        """Once a ProcessPoolExecutor knows it is broken, submit() itself
        raises rather than returning a future -- a second path to the same
        outcome, and one as_completed never gets to see."""
        from concurrent.futures.process import BrokenProcessPool

        class DeadExecutor:
            def submit(self, *args, **kwargs):
                raise BrokenProcessPool("pool was already dead")

            def shutdown(self, *args, **kwargs):
                pass

        monkeypatch.setattr(sync, "_executor_for", lambda workers: DeadExecutor())
        rc = sync.run()
        captured = capsys.readouterr()

        assert rc == 1
        assert "6 failed" in captured.out
        assert "worker" in caplog.text.lower()

    def test_a_dead_pool_fails_the_documents_not_the_run(
        self, many_corpus, monkeypatch, capsys, caplog
    ):
        """A worker killed by the OOM killer breaks the whole pool. That
        has to be reported against the documents that didn't get parsed,
        not raised as an uncaught BrokenProcessPool out of sync."""
        from concurrent.futures.process import BrokenProcessPool

        def explode(job):
            raise BrokenProcessPool("a worker died")

        monkeypatch.setattr(pdf_text, "extract_one", explode)
        rc = sync.run()
        captured = capsys.readouterr()

        assert rc == 1
        assert "6 failed" in captured.out
        assert "worker" in caplog.text.lower()


class TestPoolPrestart:
    def test_the_forkserver_is_started_before_the_bibliography_is_read(
        self, basic_corpus, monkeypatch
    ):
        """Order is the whole point. Started after the bib read, the
        forkserver's torch import is 2.5s the parent spends blocked;
        started before it, that import runs while the bib file is being
        parsed and the pool is ready 2.5s sooner."""
        order = []
        monkeypatch.setattr(pdf_text, "prestart_pool", lambda: order.append("prestart"))
        real_read = bib_reader.read_library

        def watched_read(*args, **kwargs):
            order.append("read_library")
            return real_read(*args, **kwargs)

        monkeypatch.setattr(sync.bib_reader, "read_library", watched_read)
        sync.run()

        assert order == ["prestart", "read_library"]


class TestExecutorChoice:
    def test_docling_gets_processes(self, monkeypatch):
        """docling runs in-process and holds the GIL, so threads would
        serialise exactly the work we want overlapped."""
        from concurrent.futures import ProcessPoolExecutor

        monkeypatch.setattr(config, "PARSER", "docling")
        with sync._executor_for(2) as ex:
            assert isinstance(ex, ProcessPoolExecutor)

    def test_pdftotext_gets_threads(self, monkeypatch):
        """pdftotext is an external subprocess that releases the GIL
        while it runs, so a process pool would only add pickling and
        spawn cost to buy the same concurrency."""
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setattr(config, "PARSER", "pdftotext")
        with sync._executor_for(2) as ex:
            assert isinstance(ex, ThreadPoolExecutor)


class TestPdfSize:
    """Only ever used to order work biggest-first."""

    def test_reports_the_file_size(self, tmp_path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"x" * 321)
        assert sync._pdf_size(str(pdf)) == 321

    def test_unreadable_file_sorts_last_instead_of_raising(self, tmp_path):
        """A PDF that vanished between bib resolution and here must not
        take down the ordering -- the parse will report the real error a
        moment later, which is the better place for it."""
        assert sync._pdf_size(str(tmp_path / "gone.pdf")) == 0


class TestParseSerial:
    def test_yields_a_triple_per_reference(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        refs = [make_ref("a", tmp_path), make_ref("b", tmp_path)]
        assert [(k, e) for k, _, e in sync._parse_serial(refs)] == [("a", None), ("b", None)]

    def test_a_failure_becomes_the_third_slot_not_a_raise(
        self, isolated_config, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"b"})
        )
        results = list(sync._parse_serial([make_ref("a", tmp_path), make_ref("b", tmp_path)]))
        assert results[0][2] is None
        assert isinstance(results[1][2], pdf_text.ExtractionError)


class TestGpuAssignment:
    @staticmethod
    def _capture(monkeypatch):
        """Record what ProcessPoolExecutor was constructed with.

        Asserted this way rather than through the executor's private
        _initializer/_initargs/_mp_context, which are CPython
        implementation details that could be renamed under us.
        """
        captured = {}

        def record(**kwargs):
            captured.update(kwargs)
            return contextlib.nullcontext()

        monkeypatch.setattr(sync, "ProcessPoolExecutor", record)
        return captured

    def test_docling_pool_hands_each_worker_its_own_gpu(self, monkeypatch):
        """The whole point: docling's AcceleratorDevice.AUTO resolves to
        cuda:0 in every process, so without an explicit per-worker device
        N workers contend for one card while the rest idle."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(
            pdf_text, "usable_devices", lambda: ([0, 1, 2, 3], None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["initializer"] is pdf_text.init_worker
        counter, _lock, devices = captured["initargs"]
        assert devices == [0, 1, 2, 3]
        # A shared counter, not a per-process guess: a pool creates
        # workers lazily and numbers none of them.
        assert counter.value == 0

    def test_the_initargs_actually_work_as_init_worker_arguments(self, monkeypatch):
        """Calls init_worker with what the pool would have handed it.

        Every other test here substitutes the executor, so the
        initializer is never invoked and a pool builder passing the wrong
        *shape* is invisible -- which is exactly how the enrichment layer
        went on passing an int after init_worker started wanting a list
        (PR #40 review). Asserting on a captured literal cannot catch
        that; calling across the seam can.
        """
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([2, 3], None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        pdf_text._reset_worker_device()
        try:
            pdf_text.init_worker(*captured["initargs"])
            assert pdf_text.worker_device() == "cuda:2"
        finally:
            pdf_text._reset_worker_device()

    def test_a_card_with_no_room_never_reaches_a_worker(self, monkeypatch):
        """usable_devices decides; this asserts sync passes its answer
        through rather than re-deriving a count of its own."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([1, 2, 3], None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["initargs"][2] == [1, 2, 3]

    def test_a_skipped_card_is_reported_to_the_user(self, monkeypatch, caplog):
        """A run quietly using five cards instead of six looks exactly
        like one using all six, until it is 20% slower for no stated
        reason."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(
            pdf_text, "usable_devices", lambda: ([1], "  WARNING skipping cuda:0"))
        self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert "WARNING skipping cuda:0" in caplog.text

    def test_the_start_method_is_pdf_texts_to_choose(self, monkeypatch):
        """One decision, in one place: sync and src/enrich/docling_parse
        build the same kind of pool, and a start method hard-coded in
        each would be two that can drift apart.

        Asserted by *identity* on the context object, and built from this
        platform's own default rather than a named method. Naming
        "forkserver" here made the test itself unrunnable on Windows --
        `get_context("forkserver")` raises `ValueError: cannot find
        context for 'forkserver'` before the assertion is even reached.
        The contract under test is that sync passes through whatever
        pdf_text hands it, which does not require any particular method
        to exist on the machine running the test.
        """
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([0, 1, 2, 3], None))
        chosen = multiprocessing.get_context()
        monkeypatch.setattr(
            pdf_text, "process_pool_context", lambda: (chosen, None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["mp_context"] is chosen

    def test_plain_fork_is_never_used(self, monkeypatch):
        """Not a style preference: this process holds the run lock and
        the ledger open as live sqlite connections, and SQLite says not
        to carry an open connection across fork()."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([0, 1, 2, 3], None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["mp_context"].get_start_method() != "fork"

    def test_a_start_method_complaint_reaches_stderr(self, monkeypatch, caplog):
        """A pool that quietly fell back to spawn looks exactly like one
        that got what was configured, and is ~1.5s slower to start."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([], None))
        monkeypatch.setattr(
            pdf_text, "process_pool_context",
            lambda: (multiprocessing.get_context("spawn"), "  NOTE fell back"))
        self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert "NOTE fell back" in caplog.text

    def test_a_cpu_only_host_still_builds_a_working_pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([], None))
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["initargs"][2] == []

    def test_pdftotext_pool_has_no_gpu_initialiser(self, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setattr(config, "PARSER", "pdftotext")
        with sync._executor_for(2) as ex:
            assert isinstance(ex, ThreadPoolExecutor)


class TestInterrupt:
    """Ctrl+C during a parallel run.

    Reported from real use on 501 documents: the run "took forever to
    exit" and emitted docling teardown tracebacks. Cause: every job is
    submitted up front, and `with executor` calls shutdown(wait=True) on
    the way out -- so an interrupt drained the entire remaining queue
    instead of stopping.
    """

    @pytest.fixture(autouse=True)
    def _pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

    def test_pending_work_is_cancelled_not_drained(self, many_corpus, monkeypatch, capsys):
        """The bug: `with executor` shuts down with wait=True, draining
        every queued document before exiting.

        Asserted on the shutdown call rather than on how many documents
        happened to start: with fast workers the race is real, and the
        contract -- cancel what has not begun, don't block on what has --
        is the thing that actually fixes the reported symptom.
        """
        recorded = []

        def recording_executor(workers):
            inner = _thread_executor(workers)
            real_shutdown = inner.shutdown

            def shutdown(*args, **kwargs):
                recorded.append(kwargs)
                return real_shutdown(*args, **kwargs)

            inner.shutdown = shutdown
            return inner

        monkeypatch.setattr(sync, "_executor_for", recording_executor)
        monkeypatch.setattr(
            pdf_text, "extract_one", lambda job: (_ for _ in ()).throw(KeyboardInterrupt)
        )
        with pytest.raises(KeyboardInterrupt):
            sync.run()

        assert any(k.get("cancel_futures") for k in recorded), recorded
        assert all(k.get("wait") is False for k in recorded), recorded

    def test_interrupt_says_what_it_did(self, many_corpus, monkeypatch, caplog):
        def interrupt_immediately(job):
            raise KeyboardInterrupt

        monkeypatch.setattr(pdf_text, "extract_one", interrupt_immediately)
        with pytest.raises(KeyboardInterrupt):
            sync.run()
        assert "interrupted" in caplog.text.lower()


class TestProgressReporting:
    """A parallel run reported nothing until every document finished.

    Over a 501-PDF corpus that is half an hour of apparent silence --
    the user-visible symptom being pages of docling's own OCR chatter and
    no indication anything was progressing.
    """

    @pytest.fixture(autouse=True)
    def _pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

    def test_each_completion_is_reported_as_it_lands(self, many_corpus, monkeypatch, caplog):
        caplog.set_level(logging.INFO)
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        # A counter, so the reader can see both rate and remaining work.
        assert "[1/6]" in caplog.text and "[6/6]" in caplog.text

    def test_stdout_stays_in_bibliography_order(self, many_corpus, monkeypatch, capsys):
        """Live progress goes to stderr precisely so stdout can stay
        deterministic and diffable between runs."""
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        printed = [ln.split()[-1] for ln in capsys.readouterr().out.splitlines()
                   if ln.startswith("  parsed  ")]
        assert printed == [f"doc_{i}_2024" for i in range(6)]


class TestStallWatchdog:
    """Bounds a parallel run's worst case.

    Deliberately NOT a per-document deadline: the slowest legitimate
    document in this corpus takes 246s, so any per-document threshold
    generous enough to spare it is a poor hang detector. With several
    workers, completions arrive constantly, so total silence across the
    whole pool is the signal that separates hung from slow.
    """

    @pytest.fixture(autouse=True)
    def _pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

    def test_a_stalled_pool_is_abandoned_and_its_documents_reported(
        self, many_corpus, monkeypatch, capsys, caplog
    ):
        import threading

        blocked = threading.Event()
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.3)
        monkeypatch.setattr(
            pdf_text, "extract_one", lambda job: blocked.wait(30) or fake_extract_one_factory()(job)
        )
        try:
            rc = sync.run()
        finally:
            blocked.set()

        captured = capsys.readouterr()
        assert rc == 1
        assert "6 failed" in captured.out
        assert "no document finished" in caplog.text.lower()
        # The console handler's formatter is bare "%(message)s" -- no
        # level name -- so the message text itself has to carry the
        # label, or a scheduled run tailing stderr sees an unlabeled
        # line. Checked on the raw message (record.getMessage()), not
        # caplog.text: pytest's own capture format already injects the
        # level name, which would make this assertion pass even if the
        # actual message text lost it.
        assert any("WARNING" in r.getMessage() for r in caplog.records)

    def test_progress_resets_the_clock(self, many_corpus, monkeypatch, capsys):
        """A slow-but-moving run must never be killed: the timeout is
        between completions, not for the run as a whole."""
        import time

        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.5)

        def slow_but_steady(job):
            time.sleep(0.2)
            return fake_extract_one_factory()(job)

        monkeypatch.setattr(pdf_text, "extract_one", slow_but_steady)
        assert sync.run() == 0
        assert "6 parsed" in capsys.readouterr().out

    def test_it_can_be_switched_off(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", None)
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        assert sync.run() == 0

    def test_a_stall_terminates_the_workers(self, many_corpus, monkeypatch, capsys):
        """Not merely abandoned: in-flight jobs would otherwise keep
        running and write content/parsed/<citekey>.txt for documents this
        run has already reported as failed -- a file on disk
        contradicting the ledger."""
        import threading

        blocked = threading.Event()
        killed = []
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.3)
        monkeypatch.setattr(pdf_text, "terminate_workers", lambda ex: killed.append(ex))
        monkeypatch.setattr(pdf_text, "extract_one", lambda job: blocked.wait(30))
        try:
            sync.run()
        finally:
            blocked.set()
        assert killed, "workers were left running after the stall"

    def test_a_stalled_document_is_not_blamed_on_a_dead_worker(
        self, many_corpus, monkeypatch, caplog
    ):
        """The two failure modes are different and the message has to
        say which one happened."""
        import threading

        blocked = threading.Event()
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.3)
        monkeypatch.setattr(pdf_text, "extract_one", lambda job: blocked.wait(30))
        try:
            sync.run()
        finally:
            blocked.set()
        assert "gave up waiting" in caplog.text
        assert "worker died" not in caplog.text


class TestReparse:
    def test_reparse_re_extracts_everything(self, basic_corpus, monkeypatch, capsys):
        """The recovery path for output that is recorded as fine but
        isn't -- which the ledger, by definition, cannot detect itself."""
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        sync.run()
        capsys.readouterr()

        assert sync.run() == 0
        assert "0 parsed, 2 unchanged" in capsys.readouterr().out

        sync.run(reparse=True)
        assert "2 parsed, 0 unchanged" in capsys.readouterr().out

    def test_reparse_is_registered_on_the_cli(self, isolated_config):
        import subprocess
        out = subprocess.run(
            [sys.executable, "-m", "src.sync", "--help"],
            capture_output=True, text=True, cwd=str(config.REPO_ROOT),
        ).stdout
        assert "--reparse" in out


class TestFailureReporting:
    def test_a_backend_failure_is_deterministic_and_not_retried(
        self, basic_corpus, monkeypatch, capsys
    ):
        """A PDF the backend cannot read is not going to become readable
        next run. Re-parsing it every time wastes minutes and trains the
        reader to ignore exit 1."""
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        assert sync.run() == 1
        capsys.readouterr()

        assert sync.run() == 1  # still reported...
        out = capsys.readouterr().out
        assert "0 parsed" in out  # ...but not re-parsed
        assert "needs attention" in out

    def test_a_worker_death_is_transient_and_is_retried(self, many_corpus, monkeypatch, capsys):
        """The failure that motivated retrying at all: one dead worker
        marks every in-flight document failed, and they must come back."""
        from concurrent.futures.process import BrokenProcessPool

        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)
        monkeypatch.setattr(
            pdf_text, "extract_one",
            lambda job: (_ for _ in ()).throw(BrokenProcessPool("worker died")),
        )
        assert sync.run() == 1
        capsys.readouterr()

        monkeypatch.setattr(config, "PARSER_WORKERS", 1)
        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        assert sync.run() == 0
        assert "6 parsed" in capsys.readouterr().out

    def test_the_summary_separates_the_two_kinds(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        out = capsys.readouterr().out
        assert "1 failed" in out
        assert "needs attention" in out


def timing_out_extract_text_factory(timeout_citekeys):
    """A backend that runs out of time on some documents and parses the
    rest -- the shape both real backends produce (see
    tests/test_pdf_text.py's TestTimeoutIsRecordedAsSuch)."""
    parse = fake_extract_text_factory()

    def fake_extract_text(pdf_path, citekey):
        if citekey in timeout_citekeys:
            error = pdf_text.ExtractionError(f"{citekey} exceeded the timeout")
            error.timed_out = True
            raise error
        return parse(pdf_path, citekey)
    return fake_extract_text


class TestTimeoutReporting:
    """The summary has to name a document that ran out of time, because
    the remediation differs from every other failure it is counted
    alongside: raise a config value, not fix the PDF."""

    def test_the_summary_names_the_documents_that_timed_out(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 90.0)
        monkeypatch.setattr(
            pdf_text, "extract_text",
            timing_out_extract_text_factory({"doe_broken_2023"}),
        )
        assert sync.run() == 1
        out = capsys.readouterr().out

        assert "1 document(s) hit the 90.0s [parser].document_timeout" in out
        assert "doe_broken_2023" in out
        assert "--reparse" in out

    def test_the_summary_stops_giving_the_advice_that_does_not_apply(
        self, basic_corpus, monkeypatch, capsys
    ):
        """Two contradictory instructions is worse than one: the PDF is
        fine, so the deterministic line must not send its reader to fix
        it while the line below says to raise a setting."""
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 90.0)
        monkeypatch.setattr(
            pdf_text, "extract_text",
            timing_out_extract_text_factory({"doe_broken_2023"}),
        )
        sync.run()
        out = capsys.readouterr().out
        assert "needs attention" in out  # still says what the state is...
        assert "fix or remove the PDF" not in out  # ...but not the wrong fix
        assert "see the WARNING below for the fix" in out

    def test_the_usual_advice_survives_when_nothing_timed_out(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        assert "fix or remove the PDF" in capsys.readouterr().out

    def test_a_corpus_wide_timeout_stays_one_readable_line(
        self, isolated_config, monkeypatch, capsys
    ):
        """Past a handful, the count is the diagnosis and the names are
        noise -- naming every one of them would bury the line that says
        what to do in a list no terminal wants to scroll."""
        entries = "".join(f"""
@article{{doc_{i}_2024,
  title = {{Document {i}}},
  author = {{Roe, Jan}},
  year = {{2024}},
  file = {{p{i}.pdf:p{i}.pdf:application/pdf}},
}}
""" for i in range(14))
        write_bib(isolated_config.BIB_FILE_PATH, entries)
        for i in range(14):
            (isolated_config.BIB_FILE_PATH.parent / f"p{i}.pdf").write_bytes(b"%PDF")
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 5.0)
        monkeypatch.setattr(
            pdf_text, "extract_text",
            timing_out_extract_text_factory({f"doc_{i}_2024" for i in range(14)}),
        )
        sync.run()
        out = capsys.readouterr().out

        # The count stays exact even though the list does not.
        assert "14 document(s) hit the 5.0s" in out
        assert "(+4 more)" in out
        line = next(ln for ln in out.splitlines() if "hit the 5.0s" in ln)
        assert line.count("doc_") == 10

    def test_an_ordinary_failure_produces_no_timeout_line(
        self, basic_corpus, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        assert "[parser].document_timeout" not in capsys.readouterr().out

    def test_a_timeout_is_still_counted_as_a_deterministic_failure(
        self, basic_corpus, monkeypatch, capsys
    ):
        """Deliberately not transient: if the limit is genuinely too low
        for this host, retrying every run burns the same minutes forever
        and never converges."""
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 90.0)
        monkeypatch.setattr(
            pdf_text, "extract_text",
            timing_out_extract_text_factory({"doe_broken_2023"}),
        )
        assert sync.run() == 1
        capsys.readouterr()

        assert sync.run() == 1
        out = capsys.readouterr().out
        assert "0 parsed" in out
        assert "needs attention" in out

    def test_every_timed_out_document_is_named(self, basic_corpus, monkeypatch, capsys):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 5.0)
        monkeypatch.setattr(
            pdf_text, "extract_text",
            timing_out_extract_text_factory({"doe_broken_2023", "smith_example_2024"}),
        )
        sync.run()
        out = capsys.readouterr().out
        assert "2 document(s) hit the 5.0s" in out
        assert "doe_broken_2023" in out and "smith_example_2024" in out

    def test_a_timeout_from_a_pool_worker_is_reported_too(
        self, many_corpus, monkeypatch, capsys
    ):
        """The parallel path returns the exception across a process
        boundary rather than raising it, so the mark has to arrive
        intact for the summary to say anything."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 30.0)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

        def timing_out_extract_one(job):
            _pdf_path, citekey, _threads = job
            error = pdf_text.ExtractionError("out of time")
            error.timed_out = True
            return citekey, None, error

        monkeypatch.setattr(pdf_text, "extract_one", timing_out_extract_one)
        assert sync.run() == 1
        out = capsys.readouterr().out
        assert "6 document(s) hit the 30.0s [parser].document_timeout" in out


class TestStallWarning:
    """The watchdog kills work, so it must warn before it acts.

    The schedule makes this necessary rather than merely polite:
    submission is biggest-file-first, so at pool start every worker is
    simultaneously on the largest documents in the corpus. On a CPU-only
    host with OCR on, a long first gap is the schedule working, not a
    hang -- and because stall-killed documents are retried identically,
    a kill without warning can repeat every run forever.
    """

    @pytest.fixture(autouse=True)
    def _pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

    def test_it_warns_at_half_time_before_killing(self, many_corpus, monkeypatch, caplog):
        import threading

        blocked = threading.Event()
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.4)
        monkeypatch.setattr(pdf_text, "extract_one", lambda job: blocked.wait(30))
        try:
            sync.run()
        finally:
            blocked.set()
        text = caplog.text
        assert "no completions in" in text.lower()
        # The warning has to be actionable, not just early.
        assert "stall_timeout" in text
        assert text.index("no completions in") < text.index("giving up on the")
        # See test_a_stalled_pool_is_abandoned_and_its_documents_reported
        # for why this checks the raw message, not caplog.text.
        assert any("WARNING" in r.getMessage() for r in caplog.records)

    def test_a_run_that_finishes_between_warning_and_kill_is_not_killed(
        self, many_corpus, monkeypatch, capsys
    ):
        """Slow but moving must survive: the warning is a warning."""
        import time

        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 0.6)

        def slow(job):
            time.sleep(0.4)
            return fake_extract_one_factory()(job)

        monkeypatch.setattr(pdf_text, "extract_one", slow)
        assert sync.run() == 0
        assert "6 parsed" in capsys.readouterr().out

    def test_no_warning_when_nothing_stalls(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(config, "PARSER_STALL_TIMEOUT", 30.0)
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        assert "no completions in" not in capsys.readouterr().err.lower()

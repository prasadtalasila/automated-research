"""src/sync.py: the deterministic bib -> ledger -> parsed-text entrypoint
("job 1" -- AGENTS.md). No LLM calls, must be idempotent."""

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

from src import config, ledger, pdf_text, sync
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

    def test_warns_when_parsed_text_looks_like_fused_words(self, basic_corpus, monkeypatch, capsys):
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

        assert "losing spaces" in captured.err
        assert "look like the parser lost word boundaries" in captured.out
        assert "smith_example_2024" in captured.out

    def test_no_quality_warning_for_healthy_text(self, basic_corpus, monkeypatch, capsys):
        def healthy(pdf_path, citekey):
            out_path = config.PARSED_DIR / f"{citekey}.txt"
            config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
            out_path.write_text(" ".join(["the quick brown fox jumps over a lazy dog"] * 40))
            return out_path

        monkeypatch.setattr(pdf_text, "extract_text", healthy)
        sync.run()
        captured = capsys.readouterr()

        assert "losing spaces" not in captured.err
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

    def test_previously_failed_parse_is_retried_on_the_next_run(
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
        assert rc == 1  # retried, failed again, and said so
        assert "0 parsed, 1 unchanged, 1 without a PDF attachment, 1 failed" in out
        con = ledger.connect()
        try:
            row = {r["citekey"]: r for r in ledger.all_items(con)}["doe_broken_2023"]
        finally:
            con.close()
        assert row["status"] == "parse_failed"

    def test_a_retried_parse_that_succeeds_recovers_the_document(
        self, basic_corpus, monkeypatch, capsys
    ):
        """The point of retrying: a transient failure (a dead worker, an
        OOM) must not cost the document permanently."""
        monkeypatch.setattr(
            pdf_text, "extract_text", fake_extract_text_factory(fail_citekeys={"doe_broken_2023"})
        )
        sync.run()
        capsys.readouterr()

        monkeypatch.setattr(pdf_text, "extract_text", fake_extract_text_factory())
        assert sync.run() == 0
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

    def test_oversized_request_is_clamped_and_warned(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 64)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 8)
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        monkeypatch.setattr(sync, "_executor_for", _thread_executor)

        sync.run()
        err = capsys.readouterr().err
        assert "[parser].workers=64" in err
        assert "using 2" in err


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
        library to measure)."""
        submitted = []
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory(record=submitted))
        sync.run()
        assert [c for c, _ in submitted] == [f"doc_{i}_2024" for i in reversed(range(6))]

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
        self, many_corpus, monkeypatch, capsys
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
        assert "worker" in captured.err.lower()

    def test_a_dead_pool_fails_the_documents_not_the_run(self, many_corpus, monkeypatch, capsys):
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
        assert "worker" in captured.err.lower()


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
        monkeypatch.setattr(pdf_text, "gpu_count", lambda: 4)
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["initializer"] is pdf_text.init_worker
        counter, _lock, n_gpus = captured["initargs"]
        assert n_gpus == 4
        # A shared counter, not a per-process guess: a pool creates
        # workers lazily and numbers none of them.
        assert counter.value == 0

    def test_spawn_is_used_so_a_cuda_touching_parent_is_safe(self, monkeypatch):
        """Counting GPUs initialises CUDA in the parent, and a forked
        child inherits a broken CUDA context from such a parent. Each
        worker reloads docling's models anyway, so spawn's extra startup
        is noise against that."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "gpu_count", lambda: 4)
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["mp_context"].get_start_method() == "spawn"

    def test_a_cpu_only_host_still_builds_a_working_pool(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "gpu_count", lambda: 0)
        captured = self._capture(monkeypatch)

        with sync._executor_for(2):
            pass

        assert captured["initargs"][2] == 0

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

    def test_interrupt_says_what_it_did(self, many_corpus, monkeypatch, capsys):
        def interrupt_immediately(job):
            raise KeyboardInterrupt

        monkeypatch.setattr(pdf_text, "extract_one", interrupt_immediately)
        with pytest.raises(KeyboardInterrupt):
            sync.run()
        assert "interrupted" in capsys.readouterr().err.lower()


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

    def test_each_completion_is_reported_as_it_lands(self, many_corpus, monkeypatch, capsys):
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        err = capsys.readouterr().err
        # A counter, so the reader can see both rate and remaining work.
        assert "[1/6]" in err and "[6/6]" in err

    def test_stdout_stays_in_bibliography_order(self, many_corpus, monkeypatch, capsys):
        """Live progress goes to stderr precisely so stdout can stay
        deterministic and diffable between runs."""
        monkeypatch.setattr(pdf_text, "extract_one", fake_extract_one_factory())
        sync.run()
        printed = [ln.split()[-1] for ln in capsys.readouterr().out.splitlines()
                   if ln.startswith("  parsed  ")]
        assert printed == [f"doc_{i}_2024" for i in range(6)]

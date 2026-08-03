"""src/ledger.py: the sqlite state that makes `sync` incremental."""

import os
import sqlite3
import time

import pytest

from src import ledger

from tests.conftest import make_reference


class TestConnect:
    def test_creates_schema_and_content_dir(self, isolated_config):
        assert not isolated_config.CONTENT_DIR.exists()
        con = ledger.connect()
        try:
            assert isolated_config.LEDGER_PATH.exists()
            # Schema present -- querying an empty table doesn't raise.
            assert ledger.all_items(con) == []
        finally:
            con.close()

    def test_idempotent_across_calls(self, isolated_config):
        con1 = ledger.connect()
        con1.close()
        con2 = ledger.connect()
        try:
            assert ledger.all_items(con2) == []
        finally:
            con2.close()


class TestSchemaMigration:
    def test_fresh_database_is_at_current_schema_version(self, isolated_config):
        con = ledger.connect()
        try:
            (version,) = con.execute("PRAGMA user_version").fetchone()
            assert version == len(ledger._MIGRATIONS)
            cols = {row[1] for row in con.execute("PRAGMA table_info(items)")}
            assert {"pdf_size", "pdf_mtime_ns"} <= cols
        finally:
            con.close()

    def test_legacy_database_predating_the_migration_gets_upgraded(self, isolated_config):
        # Simulates a ledger written before pdf_size/pdf_mtime_ns existed:
        # the original table shape, user_version still at sqlite's default
        # of 0. CREATE TABLE IF NOT EXISTS alone would never add the new
        # columns to this file -- only _migrate does.
        isolated_config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(isolated_config.LEDGER_PATH)
        raw.execute("""
            CREATE TABLE items (
                citekey TEXT PRIMARY KEY,
                item_type TEXT,
                title TEXT,
                year TEXT,
                doi TEXT,
                url TEXT,
                pdf_path TEXT,
                pdf_hash TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                parsed_path TEXT,
                parse_error TEXT,
                last_synced TEXT NOT NULL
            )
        """)
        raw.execute(
            "INSERT INTO items (citekey, status, last_synced) VALUES (?, ?, ?)",
            ("legacy_key", "discovered", "2020-01-01T00:00:00+00:00"),
        )
        raw.commit()
        raw.close()

        con = ledger.connect()
        try:
            cols = {row[1] for row in con.execute("PRAGMA table_info(items)")}
            assert {"pdf_size", "pdf_mtime_ns"} <= cols
            (version,) = con.execute("PRAGMA user_version").fetchone()
            assert version == len(ledger._MIGRATIONS)
            assert ledger.known_citekeys(con) == {"legacy_key"}
        finally:
            con.close()

    def test_already_migrated_database_is_a_no_op(self, isolated_config):
        con1 = ledger.connect()
        con1.close()

        con2 = ledger.connect()
        try:
            (version,) = con2.execute("PRAGMA user_version").fetchone()
            assert version == len(ledger._MIGRATIONS)
        finally:
            con2.close()

    def test_columns_already_present_at_user_version_zero_does_not_raise(self, isolated_config):
        # Pathological but guarded-against case: user_version somehow
        # lagging behind a table that already has the target columns
        # (e.g. a future column added directly to _SCHEMA instead of
        # _MIGRATIONS). "ALTER TABLE ADD COLUMN" on an existing column
        # raises OperationalError -- _migrate must check PRAGMA
        # table_info(items) first instead of blindly trusting
        # user_version, or this would crash every `sync` on every host
        # that ever hit it.
        isolated_config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(isolated_config.LEDGER_PATH)
        raw.execute("""
            CREATE TABLE items (
                citekey TEXT PRIMARY KEY,
                item_type TEXT,
                title TEXT,
                year TEXT,
                doi TEXT,
                url TEXT,
                pdf_path TEXT,
                pdf_hash TEXT,
                pdf_size INTEGER,
                pdf_mtime_ns INTEGER,
                status TEXT NOT NULL DEFAULT 'discovered',
                parsed_path TEXT,
                parse_error TEXT,
                last_synced TEXT NOT NULL
            )
        """)
        raw.commit()
        raw.close()

        con = ledger.connect()  # must not raise
        try:
            (version,) = con.execute("PRAGMA user_version").fetchone()
            assert version == len(ledger._MIGRATIONS)
        finally:
            con.close()


class TestUpsertReference:
    def test_new_item_with_pdf_needs_parse(self, ledger_con, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")
        ref = make_reference(pdf_path=str(pdf))
        assert ledger.upsert_reference(ledger_con, ref) is True

        row = ledger_con.execute(
            "SELECT status, pdf_hash FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "discovered"
        assert row[1] is not None

    def test_new_item_without_pdf_does_not_need_parse(self, ledger_con):
        ref = make_reference(pdf_path=None)
        assert ledger.upsert_reference(ledger_con, ref) is False

        row = ledger_con.execute(
            "SELECT status, pdf_hash FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "no_pdf"
        assert row[1] is None

    def test_unchanged_pdf_hash_does_not_need_reparse(self, ledger_con, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"same content")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, ref.citekey, tmp_path / "parsed.txt")

        assert ledger.upsert_reference(ledger_con, ref) is False
        row = ledger_con.execute(
            "SELECT status FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "parsed"  # status preserved, not reset

    def test_changed_pdf_hash_needs_reparse(self, ledger_con, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"version 1")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, ref.citekey, tmp_path / "parsed.txt")

        pdf.write_bytes(b"version 2, totally different")
        assert ledger.upsert_reference(ledger_con, ref) is True
        row = ledger_con.execute(
            "SELECT status FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "discovered"

    def test_pdf_removed_goes_back_to_no_pdf(self, ledger_con, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"content")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)

        ref_no_pdf = make_reference(pdf_path=None)
        needs_parse = ledger.upsert_reference(ledger_con, ref_no_pdf)
        assert needs_parse is False
        row = ledger_con.execute(
            "SELECT status, pdf_hash FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row == ("no_pdf", None)

    def test_updates_bibliographic_fields_in_place(self, ledger_con):
        ref = make_reference(title="Original Title")
        ledger.upsert_reference(ledger_con, ref)
        ref2 = make_reference(title="Updated Title")
        ledger.upsert_reference(ledger_con, ref2)

        row = ledger_con.execute(
            "SELECT title FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "Updated Title"
        assert len(ledger.known_citekeys(ledger_con)) == 1

    def test_item_gaining_a_pdf_after_being_no_pdf_needs_parse(self, ledger_con, tmp_path):
        # Row already exists (from the earlier no-pdf sync) but its stored
        # pdf_hash is NULL -- the stat-unchanged fast path must not treat
        # a NULL stored hash as "nothing changed" just because there's
        # nothing yet to compare a size/mtime against.
        ref_no_pdf = make_reference(pdf_path=None)
        ledger.upsert_reference(ledger_con, ref_no_pdf)

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"now has content")
        ref_with_pdf = make_reference(pdf_path=str(pdf))
        assert ledger.upsert_reference(ledger_con, ref_with_pdf) is True

        row = ledger_con.execute(
            "SELECT status, pdf_hash FROM items WHERE citekey = ?", (ref_with_pdf.citekey,)
        ).fetchone()
        assert row[0] == "discovered"
        assert row[1] is not None


class TestUpsertReferenceRehashSkip:
    """`sync` re-hashing the entire PDF corpus on every no-op run doesn't
    scale (audited at 1.37GB) -- upsert_reference must compare (size,
    mtime) against what was last recorded and only fall back to
    sha256-hashing the file when that doesn't match."""

    def test_unchanged_size_and_mtime_skips_hashing(self, ledger_con, tmp_path, monkeypatch):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"stable content")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)

        calls = []
        original_hash_pdf = ledger._hash_pdf

        def spy(path):
            calls.append(path)
            return original_hash_pdf(path)

        monkeypatch.setattr(ledger, "_hash_pdf", spy)

        needs_parse = ledger.upsert_reference(ledger_con, ref)

        assert needs_parse is False
        assert calls == []  # (size, mtime) matched -- hash was never recomputed

    def test_changed_mtime_with_same_size_still_rehashes(self, ledger_con, tmp_path, monkeypatch):
        # Same byte length, deliberately touched to a different mtime --
        # size alone matching must not be treated as proof of unchanged
        # content.
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"AAAAAAAAAA")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)

        future = time.time() + 5
        os.utime(pdf, (future, future))

        calls = []
        original_hash_pdf = ledger._hash_pdf

        def spy(path):
            calls.append(path)
            return original_hash_pdf(path)

        monkeypatch.setattr(ledger, "_hash_pdf", spy)

        ledger.upsert_reference(ledger_con, ref)

        assert calls == [str(pdf)]

    def test_stored_size_and_mtime_are_persisted(self, ledger_con, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"content")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)

        st = pdf.stat()
        row = ledger_con.execute(
            "SELECT pdf_size, pdf_mtime_ns FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row == (st.st_size, st.st_mtime_ns)


class TestMarkParsed:
    def test_sets_status_and_clears_error(self, ledger_con, tmp_path):
        ref = make_reference()
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parse_failed(ledger_con, ref.citekey, "boom")

        parsed_path = tmp_path / "out.txt"
        ledger.mark_parsed(ledger_con, ref.citekey, parsed_path)

        row = ledger_con.execute(
            "SELECT status, parsed_path, parse_error FROM items WHERE citekey = ?",
            (ref.citekey,),
        ).fetchone()
        assert row == ("parsed", str(parsed_path), None)


class TestMarkParseFailed:
    def test_sets_status_and_error(self, ledger_con):
        ref = make_reference()
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parse_failed(ledger_con, ref.citekey, "pdftotext exploded")

        row = ledger_con.execute(
            "SELECT status, parse_error FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row == ("parse_failed", "pdftotext exploded")


class TestKnownCitekeysAndAllItems:
    def test_known_citekeys_empty_ledger(self, ledger_con):
        assert ledger.known_citekeys(ledger_con) == set()

    def test_known_citekeys_reflects_inserts(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a_2024"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="b_2024"))
        assert ledger.known_citekeys(ledger_con) == {"a_2024", "b_2024"}

    def test_all_items_ordered_by_citekey(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="zeta_2024"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="alpha_2024"))
        rows = ledger.all_items(ledger_con)
        assert [r["citekey"] for r in rows] == ["alpha_2024", "zeta_2024"]

    def test_all_items_row_supports_column_access(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a_2024", title="Title A"))
        rows = ledger.all_items(ledger_con)
        assert rows[0]["title"] == "Title A"


class TestFindStale:
    """Read-only counterpart to prune_missing -- must never delete."""

    def test_finds_citekey_no_longer_in_bib(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="orphaned_key"))

        stale = ledger.find_stale(ledger_con, seen_citekeys={"kept_key"})

        assert [k for k, _ in stale] == ["orphaned_key"]
        # Unlike prune_missing, nothing is actually removed.
        assert ledger.known_citekeys(ledger_con) == {"kept_key", "orphaned_key"}

    def test_no_stale_citekeys_is_empty(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))

        assert ledger.find_stale(ledger_con, seen_citekeys={"kept_key"}) == []

    def test_never_raises_even_when_seen_citekeys_is_empty(self, ledger_con):
        # prune_missing refuses (raises) on this exact shape -- find_stale
        # is read-only, so there's nothing destructive to guard against.
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))

        stale = ledger.find_stale(ledger_con, seen_citekeys=set())

        assert [k for k, _ in stale] == ["kept_key"]
        assert ledger.known_citekeys(ledger_con) == {"kept_key"}


class TestPruneMissing:
    """Without this, a citekey removed from bibliography.bib stays "known"
    to citation_gate forever -- the fabricated-citekey failure mode
    AGENTS.md's invariant exists to prevent, just arriving via deletion
    instead of invention."""

    def test_removes_citekeys_no_longer_in_bib(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="orphaned_key"))

        removed = ledger.prune_missing(ledger_con, seen_citekeys={"kept_key"})

        assert [k for k, _ in removed] == ["orphaned_key"]
        assert ledger.known_citekeys(ledger_con) == {"kept_key"}

    def test_no_orphans_is_a_no_op(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))

        removed = ledger.prune_missing(ledger_con, seen_citekeys={"kept_key"})

        assert removed == []
        assert ledger.known_citekeys(ledger_con) == {"kept_key"}

    def test_pruned_citekey_is_no_longer_known(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))
        ledger.upsert_reference(ledger_con, make_reference(citekey="removed_from_bib"))
        ledger.prune_missing(ledger_con, seen_citekeys={"kept_key"})

        assert "removed_from_bib" not in ledger.known_citekeys(ledger_con)

    def test_returns_parsed_path_for_caller_cleanup(self, ledger_con, tmp_path):
        parsed_path = tmp_path / "orphaned_key.txt"
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))
        ref = make_reference(citekey="orphaned_key")
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, "orphaned_key", parsed_path)

        removed = ledger.prune_missing(ledger_con, seen_citekeys={"kept_key"})

        assert removed == [("orphaned_key", str(parsed_path))]

    def test_refuses_to_prune_everything_when_bib_yields_nothing(self, ledger_con):
        # A real bib file with entries should never legitimately produce a
        # prune call with an empty seen_citekeys set while the ledger has
        # existing items -- that shape signals a corrupted/misconfigured
        # bib export (BIB_FILE pointing at the wrong path, a truncated
        # re-export), not "every citekey was deleted on purpose." Refusing
        # loudly here is what stops sync from silently wiping the ledger
        # and making every existing draft's citations look fabricated on
        # the very next citation_gate run.
        ledger.upsert_reference(ledger_con, make_reference(citekey="kept_key"))

        with pytest.raises(RuntimeError, match="Refusing to prune"):
            ledger.prune_missing(ledger_con, seen_citekeys=set())

        assert ledger.known_citekeys(ledger_con) == {"kept_key"}

    def test_empty_ledger_with_empty_seen_citekeys_is_still_a_no_op(self, ledger_con):
        # The guard above must not fire when there's nothing to protect --
        # a genuinely empty ledger (fresh project, nothing synced yet)
        # paired with a genuinely empty bib file is a normal, un-suspicious
        # state, not a signal of corruption.
        assert ledger.prune_missing(ledger_con, seen_citekeys=set()) == []


class TestFailedParseIsRetried:
    """A parse that failed must be retried on the next sync.

    Before this, needs_parse was true only for a new row or a changed
    pdf_hash, so a `parse_failed` document was skipped forever unless its
    PDF *bytes* changed. Harmless when failures were per-document and
    permanent (a corrupt PDF); not harmless once a worker pool exists,
    because a single dead worker marks every in-flight document
    parse_failed. Under an unattended cron that silently removes those
    documents from the corpus for good: every later run counts them
    "unchanged" and exits 0.
    """

    def test_parse_failed_is_retried_while_the_pdf_is_unchanged(
        self, isolated_config, ledger_con, tmp_path
    ):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")
        ref = make_reference(pdf_path=str(pdf))

        assert ledger.upsert_reference(ledger_con, ref) is True
        ledger.mark_parse_failed(ledger_con, ref.citekey, "worker died")

        # Same bytes, same mtime -- and it must still come back for retry.
        assert ledger.upsert_reference(ledger_con, ref) is True

    def test_a_successful_parse_is_still_not_redone(
        self, isolated_config, ledger_con, tmp_path
    ):
        """The incremental skip is the whole point of the ledger; only
        the failed state is retried."""
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")
        ref = make_reference(pdf_path=str(pdf))

        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, ref.citekey, tmp_path / "out.txt")

        assert ledger.upsert_reference(ledger_con, ref) is False

    def test_retrying_resets_the_status_so_it_is_not_reported_as_failed(
        self, isolated_config, ledger_con, tmp_path
    ):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 content")
        ref = make_reference(pdf_path=str(pdf))
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parse_failed(ledger_con, ref.citekey, "worker died")

        ledger.upsert_reference(ledger_con, ref)
        row = ledger_con.execute(
            "SELECT status FROM items WHERE citekey = ?", (ref.citekey,)
        ).fetchone()
        assert row[0] == "discovered"

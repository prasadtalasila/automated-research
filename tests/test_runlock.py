"""src/runlock.py: one writer at a time across content/.

Deliberately a sqlite file used purely as a mutex, not an O_EXCL lock
file and not the ledger itself. The experiment behind that choice, run
against real sqlite before any of this was written:

  - a connection holding BEGIN IMMEDIATE does NOT block other processes
    from READING, so citation_gate and the drafting skills keep working
    during a sync (a RESERVED lock permits readers);
  - a second process attempting BEGIN IMMEDIATE gets SQLITE_BUSY, so it
    works as a mutex;
  - after kill -9 on the holder the lock is released immediately, so
    stale locks self-heal with no PID liveness check -- which is the
    part an O_EXCL lock file cannot do portably;
  - a bare BEGIN IMMEDIATE with no write still holds it.

The ledger itself is not used, because src/ledger.py commits at five
separate points: wrapping a run in one transaction would trade
incremental durability for the mutex.
"""

import json
import multiprocessing
import os
import pathlib
import sqlite3
import time

import pytest

from src import config, runlock


def _hold(path, started, release):
    """Child process: take the lock, signal, wait to be told to drop it."""
    with runlock.pipeline_lock(path):
        started.set()
        release.wait(30)


class TestAcquire:
    def test_the_lock_can_be_taken_and_released(self, tmp_path):
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            pass
        with runlock.pipeline_lock(path):
            pass

    def test_a_second_holder_in_the_same_process_is_refused(self, tmp_path):
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            with pytest.raises(runlock.AlreadyRunning):
                with runlock.pipeline_lock(path):
                    pass

    def test_the_message_says_what_to_do(self, tmp_path):
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            with pytest.raises(runlock.AlreadyRunning) as excinfo:
                with runlock.pipeline_lock(path):
                    pass
        message = str(excinfo.value)
        # The lock dies with its holder, so "another run holds it" is
        # always true when this is seen -- the message can say so plainly.
        assert "already running" in message.lower()
        assert "nothing is lost" in message.lower()

    def test_the_lock_file_is_created_with_its_parent(self, tmp_path):
        path = tmp_path / "fresh" / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            assert path.exists()

    def test_the_lock_file_is_never_deleted(self, tmp_path):
        """Unlinking it is unsafe: on Windows removing an open file
        fails, and on POSIX a delete-then-recreate race gives two
        processes locks on different inodes, both believing they hold it."""
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            pass
        assert path.exists()


class TestReadersAreNotBlocked:
    def test_a_reader_can_still_read_the_ledger_while_a_run_holds_the_lock(
        self, isolated_config, tmp_path
    ):
        """The lock is a separate file precisely so this stays true --
        citation_gate and the drafting skills read the ledger while sync
        writes it."""
        from src import ledger

        con = ledger.connect()
        con.close()
        with runlock.pipeline_lock(tmp_path / "pipeline.lock.db"):
            reader = sqlite3.connect(config.LEDGER_PATH, timeout=1)
            assert reader.execute("SELECT count(*) FROM items").fetchone()[0] == 0
            reader.close()


class TestAcrossProcesses:
    def test_a_second_process_is_refused_while_the_first_holds_it(self, tmp_path):
        path = str(tmp_path / "pipeline.lock.db")
        ctx = multiprocessing.get_context("spawn")
        started, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold, args=(path, started, release))
        holder.start()
        try:
            assert started.wait(30), "holder never acquired the lock"
            with pytest.raises(runlock.AlreadyRunning):
                with runlock.pipeline_lock(path):
                    pass
        finally:
            release.set()
            holder.join(30)

    def test_a_killed_holder_releases_the_lock(self, tmp_path):
        """The property that makes this design better than a PID file:
        no liveness check, no staleness heuristic, no platform-specific
        code -- the OS closing the fd is what releases it."""
        path = str(tmp_path / "pipeline.lock.db")
        ctx = multiprocessing.get_context("spawn")
        started, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold, args=(path, started, release))
        holder.start()
        assert started.wait(30), "holder never acquired the lock"
        holder.kill()
        holder.join(30)

        deadline = time.monotonic() + 10
        while True:
            try:
                with runlock.pipeline_lock(path):
                    break
            except runlock.AlreadyRunning:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)


class TestFailuresThatAreNotContention:
    def test_an_unusable_lock_file_is_not_reported_as_another_run(self, tmp_path):
        """OperationalError also covers disk-full, permissions and a
        corrupt file. Reporting those as "already running" would send
        someone hunting for a process that does not exist."""
        path = tmp_path / "pipeline.lock.db"
        path.write_text("this is not a sqlite database")
        with pytest.raises(sqlite3.DatabaseError):
            with runlock.pipeline_lock(path):
                pass


class TestCleanup:
    def test_a_failure_that_is_not_busy_still_closes_the_connection(
        self, tmp_path, monkeypatch
    ):
        """Whatever goes wrong during acquisition, the connection must
        not be left open -- an open connection is a held lock."""
        path = tmp_path / "pipeline.lock.db"
        lock = runlock.pipeline_lock(path)
        closed = []

        class ExplodingConnection:
            def execute(self, *_args):
                raise MemoryError("boom")

            def close(self):
                closed.append(True)

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: ExplodingConnection())
        with pytest.raises(MemoryError):
            lock.__enter__()
        assert closed, "the connection was left open, i.e. the lock was left held"
        assert lock._con is None

        # ...and the lock is genuinely free afterwards.
        monkeypatch.undo()
        with runlock.pipeline_lock(path):
            pass

    def test_exiting_without_having_acquired_is_harmless(self, tmp_path):
        """__exit__ can run after a failed __enter__ (a `with` that
        raised), and must not blow up on the connection it never got."""
        lock = runlock.pipeline_lock(tmp_path / "pipeline.lock.db")
        assert lock.__exit__(None, None, None) is False

    def test_a_non_busy_operational_error_is_reraised_not_misreported(
        self, tmp_path, monkeypatch
    ):
        """A full disk or an unwritable content/ raises OperationalError
        too. Reporting that as "another run is already running" would
        send someone hunting for a process that does not exist, so the
        error *code* decides, not the exception type."""
        class FailingConnection:
            def execute(self, *_args):
                raise sqlite3.OperationalError("disk I/O error")

            def close(self):
                pass

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: FailingConnection())
        with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
            with runlock.pipeline_lock(tmp_path / "pipeline.lock.db"):
                pass


class TestHolderVisibility:
    """Exit 2 is the one code that carries no information about how long
    it has been true. The lock message says "nothing is lost -- the next
    run continues", which is reassuring and wrong if a run has been
    wedged for a week: every cycle exits 2 and the pipeline silently
    stops making progress.
    """

    def test_the_refusal_reports_how_long_the_holder_has_been_running(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            with pytest.raises(runlock.AlreadyRunning) as excinfo:
                with runlock.pipeline_lock(path):
                    pass
        message = str(excinfo.value)
        assert "started" in message.lower()
        assert "pid" in message.lower()

    def test_a_long_held_lock_escalates_the_wording(self, tmp_path, monkeypatch):
        """A grep-able marker, so an unattended caller can alert on it
        rather than on exit 2 alone -- which is normal."""
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            monkeypatch.setattr(runlock, "_STUCK_AFTER_SECONDS", 0.0)
            with pytest.raises(runlock.AlreadyRunning) as excinfo:
                with runlock.pipeline_lock(path):
                    pass
        assert "POSSIBLY STUCK" in str(excinfo.value)

    def test_holder_details_live_beside_the_lock_not_inside_it(self, tmp_path):
        """They must not be a row in the lock database. Writing one means
        committing on the lock connection, and a COMMIT *releases*
        BEGIN IMMEDIATE -- so commit-then-reacquire opens a window where
        a second process can take the lock while this one still believes
        it holds it. A sidecar touches the transaction not at all."""
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            data = json.loads((tmp_path / "pipeline.lock.db.holder").read_text())
        assert data["pid"] == os.getpid()

    def test_recording_the_holder_never_costs_the_lock(self, tmp_path, monkeypatch):
        """The details are advisory. If the sidecar cannot be written,
        the lock we just won must still be held."""
        path = tmp_path / "pipeline.lock.db"

        def refuse(*_a, **_k):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(pathlib.Path, "write_text", refuse)
        with runlock.pipeline_lock(path):
            monkeypatch.undo()
            with pytest.raises(runlock.AlreadyRunning):
                with runlock.pipeline_lock(path):
                    pass

    def test_a_missing_holder_row_does_not_break_the_refusal(self, tmp_path, monkeypatch):
        """Defensive: an older lock file has no holder table, and failing
        to describe the holder must not replace a useful refusal with a
        crash."""
        path = tmp_path / "pipeline.lock.db"
        with runlock.pipeline_lock(path):
            monkeypatch.setattr(runlock, "_describe_holder", lambda _p: None)
            with pytest.raises(runlock.AlreadyRunning) as excinfo:
                with runlock.pipeline_lock(path):
                    pass
        assert "already running" in str(excinfo.value).lower()

    def test_a_missing_sidecar_is_handled(self, tmp_path):
        """A lock file from a version that didn't write one: describe
        nothing rather than crash."""
        path = tmp_path / "pipeline.lock.db"
        path.write_bytes(b"")
        assert runlock._describe_holder(path) is None

    def test_a_corrupt_sidecar_is_handled(self, tmp_path):
        path = tmp_path / "pipeline.lock.db"
        (tmp_path / "pipeline.lock.db.holder").write_text("{not json")
        assert runlock._describe_holder(path) is None

    def test_the_refusal_survives_an_undescribable_holder(self, tmp_path):
        """_refusal_message must still say the useful part."""
        message = runlock._refusal_message(tmp_path / "never-created.db")
        assert "already running" in message.lower()
        assert "nothing is lost" in message.lower()

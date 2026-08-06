"""One writer at a time across content/.

`sync` and `scripts/full_pipeline.py` both write there, and the planned
cron job makes overlap a matter of time rather than bad luck. Two runs
overlapping is not merely wasteful: sync's parsed-text writes are not
atomic, so a concurrent heavy run can read a half-written
content/parsed/<citekey>.txt, and both can contend for the same GPUs.

The mutex is a dedicated sqlite file, held under `BEGIN IMMEDIATE` by its
own connection. That choice was made from measurement, not taste --
against real sqlite, before any of this was written:

  - a connection holding BEGIN IMMEDIATE does **not** block other
    processes from reading, because it takes a RESERVED lock rather than
    an EXCLUSIVE one. `citation_gate`, `retrieval` and the drafting
    skills keep working while a sync runs. (This was the objection that
    nearly ruled the design out; it turned out to be false.)
  - a second process attempting BEGIN IMMEDIATE gets SQLITE_BUSY, so it
    is a real mutex.
  - after `kill -9` on the holder, the lock is released **immediately**.
    That is the property that decides this design: staleness handles
    itself, with no PID liveness check and no platform-specific code for
    the CI leg nobody watches.
  - a bare BEGIN IMMEDIATE with no write still holds it, so the lock
    connection never has to dirty anything.

Two alternatives, and why not:

  - An `os.open(O_CREAT|O_EXCL)` lock file needs stale-lock detection,
    which means `os.kill(pid, 0)` on POSIX and something else on Windows
    -- exactly the kind of branch that rots on the untested platform.
  - Locking `content/ledger.sqlite` itself would force the whole run into
    one transaction. `src/ledger.py` commits at five separate points, so
    that would trade incremental durability for the mutex: a crash at 90%
    would discard every parse recorded up to then.

Portability note: this inherits sqlite's own locking, which is
unreliable on network filesystems. That is not a new constraint -- the
ledger already depends on it -- so `content/` must live on a local
filesystem either way.

Scope: this serialises *writers*. Readers are deliberately unaffected,
which is the point of the separate file.
"""

import json
import os
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src import config


# A run holding the lock for longer than this is *probably* wedged
# rather than busy. Deliberately generous -- a full first-time parse of a
# large corpus is legitimately long -- because this only changes the
# wording, never the behaviour. It exists so an unattended caller has
# something to alert on other than exit 2, which is normal.
_STUCK_AFTER_SECONDS = 6 * 60 * 60

class AlreadyRunning(RuntimeError):
    """Another sync or enrichment run holds the lock."""


# Exit code for "another run holds the lock", distinct from 1 ("ran, and
# something failed") so an unattended caller can tell a skipped cycle
# from a real failure without parsing output.
EXIT_ALREADY_RUNNING = 2


def _describe_holder(path) -> "tuple[int, str, str, float] | None":
    """(pid, host, started_at, age_seconds) for the current holder, or
    None if that can't be read.

    Never raises: failing to describe the holder must not replace a
    useful refusal with a traceback. The details can also be stale (a
    previous holder's, if this one hasn't written yet), which is why the
    message presents them as advisory and nothing depends on them.
    """
    try:
        data = json.loads(Path(str(path) + ".holder").read_text())
        started = datetime.fromisoformat(data["started_at"])
        return (data["pid"], data["host"], data["started_at"],
                (datetime.now(timezone.utc) - started).total_seconds())
    except Exception:  # noqa: BLE001 -- see docstring
        return None


def _refusal_message(path) -> str:
    base = (
        f"another sync or pipeline run is already running (it holds {path}), so "
        "this run was skipped. Nothing is lost -- the pipeline is incremental, "
        "and the next run continues from where this one would have started."
    )
    holder = _describe_holder(path)
    if holder is None:
        return base
    pid, host, started_at, age = holder
    base += f" That run: pid {pid} on {host}, started {started_at} ({age / 60:.0f} min ago)."
    if age >= _STUCK_AFTER_SECONDS:
        # A distinct, grep-able phrase: exit 2 alone is normal and says
        # nothing about duration, so an unattended caller needs a signal
        # that "skipped" has stopped being benign.
        base += (
            " POSSIBLY STUCK: it has held the lock far longer than a run should "
            "take, and every run since has been skipped. Check that process is "
            "alive and making progress; the lock frees itself once it exits."
        )
    else:
        base += (
            " The lock frees itself when that run exits, including on a crash, so "
            "if you believe none is active then one really is still alive."
        )
    return base


class pipeline_lock:
    """Hold the pipeline lock for the duration of a `with` block.

    The connection is kept on the instance, not in a local, and that is
    load-bearing: the lock lives exactly as long as the connection
    object. If it were a local that fell out of scope, CPython would
    close it and silently release the lock mid-run -- the classic way
    this pattern fails.
    """

    def __init__(self, path=None):
        self._path = Path(path) if path is not None else config.PIPELINE_LOCK_PATH
        self._con = None

    def _record_holder(self) -> None:
        """Note who we are, in a plain file beside the lock.

        Deliberately NOT a row in the lock database. Writing one would
        mean committing on the lock connection, and a COMMIT *releases*
        BEGIN IMMEDIATE -- so a commit-then-reacquire sequence opens a
        window where a second process can take the lock while this one
        still believes it holds it. Committing before acquiring instead
        would make a loser read its own details rather than the winner's.
        A separate file avoids both: it touches the lock's transaction
        not at all.

        Best-effort. These details are advisory -- they exist for a human
        deciding whether to go looking for a process -- so failing to
        write them must never cost the lock we just won.
        """
        try:
            self._holder_path().write_text(json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }))
        except OSError:
            pass

    def _holder_path(self):
        return self._path.with_name(self._path.name + ".holder")

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # timeout=0: fail immediately rather than sitting for sqlite's
        # default five seconds. Failing fast is right here because sync
        # is incremental and idempotent -- a skipped cron cycle costs
        # nothing, and the next one picks up the work.
        #
        # isolation_level=None puts the connection in explicit-transaction
        # mode, so the BEGIN IMMEDIATE below is the only transaction that
        # will ever exist on it. Left at the default, sqlite3's implicit
        # transaction handling can start one first and turn this into
        # "cannot start a transaction within a transaction".
        self._con = sqlite3.connect(self._path, timeout=0, isolation_level=None)
        try:
            self._con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            self._con.close()
            self._con = None
            # Checked by error *code*, not by matching the message:
            # OperationalError also covers a full disk, a permissions
            # problem and a corrupt file, and reporting those as "another
            # run is in progress" would send someone hunting a process
            # that does not exist.
            if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY:
                raise
            raise AlreadyRunning(_refusal_message(self._path)) from exc
        except Exception:
            self._con.close()
            self._con = None
            raise
        # Only after the lock is genuinely held: the details describe the
        # winner, and writing them cannot affect whether we won.
        self._record_holder()
        return self

    def __exit__(self, *exc_info):
        if self._con is not None:
            # ROLLBACK, not COMMIT: the transaction exists only to hold
            # the lock and has written nothing.
            #
            # The file itself is never deleted. On Windows unlinking an
            # open file fails, and on POSIX a delete-then-recreate race
            # would give two processes locks on different inodes, each
            # believing it holds the only one.
            try:
                self._con.execute("ROLLBACK")
            finally:
                self._con.close()
                self._con = None
        return False

"""SQLite ledger tracking per-citekey pipeline status.

The ledger lets `sync` be incremental: a paper is only re-parsed if its
PDF content actually changed, not on every run. Change detection is
two-stage: a cheap (size, mtime) stat comparison first, and a sha256
content hash only when that stat doesn't match what was last recorded
(or there's nothing recorded yet) -- see upsert_reference. This is the
state that makes the deterministic pipeline safe to run unattended/on a
schedule, including on a corpus large enough that re-hashing every PDF
on every no-op run would dominate the run's wall-clock time.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src import config

if TYPE_CHECKING:
    # Only for the upsert_reference type hint -- citation_gate.py imports
    # this module and must not require bibtexparser (src/bib_reader.py's
    # only dependency) just to check citekeys against the ledger.
    from src.bib_reader import Reference

# _SCHEMA only ever describes the *original* table shape (schema version
# 0) -- every column added since is a migration in _MIGRATIONS below, not
# an edit here. That way a brand-new database and an existing one predating
# a migration go through the exact same code path in _migrate (both start
# at user_version 0), instead of _SCHEMA silently being "current" for a
# fresh file while an existing file still needs ALTER TABLE.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
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
);
"""

# Ordered, one tuple of (column_name, "ADD COLUMN" statement) pairs per
# schema version -- version N is "the first N tuples have been applied".
# Tracked via PRAGMA user_version (a plain integer stored in the database
# file itself) so an already-migrated ledger -- the common case on every
# routine `sync` -- skips straight past the loop below on a single
# integer comparison. Each statement is still paired with the column name
# it adds and re-checked against PRAGMA table_info(items) before running
# (_migrate below) rather than trusted on user_version alone: user_version
# and the table's actual shape could in principle disagree (e.g. a future
# column added directly to _SCHEMA instead of here), and "ADD COLUMN" on
# a column that already exists raises, so this is what keeps that
# specific mistake from crashing every `sync` on every host instead of
# just being a no-op.
_MIGRATIONS: list[tuple[tuple[str, str], ...]] = [
    (
        # version 1: upsert_reference's stat-before-hash skip (module
        # docstring) needs somewhere to persist the (size, mtime) last
        # observed for a given PDF, so a subsequent no-op sync can compare
        # against that instead of re-reading and sha256-hashing
        # potentially gigabytes of PDF content.
        ("pdf_size", "ALTER TABLE items ADD COLUMN pdf_size INTEGER"),
        ("pdf_mtime_ns", "ALTER TABLE items ADD COLUMN pdf_mtime_ns INTEGER"),
    ),
]


def _migrate(con: sqlite3.Connection) -> None:
    (current,) = con.execute("PRAGMA user_version").fetchone()
    target = len(_MIGRATIONS)
    if current >= target:
        return
    existing_cols = {row[1] for row in con.execute("PRAGMA table_info(items)")}
    for steps in _MIGRATIONS[current:target]:
        for column, statement in steps:
            if column not in existing_cols:
                con.execute(statement)
    # PRAGMA user_version doesn't accept `?` parameter binding -- target
    # is this module's own len(_MIGRATIONS), never user input.
    con.execute(f"PRAGMA user_version = {target}")


def connect() -> sqlite3.Connection:
    config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.LEDGER_PATH)
    con.execute(_SCHEMA)
    _migrate(con)
    con.commit()
    return con


def _hash_pdf(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stat_pdf(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def upsert_reference(con: sqlite3.Connection, ref: Reference) -> bool:
    """Insert or update a reference's bibliographic fields.

    Returns True if the PDF content is new/changed and needs (re-)parsing.
    """
    now = datetime.now(timezone.utc).isoformat()

    row = con.execute(
        "SELECT pdf_hash, pdf_size, pdf_mtime_ns, status FROM items WHERE citekey = ?",
        (ref.citekey,),
    ).fetchone()

    pdf_size = pdf_mtime_ns = None
    pdf_hash = None
    if ref.pdf_path:
        pdf_size, pdf_mtime_ns = _stat_pdf(ref.pdf_path)
        stat_unchanged = (
            row is not None
            and row[0] is not None
            and (row[1], row[2]) == (pdf_size, pdf_mtime_ns)
        )
        # Trust an unchanged (size, mtime) instead of re-hashing -- any
        # write to the file (even one that happens to reproduce the same
        # byte length) advances mtime, so this can only under-detect a
        # change on a filesystem/tool that leaves mtime untouched across a
        # real content edit, which sha256-hashing every PDF on every run
        # was never actually guarding against differently.
        pdf_hash = row[0] if stat_unchanged else _hash_pdf(ref.pdf_path)

    needs_parse = False
    if row is None:
        status = "discovered" if pdf_hash else "no_pdf"
        needs_parse = pdf_hash is not None
        con.execute(
            """
            INSERT INTO items
                (citekey, item_type, title, year, doi, url,
                 pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, status, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ref.citekey, ref.item_type, ref.title, ref.year,
             ref.doi, ref.url, ref.pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, status, now),
        )
    else:
        old_hash, _old_size, _old_mtime_ns, old_status = row
        if pdf_hash != old_hash:
            needs_parse = pdf_hash is not None
            new_status = "discovered" if pdf_hash else "no_pdf"
        else:
            new_status = old_status
        con.execute(
            """
            UPDATE items SET
                item_type = ?, title = ?, year = ?, doi = ?,
                url = ?, pdf_path = ?, pdf_hash = ?, pdf_size = ?, pdf_mtime_ns = ?,
                status = ?, last_synced = ?
            WHERE citekey = ?
            """,
            (ref.item_type, ref.title, ref.year, ref.doi,
             ref.url, ref.pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, new_status, now, ref.citekey),
        )
    con.commit()
    return needs_parse


def mark_parsed(con: sqlite3.Connection, citekey: str, parsed_path: Path) -> None:
    con.execute(
        "UPDATE items SET status = 'parsed', parsed_path = ?, parse_error = NULL WHERE citekey = ?",
        (str(parsed_path), citekey),
    )
    con.commit()


def mark_parse_failed(con: sqlite3.Connection, citekey: str, error: str) -> None:
    con.execute(
        "UPDATE items SET status = 'parse_failed', parse_error = ? WHERE citekey = ?",
        (error, citekey),
    )
    con.commit()


def known_citekeys(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT citekey FROM items")}


def find_stale(con: sqlite3.Connection, seen_citekeys: set[str]) -> list[tuple[str, str | None]]:
    """Read-only: ledger rows whose citekey is no longer in the bib file.

    Never deletes anything -- this is what `sync`'s default (--remove-stale
    not passed) mode calls to report what a --remove-stale run would prune,
    without taking the destructive step. See prune_missing for the version
    that actually deletes.
    """
    rows = con.execute("SELECT citekey, parsed_path FROM items").fetchall()
    return [(citekey, parsed_path) for citekey, parsed_path in rows if citekey not in seen_citekeys]


def prune_missing(con: sqlite3.Connection, seen_citekeys: set[str]) -> list[tuple[str, str | None]]:
    """Removes ledger rows whose citekey is no longer in the bib file.

    Without this, a citekey removed from bibliography.bib (the source of
    truth) stays "known" to citation_gate forever -- exactly the fabricated-
    citekey failure mode AGENTS.md's invariant exists to prevent, just
    arriving via deletion instead of invention. Returns the removed
    (citekey, parsed_path) pairs so the caller can also clean up the
    now-orphaned parsed text file, though sync.py deliberately doesn't:
    only the row is what citation_gate actually checks, and pointing
    BIB_FILE at a smaller export is a documented, routine way to narrow
    the working set (and does, intentionally, prune the rows it excludes
    when --remove-stale is passed) -- but leaving the derived text in
    place means switching BIB_FILE back to a wider export later doesn't
    force a re-parse of PDFs whose text was already extracted.

    Only called when `sync --remove-stale` is passed (default: off, see
    sync.run) -- otherwise sync calls the read-only find_stale() instead,
    so an accidental citekey drop just gets reported, not deleted, until
    the user explicitly opts in.

    Refuses (raises) rather than pruning when seen_citekeys is empty but
    the ledger already has rows: bibliography.bib is a manual export
    (AGENTS.md), and a file that exists and parses cleanly but yields
    zero entries is far more likely to be a botched re-export, a
    truncated file, or BIB_FILE pointing at the wrong path than someone
    deliberately deleting their entire library. Pruning through that
    would wipe every row in one sync run and make citation_gate report
    every citekey in every existing draft as fabricated.
    """
    stale = find_stale(con, seen_citekeys)
    if not seen_citekeys and stale:
        # Query the total row count explicitly rather than reusing
        # len(stale) -- true today only because seen_citekeys is empty
        # (every row is trivially "stale"), but the message should stay
        # accurate even if this guard's condition changes later.
        (total,) = con.execute("SELECT COUNT(*) FROM items").fetchone()
        raise RuntimeError(
            f"Refusing to prune: the bib file yielded 0 references but the "
            f"ledger has {total} existing item(s). This almost always "
            "means the bib file is empty, corrupted, or misconfigured "
            "(BIB_FILE pointing at the wrong path, a truncated re-export) "
            "rather than every citekey being legitimately removed. Fix the "
            "bib file/BIB_FILE and re-run sync -- if this really is "
            "intentional, delete content/ledger.sqlite directly instead."
        )
    if stale:
        con.executemany("DELETE FROM items WHERE citekey = ?", [(k,) for k, _ in stale])
        con.commit()
    return stale


def all_items(con: sqlite3.Connection) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM items ORDER BY citekey").fetchall()
    con.row_factory = None
    return rows

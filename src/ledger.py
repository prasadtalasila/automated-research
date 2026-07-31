"""SQLite ledger tracking per-citekey pipeline status.

The ledger lets `sync` be incremental: a paper is only re-parsed if its
PDF content actually changed (tracked via a content hash), not on every
run. This is the state that makes the deterministic pipeline safe to
run unattended/on a schedule.
"""

from __future__ import annotations

import hashlib
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


def connect() -> sqlite3.Connection:
    config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.LEDGER_PATH)
    con.execute(_SCHEMA)
    con.commit()
    return con


def _hash_pdf(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_reference(con: sqlite3.Connection, ref: Reference) -> bool:
    """Insert or update a reference's bibliographic fields.

    Returns True if the PDF content is new/changed and needs (re-)parsing.
    """
    pdf_hash = _hash_pdf(ref.pdf_path) if ref.pdf_path else None
    now = datetime.now(timezone.utc).isoformat()

    row = con.execute(
        "SELECT pdf_hash, status FROM items WHERE citekey = ?", (ref.citekey,)
    ).fetchone()

    needs_parse = False
    if row is None:
        status = "discovered" if pdf_hash else "no_pdf"
        needs_parse = pdf_hash is not None
        con.execute(
            """
            INSERT INTO items
                (citekey, item_type, title, year, doi, url,
                 pdf_path, pdf_hash, status, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ref.citekey, ref.item_type, ref.title, ref.year,
             ref.doi, ref.url, ref.pdf_path, pdf_hash, status, now),
        )
    else:
        old_hash, old_status = row
        if pdf_hash != old_hash:
            needs_parse = pdf_hash is not None
            new_status = "discovered" if pdf_hash else "no_pdf"
        else:
            new_status = old_status
        con.execute(
            """
            UPDATE items SET
                item_type = ?, title = ?, year = ?, doi = ?,
                url = ?, pdf_path = ?, pdf_hash = ?, status = ?, last_synced = ?
            WHERE citekey = ?
            """,
            (ref.item_type, ref.title, ref.year, ref.doi,
             ref.url, ref.pdf_path, pdf_hash, new_status, now, ref.citekey),
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
        # seen_citekeys empty => every ledger row is "stale" by
        # definition, so len(stale) here is the ledger's full row count.
        raise RuntimeError(
            f"Refusing to prune: the bib file yielded 0 references but the "
            f"ledger has {len(stale)} existing item(s). This almost always "
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

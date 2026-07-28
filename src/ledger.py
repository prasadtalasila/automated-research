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


def all_items(con: sqlite3.Connection) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM items ORDER BY citekey").fetchall()
    con.row_factory = None
    return rows

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
import json
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
    (
        # version 2: 'parse_failed' conflated two failures that want
        # opposite handling. A worker dying marks every in-flight
        # document failed and must be retried, or one OOM silently
        # removes them from the corpus for good; a corrupt PDF must NOT
        # be re-parsed every run, because a sync that exits 1 forever
        # trains its reader to ignore exit 1 -- which is how the next
        # real failure gets missed.
        #
        # NULL means "recorded before this distinction existed", and is
        # deliberately treated as transient: those rows predate the
        # column, and retrying one corrupt PDF once is cheaper than
        # silently abandoning a document that failed for a transient
        # reason.
        ("failure_kind", "ALTER TABLE items ADD COLUMN failure_kind TEXT"),
    ),
    (
        # version 3: the entry's own BibTeX fields, verbatim, as a JSON
        # object -- authors, journal/booktitle, volume, pages, publisher,
        # everything the title/year/doi columns above don't keep.
        #
        # src/references.py needs them to write a real bibliography entry
        # rather than just "citekey -- Title (Year)", and it may not read
        # bibliography.bib to get them: src/bib_reader.py is the only
        # module allowed to (AGENTS.md), and it needs bibtexparser, while
        # references.py runs under bare python3. One opaque JSON column
        # rather than a column per field because nothing here queries or
        # indexes them -- they are only ever read back whole, for one
        # citekey at a time, to format an entry.
        #
        # NULL means "synced before this column existed". references.py
        # falls back to the title/year columns for those rather than
        # failing, so an existing ledger keeps working until the next
        # `python -m src.sync` backfills it.
        ("bib_fields", "ALTER TABLE items ADD COLUMN bib_fields TEXT"),
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


# Fields carried over verbatim from the BibTeX entry into the bib_fields
# column (_MIGRATIONS version 3), for src/references.py to format a full
# bibliography entry from. Deliberately a fixed allowlist rather than the
# whole entry dict: a reference manager's export carries per-host noise
# (`file` paths, `abstract`, `keywords`, timestamps, arbitrary `note`
# fields) that nothing formats and that would churn the ledger on every
# re-export. `title`/`year`/`doi` are omitted -- they have real columns.
_BIB_FIELDS_KEPT = (
    "author", "editor", "journal", "journaltitle", "booktitle", "series",
    "volume", "number", "pages", "publisher", "institution", "school",
    "address", "location", "edition", "howpublished", "organization",
    "eprint", "eprinttype", "archiveprefix", "primaryclass",
)


def _bib_fields_json(ref: Reference) -> str | None:
    """`ref`'s formatting-relevant BibTeX fields as a JSON object.

    Returns None (SQL NULL) when the entry has none of them, so "this
    entry genuinely carries no author or venue" and "this row predates
    the column" read the same to references.py -- both fall back to the
    title/year columns, which is the same output either way.
    """
    kept = {
        key: value for key, value in ref.fields.items()
        if key.lower() in _BIB_FIELDS_KEPT and str(value).strip()
    }
    # sort_keys so a re-sync of an unchanged entry writes a byte-identical
    # value rather than reordering it with the export's dict order.
    return json.dumps(kept, sort_keys=True) if kept else None


def upsert_reference(con: sqlite3.Connection, ref: Reference, force: bool = False) -> bool:
    """Insert or update a reference's bibliographic fields.

    Returns True if the PDF content is new/changed and needs (re-)parsing.
    """
    now = datetime.now(timezone.utc).isoformat()

    row = con.execute(
        "SELECT pdf_hash, pdf_size, pdf_mtime_ns, status, failure_kind "
        "FROM items WHERE citekey = ?",
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
        # Trust an unchanged (size, mtime) instead of re-hashing -- a
        # deliberate trade-off (PR #8 review): always-hashing *would*
        # still catch a same-size edit that also preserves mtime (e.g.
        # `cp --preserve=timestamps` overwriting the file in place), which
        # this stat-first check cannot. That's judged rare enough, and
        # re-reading every PDF's bytes on every run expensive enough at
        # this corpus's scale, to accept losing that one edge case.
        pdf_hash = row[0] if stat_unchanged else _hash_pdf(ref.pdf_path)

    needs_parse = False
    if force and pdf_hash is not None:
        # `sync --reparse`: re-extract regardless of what the ledger
        # believes. The point is to recover from output that is recorded
        # as fine but isn't -- which the ledger, by definition, cannot
        # detect on its own.
        needs_parse = True
    if row is None:
        status = "discovered" if pdf_hash else "no_pdf"
        needs_parse = pdf_hash is not None
        con.execute(
            """
            INSERT INTO items
                (citekey, item_type, title, year, doi, url,
                 pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, status, last_synced, bib_fields)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ref.citekey, ref.item_type, ref.title, ref.year,
             ref.doi, ref.url, ref.pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, status, now,
             _bib_fields_json(ref)),
        )
    else:
        old_hash, _old_size, _old_mtime_ns, old_status, old_kind = row
        if force and pdf_hash is not None:
            new_status = "discovered"
        elif pdf_hash != old_hash:
            needs_parse = pdf_hash is not None
            new_status = "discovered" if pdf_hash else "no_pdf"
        elif (old_status == "parse_failed" and pdf_hash is not None
                and old_kind != "deterministic"):
            # Retry a *transient* failed parse even though the PDF is
            # byte-identical -- a NULL kind predates the distinction and
            # counts as transient (see _MIGRATIONS version 2).
            # Without this, needs_parse was true only for a new or
            # changed document, so a failure stuck until the file itself
            # changed -- which for a corrupt PDF is never. That was
            # tolerable while failures were per-document and permanent;
            # it stopped being tolerable once a worker pool could mark
            # every in-flight document parse_failed on one worker's
            # death. Unattended, that silently drops those documents from
            # the corpus for good: each later run counts them "unchanged"
            # and exits 0. A retry costs one re-parse of a genuinely bad
            # PDF per run, which is visible and bounded; the alternative
            # is invisible and permanent.
            #
            # A deterministic failure deliberately falls through to the
            # else branch: it keeps its parse_failed status, so it is
            # still counted and still makes the run exit nonzero, but it
            # is not re-parsed. --reparse and editing the PDF are the two
            # escape hatches for a misclassification.
            needs_parse = True
            new_status = "discovered"
        else:
            new_status = old_status
        con.execute(
            """
            UPDATE items SET
                item_type = ?, title = ?, year = ?, doi = ?,
                url = ?, pdf_path = ?, pdf_hash = ?, pdf_size = ?, pdf_mtime_ns = ?,
                status = ?, last_synced = ?, bib_fields = ?
            WHERE citekey = ?
            """,
            (ref.item_type, ref.title, ref.year, ref.doi,
             ref.url, ref.pdf_path, pdf_hash, pdf_size, pdf_mtime_ns, new_status, now,
             _bib_fields_json(ref), ref.citekey),
        )
    con.commit()
    return needs_parse


def mark_parsed(con: sqlite3.Connection, citekey: str, parsed_path: Path) -> None:
    con.execute(
        "UPDATE items SET status = 'parsed', parsed_path = ?, parse_error = NULL WHERE citekey = ?",
        (str(parsed_path), citekey),
    )
    con.commit()


def mark_parse_failed(
    con: sqlite3.Connection, citekey: str, error: str, *, transient: bool = False
) -> None:
    """transient=True for a failure caused by the *run* rather than the
    document -- a dead pool worker, a stalled run. Those are retried
    automatically. The default is deterministic: the backend read this
    particular PDF and could not parse it, so re-reading it next run
    would only waste the same time again."""
    con.execute(
        "UPDATE items SET status = 'parse_failed', parse_error = ?, failure_kind = ? "
        "WHERE citekey = ?",
        (error, "transient" if transient else "deterministic", citekey),
    )
    con.commit()


def failure_counts(con: sqlite3.Connection) -> dict[str, int]:
    """{'transient': n, 'deterministic': n} over parse_failed rows.

    A NULL failure_kind counts as transient -- see _MIGRATIONS version 2.
    """
    counts = {"transient": 0, "deterministic": 0}
    for kind, n in con.execute(
        "SELECT failure_kind, count(*) FROM items WHERE status = 'parse_failed' "
        "GROUP BY failure_kind"
    ):
        counts["deterministic" if kind == "deterministic" else "transient"] += n
    return counts


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


# ---------------------------------------------------------------------
# Read-only CLI: `python3 -m src.ledger`
#
# Its own entrypoint rather than a `sync --inspect` flag, for two reasons
# that are both about what a *reader* needs. `sync` takes the pipeline
# write lock, so an inspect flag on it would exit 2 exactly when you most
# want to look -- during a run; this takes no lock at all, which is the
# property src/runlock.py's separate lock file exists to preserve. And
# `sync` needs bibtexparser, while reading the ledger needs only sqlite3,
# so this runs under the bare system interpreter like citation_gate and
# references do.
# ---------------------------------------------------------------------

_STATUS_LABELS = {
    "parsed": "parsed",
    "no_pdf": "no PDF attachment",
    "discovered": "found, not yet parsed",
    "parse_failed": "failed",
}


def _print_item(row) -> None:
    print(f"  {row['citekey']}")
    for field in ("status", "failure_kind", "item_type", "year", "doi",
                  "pdf_path", "parsed_path", "parse_error", "last_synced"):
        value = row[field] if field in row.keys() else None
        if value:
            print(f"      {field:<13} {value}")


def main(argv: "list[str] | None" = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m src.ledger",
        description="Show what the content layer holds. Read-only, takes no lock, "
                    "and runs with the bare system python3.",
    )
    parser.add_argument("--list", action="store_true", help="List every item")
    parser.add_argument("--status", help="List only items with this status "
                                         f"({', '.join(_STATUS_LABELS)})")
    parser.add_argument("--citekey", help="Show one item in full")
    args = parser.parse_args(argv)

    if not config.LEDGER_PATH.exists():
        print(f"No ledger at {config.LEDGER_PATH}.")
        print("Run `python -m src.sync` to build it from your bib file.")
        return 0

    # Opened read-only, NOT via connect(): connect() runs the schema and
    # migrations and commits, which takes a write lock and can bump
    # PRAGMA user_version. That would contradict this command's whole
    # reason for existing -- inspecting without interfering, including
    # while a sync is mid-run.
    con = sqlite3.connect(f"file:{config.LEDGER_PATH}?mode=ro", uri=True, timeout=0)
    # connect() leaves rows as tuples; this CLI addresses columns by name
    # so that adding one doesn't silently shift the output.
    con.row_factory = sqlite3.Row
    try:
        if args.citekey:
            row = con.execute(
                "SELECT * FROM items WHERE citekey = ?", (args.citekey,)
            ).fetchone()
            if row is None:
                print(f"{args.citekey} is not in the ledger.")
                return 1
            _print_item(row)
            return 0

        if args.status or args.list:
            rows = con.execute(
                "SELECT * FROM items WHERE (? IS NULL OR status = ?) ORDER BY citekey",
                (args.status, args.status),
            ).fetchall()
            if not rows:
                print(f"No items with status {args.status!r}.")
                return 0
            for row in rows:
                _print_item(row)
            print(f"\n  {len(rows)} item(s).")
            return 0

        counts = dict(con.execute("SELECT status, count(*) FROM items GROUP BY status"))
        total = sum(counts.values())
        if not total:
            print(f"Ledger at {config.LEDGER_PATH} is empty.")
            print("Run `python -m src.sync` to populate it from your bib file.")
            return 0

        print(f"Ledger: {config.LEDGER_PATH}   ({total} item(s) from "
              f"{config.BIB_FILE_PATH.name})\n")
        for status, label in _STATUS_LABELS.items():
            if counts.get(status):
                print(f"  {counts[status]:>4}  {label}")

        try:
            kinds = failure_counts(con)
        except sqlite3.OperationalError:
            # A ledger written before failure_kind existed. Read-only, so
            # it cannot be migrated here -- `python -m src.sync` does that.
            kinds = {"deterministic": 0, "transient": 0}
        if kinds["deterministic"]:
            print(f"\n  {kinds['deterministic']} item(s) need attention -- not retried "
                  "automatically.\n  Fix or remove the PDF, or re-run "
                  "`python -m src.sync --reparse`.")
            print("  See which: python3 -m src.ledger --status parse_failed")
        elif kinds["transient"]:
            print(f"\n  {kinds['transient']} item(s) failed for a transient reason "
                  "and will be retried on the next sync.")
        else:
            print("\n  Nothing needs attention.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())

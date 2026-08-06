"""Unified view over both corpus sources for the enrichment layer.

Two sources, two identifier namespaces, on purpose:

- Bib items (via the existing ledger): `doc_id == citekey`, whatever
  citekey the exported bib file assigned (src/bib_reader.py -- the bib
  file is the source of truth, this project doesn't generate its own).
  These are real, citable references that `python -m src.sync` pulled
  from it.
- `papers/pdfs/*.pdf` (config.toml's `[source_pdfs].dir` default): raw
  PDFs gathered outside the bib file (e.g. an open metadata-API search),
  with no citekey. `doc_id` is
  `doc:<filename stem>` -- a shape that can never be a real bib citekey
  (those never contain a colon) and that `citation_gate.py` will always
  reject, since it only checks membership in the ledger. Per AGENTS.md's
  invariant, these documents must not be cited until they are added to
  the reference manager, exported into the bib file, and picked up by
  `sync`.

The second source is the one that can surprise you, so this module reports
what it did with it rather than quietly widening the corpus. Two things
are said out loud, both as complaints the caller prints (the shape
`pdf_text.resolve_workers` uses):

1. **A source PDF the ledger already covers is skipped.** The two
   directories can overlap -- Zotero's exported attachments and
   `papers/pdfs/` both live under `papers/` -- and the same file ingested
   twice becomes two documents in Chroma and two rows in the topic model,
   one citable and one not, with nothing to say they are the same paper.
   It also happens legitimately over time: catalogue a raw PDF in the
   reference manager, re-export, re-run `sync`, and the copy in
   `papers/pdfs/` is now a duplicate of a citable row.
2. **Whatever remains is counted.** Those documents are real evidence a
   drafting agent can read and can never cite, and that is worth stating
   once per run rather than leaving to be noticed in a search result whose
   `citekey` is empty.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from src import config, ledger


@dataclass
class CorpusDoc:
    doc_id: str
    citekey: str | None  # None for source-pdfs docs; never invented
    source: str  # "bib" | "source-pdfs"
    title: str
    pdf_path: str | None
    text_path: str | None = None


def safe_filename(doc_id: str) -> str:
    """doc_id (possibly containing ':') -> a safe on-disk filename stem."""
    return doc_id.replace(":", "_")


def _source_pdf_manifest() -> dict[str, dict]:
    if not config.SOURCE_PDFS_MANIFEST.exists():
        return {}
    raw = json.loads(config.SOURCE_PDFS_MANIFEST.read_text())
    return {entry["file"]: entry for entry in raw.get("items", []) if entry.get("file")}


# Read size for hashing, matching src/ledger.py's own chunked read.
_CHUNK = 1 << 20


def _sha256(path) -> str | None:
    """Content digest, or None if the file can't be read.

    Matches src/ledger.py's own hashing so a digest computed here is
    comparable with the `pdf_hash` column it stores. Unreadable is not an
    error: the caller falls back to treating the file as new, which at
    worst re-adds a document that was already there.
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_CHUNK), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _real(path) -> str | None:
    try:
        return os.path.realpath(path)
    except OSError:
        return None


def _ledger_pdf_index(rows) -> tuple[dict, dict]:
    """Two lookups over the ledger's PDFs: by resolved path, and by size.

    Size rather than digest is the second key on purpose, mirroring
    src/ledger.py's own stat-before-hash skip: a size collision is rare
    and cheap to test, so a source PDF is only ever hashed when some
    ledger row is already the same length as it. On a corpus where the
    two directories don't overlap, that means no PDF is read at all.
    """
    by_path: dict[str, str] = {}
    by_size: dict[int, list[tuple[str, str]]] = {}
    for row in rows:
        pdf_path = row["pdf_path"]
        if not pdf_path:
            continue
        real = _real(pdf_path)
        if real:
            by_path[real] = row["citekey"]
        size, digest = row["pdf_size"], row["pdf_hash"]
        if size and digest:
            by_size.setdefault(size, []).append((digest, row["citekey"]))
    return by_path, by_size


def _already_citable(pdf_path: Path, by_path: dict, by_size: dict) -> str | None:
    """The citekey this source PDF duplicates, or None if it is new.

    Path first (free, and catches the exported-attachments-underneath
    case), then content (catches a copy saved under another name). A file
    that is merely the same *size* as a ledger PDF is not a duplicate --
    that is what the digest comparison is for.
    """
    real = _real(pdf_path)
    if real and real in by_path:
        return by_path[real]
    try:
        size = pdf_path.stat().st_size
    except OSError:
        return None
    candidates = by_size.get(size)
    if not candidates:
        return None
    digest = _sha256(pdf_path)
    if digest is None:
        return None
    for row_digest, citekey in candidates:
        if row_digest == digest:
            return citekey
    return None


def build_corpus() -> tuple[list[CorpusDoc], list[str]]:
    """(docs, complaints). Complaints are for the caller to print --
    `scripts/enrich.py` does, before it runs any stage."""
    docs: list[CorpusDoc] = []
    complaints: list[str] = []

    con = ledger.connect()
    try:
        rows = ledger.all_items(con)
    finally:
        con.close()

    for item in rows:
        docs.append(
            CorpusDoc(
                doc_id=item["citekey"],
                citekey=item["citekey"],
                source="bib",
                title=item["title"] or "Untitled",
                pdf_path=item["pdf_path"],
                text_path=item["parsed_path"],
            )
        )

    manifest = _source_pdf_manifest()
    # is_dir(), not exists(): a plain file where the directory is
    # expected is a misconfiguration, and this module's contract is to
    # degrade rather than raise on one.
    if config.SOURCE_PDFS_DIR.is_dir():
        by_path, by_size = _ledger_pdf_index(rows)
        added = 0
        for pdf_path in sorted(config.SOURCE_PDFS_DIR.glob("*.pdf")):
            duplicate_of = _already_citable(pdf_path, by_path, by_size)
            if duplicate_of:
                complaints.append(
                    f"  skipped {pdf_path.name}: same PDF as {duplicate_of}, which is "
                    f"already in the ledger. Indexing it again would put the same paper "
                    f"in the corpus twice, once citable and once not."
                )
                continue
            entry = manifest.get(pdf_path.name, {})
            docs.append(
                CorpusDoc(
                    doc_id=f"doc:{pdf_path.stem}",
                    citekey=None,
                    source="source-pdfs",
                    title=entry.get("title", pdf_path.stem),
                    pdf_path=str(pdf_path),
                )
            )
            added += 1
        if added:
            complaints.append(
                f"  NOTE {added} document(s) from {config.SOURCE_PDFS_DIR} have no bib "
                f"entry, so they have no citekey and can never be cited. They are "
                f"searchable evidence only -- add one to your reference manager, "
                f"re-export, and re-run `python -m src.sync` to make it citable."
            )

    assert_no_citekey_collision(docs)
    return docs, complaints


def assert_no_citekey_collision(docs: list[CorpusDoc]) -> None:
    """Paranoia check: a source-pdfs doc_id must never be citable.

    citation_gate.py only checks ledger membership, so the real safety net
    is that a `doc:`-prefixed id can never appear in the bib file (bib
    citekeys don't contain colons). This assertion makes that invariant a
    hard failure instead of a silent assumption, in case either side changes.
    """
    con = ledger.connect()
    try:
        known_citekeys = ledger.known_citekeys(con)
    finally:
        con.close()

    for doc in docs:
        if doc.source == "source-pdfs":
            assert doc.citekey is None, f"{doc.doc_id}: source-pdfs doc must not have a citekey"
            assert doc.doc_id not in known_citekeys, (
                f"{doc.doc_id}: collides with a real citekey -- "
                "source-pdfs doc_ids must stay outside the citekey namespace"
            )
            assert doc.doc_id.startswith("doc:"), f"{doc.doc_id}: source-pdfs doc_id must carry the doc: prefix"

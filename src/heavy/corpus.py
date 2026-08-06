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
"""

import json
from dataclasses import dataclass

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


def build_corpus() -> list[CorpusDoc]:
    docs: list[CorpusDoc] = []

    con = ledger.connect()
    try:
        for item in ledger.all_items(con):
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
    finally:
        con.close()

    manifest = _source_pdf_manifest()
    if config.SOURCE_PDFS_DIR.exists():
        for pdf_path in sorted(config.SOURCE_PDFS_DIR.glob("*.pdf")):
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

    assert_no_citekey_collision(docs)
    return docs


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

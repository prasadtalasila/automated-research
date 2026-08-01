"""Stage 1: Docling PDF parsing.

Layout-aware parsing (headings, tables, reading order) -- a step up from
the core pipeline's plain pdftotext. Needs `docling` from
pyproject.toml's "heavy" Poetry group, in a venv; heavy (its own
layout/OCR models), so this is the stage most likely to be slow or fail
on a small/CPU-only host. Output is Markdown, written per-doc so a
failure on one document doesn't lose progress on the others.

parse_corpus() is incremental: a per-doc_id (size, mtime_ns) fingerprint
is cached to config.DOCLING_CACHE_PATH, so a PDF that's unchanged since
the last call skips straight past DocumentConverter -- the slowest stage
in this whole pipeline (373s for 5 PDFs, per DEVELOPER.md's own known-gaps
note this closes). Unlike src/ledger.py's stat-before-hash, there's no
sha256 fallback here: a same-size edit that also preserves mtime (e.g.
`cp --preserve=timestamps`) slips past this check and the .md stays
stale until something else invalidates the cache entry (deleting it, or
deleting the .md itself -- see below). That's a real gap, not a free
trade-off the way it is in ledger.py (there, hashing is the fallback
that stat merely defers); accepted here because Docling is opt-in
(`full_pipeline.py --stages docling`, not part of `sync`) and a source
this stale-cache-prone is rare enough not to warrant sha256-hashing every
PDF up front just to guard against it. The cache also re-checks that the
expected output file still exists before trusting a fingerprint match,
so manually deleting a .md file forces a re-parse instead of leaving it
silently missing forever.
"""

import json
import os
from pathlib import Path

from src import config
from src.heavy.corpus import CorpusDoc, safe_filename


def _load_cache() -> dict:
    """Corrupt or unexpected-shape cache data is treated as empty rather
    than raised -- see src/retrieval.py's _load_cache for the same
    defensive shape, applied here so a truncated write (e.g. a killed
    mid-run process) doesn't take down every doc in the next parse_corpus
    call, just cost it one avoidable re-parse per doc."""
    try:
        data = json.loads(config.DOCLING_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        doc_id: fp for doc_id, fp in data.items()
        if isinstance(fp, list) and len(fp) == 2 and all(isinstance(n, int) for n in fp)
    }


def _save_cache(cache: dict) -> None:
    """Atomic write-then-replace so a process killed mid-save leaves the
    previous, still-valid cache in place instead of a torn file --
    doesn't need src/retrieval.py's per-writer-unique temp name (its
    concurrent-subagent scenario doesn't apply: full_pipeline.py runs
    this stage from a single process).

    A failure to persist (permission, disk full) is reported, not
    raised (PR #10 review): by the time this runs, the expensive part
    -- Docling itself -- has already succeeded, so failing the whole
    parse over a cache write is worse than the alternative of just
    re-paying that one doc's parse cost next call."""
    try:
        config.DOCLING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = config.DOCLING_CACHE_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(cache))
        os.replace(tmp_path, config.DOCLING_CACHE_PATH)
    except OSError as exc:
        print(
            f"  WARNING: couldn't persist Docling's incremental cache "
            f"({exc}) -- next run will re-parse what was already done "
            "this run."
        )


def parse_doc(doc: CorpusDoc, cache: dict | None = None) -> Path:
    """cache, when passed explicitly (parse_corpus does this), is
    mutated in place but NOT persisted by this call -- the caller owns
    save timing. Call with cache=None (the default) for a one-off parse
    that should persist its own result immediately."""
    from docling.document_converter import DocumentConverter

    if not doc.pdf_path:
        raise ValueError(f"{doc.doc_id}: no PDF to parse")

    owns_cache = cache is None
    if owns_cache:
        cache = _load_cache()

    config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.DOCLING_DIR / f"{safe_filename(doc.doc_id)}.md"

    st = os.stat(doc.pdf_path)
    fingerprint = [st.st_size, st.st_mtime_ns]
    if cache.get(doc.doc_id) == fingerprint and out_path.exists():
        return out_path

    converter = DocumentConverter()
    result = converter.convert(doc.pdf_path)
    out_path.write_text(result.document.export_to_markdown())

    cache[doc.doc_id] = fingerprint
    if owns_cache:
        _save_cache(cache)
    return out_path


def parse_corpus(docs: list[CorpusDoc]) -> dict[str, str]:
    """Returns {doc_id: 'ok' | 'error: ...'} -- never raises for a single doc failure."""
    cache = _load_cache()
    status = {}
    for doc in docs:
        try:
            out_path = parse_doc(doc, cache=cache)
            status[doc.doc_id] = f"ok: {out_path}"
        except Exception as exc:  # noqa: BLE001 -- report per-doc, don't abort the batch
            status[doc.doc_id] = f"error: {exc}"
    _save_cache(cache)
    return status

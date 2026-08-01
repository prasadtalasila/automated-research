"""Stage 1: Docling PDF parsing.

Layout-aware parsing (headings, tables, reading order) -- a step up from
the core pipeline's plain pdftotext. Needs `docling` from
pyproject.toml's "heavy" Poetry group, in a venv; heavy (its own
layout/OCR models), so this is the stage most likely to be slow or fail
on a small/CPU-only host. Output is Markdown, written per-doc so a
failure on one document doesn't lose progress on the others.

With config.DOCLING_IMAGES on, each doc also gets its figure bitmaps
(in `<stem>_artifacts/`, written by Docling itself) and a
`<stem>.figures.json` index giving each figure's page, caption, and the
string to cite it by. Those images are a reading aid for checking a
draft against its sources -- never draft content, since citing a paper
grants no right to reproduce its figures. See DEVELOPER.md's "Figures
and copyright".

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

That fingerprint only sees the *input* PDF, though, so it cannot notice
a change to what this module writes. `_CACHE_VERSION` and the recorded
`config.DOCLING_IMAGES` setting cover that second axis: either one
differing from the cache file invalidates the whole cache rather than
any single entry, since both change what every .md should contain.
"""

import json
import os
import re
from pathlib import Path

from src import config
from src.heavy.corpus import CorpusDoc, safe_filename

# Bump when a change to what parse_doc() *writes* makes an existing .md
# stale even though its PDF hasn't changed -- the (size, mtime_ns)
# fingerprint below only sees the input, never the output shape, so
# without this an option change silently serves last run's files
# forever. Mirrors src/retrieval.py's _INDEX_SCHEMA_VERSION.
# config.DOCLING_IMAGES is stored alongside it for the same reason:
# it's a *runtime* toggle, so it can't be folded into this constant.
_CACHE_VERSION = 1


def _load_cache() -> dict:
    """Corrupt or unexpected-shape cache data is treated as empty rather
    than raised -- see src/retrieval.py's _load_cache for the same
    defensive shape, applied here so a truncated write (e.g. a killed
    mid-run process) doesn't take down every doc in the next parse_corpus
    call, just cost it one avoidable re-parse per doc.

    A version or image-setting mismatch invalidates the whole cache
    rather than any one entry: both change what every .md in
    config.DOCLING_DIR should contain, not just one document's."""
    try:
        data = json.loads(config.DOCLING_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != _CACHE_VERSION or data.get("images") != config.DOCLING_IMAGES:
        return {}
    items = data.get("items")
    if not isinstance(items, dict):
        return {}
    return {
        doc_id: fp for doc_id, fp in items.items()
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
        payload = {"version": _CACHE_VERSION, "images": config.DOCLING_IMAGES, "items": cache}
        tmp_path.write_text(json.dumps(payload))
        os.replace(tmp_path, config.DOCLING_CACHE_PATH)
    except OSError as exc:
        print(
            f"  WARNING: couldn't persist Docling's incremental cache "
            f"({exc}) -- next run will re-parse what was already done "
            "this run."
        )


# Leading "Figure 3." / "Fig. 3" / "Table 2:" in a caption -- the
# paper's *own* numbering, which is the only trustworthy source for it.
# Docling's picture order can't stand in: publisher logos and licence
# badges are pictures too (3 of the first 3 on a real MDPI paper), so
# the Nth picture is routinely not the paper's Figure N.
_CAPTION_LABEL_RE = re.compile(r"^\s*(Figure|Fig\.?|Table|Scheme)\s*(\d+)", re.IGNORECASE)


def _build_converter():
    """A bare DocumentConverter() unless images are on -- picture bitmaps
    are off by default in Docling's own pipeline, and turning them on is
    what costs the extra decode time and the artifacts directory."""
    from docling.document_converter import DocumentConverter

    if not config.DOCLING_IMAGES:
        return DocumentConverter()

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import PdfFormatOption

    opts = PdfPipelineOptions()
    opts.generate_picture_images = True
    opts.images_scale = config.DOCLING_IMAGE_SCALE
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _figure_records(doc: CorpusDoc, dl_doc) -> list[dict]:
    """One record per extracted picture: where it sits in the source, and
    the exact string to cite it by.

    Deliberately produces a *textual* citation, never an instruction to
    reproduce the image -- see DEVELOPER.md's "Figures and copyright".
    A figure whose caption carries no number is cited by page, rather
    than by a number this module would otherwise have to invent.
    """
    records = []
    for pic in dl_doc.pictures:
        caption = (pic.caption_text(dl_doc) or "").strip()
        page = pic.prov[0].page_no if pic.prov else None
        label_match = _CAPTION_LABEL_RE.match(caption)
        ref = f"[@{doc.citekey}]" if doc.citekey else f"({doc.doc_id} -- not citable)"
        if label_match:
            label = f"{label_match.group(1).rstrip('.')} {label_match.group(2)}"
            cite = f"{label} of {ref}" + (f", p.{page}" if page else "")
        else:
            cite = (f"the figure on p.{page} of {ref}" if page
                    else f"an unplaced figure in {ref}")
        records.append({
            "page": page,
            "caption": caption or None,
            "cite": cite,
            "image": pic.image.uri.path if pic.image and pic.image.uri else None,
        })
    return records


def parse_doc(doc: CorpusDoc, cache: dict | None = None) -> Path:
    """cache, when passed explicitly (parse_corpus does this), is
    mutated in place but NOT persisted by this call -- the caller owns
    save timing. Call with cache=None (the default) for a one-off parse
    that should persist its own result immediately."""
    if not doc.pdf_path:
        raise ValueError(f"{doc.doc_id}: no PDF to parse")

    owns_cache = cache is None
    if owns_cache:
        cache = _load_cache()

    config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(doc.doc_id)
    out_path = config.DOCLING_DIR / f"{stem}.md"

    st = os.stat(doc.pdf_path)
    fingerprint = [st.st_size, st.st_mtime_ns]
    if cache.get(doc.doc_id) == fingerprint and out_path.exists():
        return out_path

    converter = _build_converter()
    dl_doc = converter.convert(doc.pdf_path).document
    if config.DOCLING_IMAGES:
        from docling_core.types.doc import ImageRefMode

        # save_as_markdown (not export_to_markdown) so Docling writes the
        # PNGs itself, into <stem>_artifacts/ beside the .md, and rewrites
        # each reference to point at them. Paths land relative to the .md,
        # which is what keeps content/docling/ movable between hosts.
        dl_doc.save_as_markdown(out_path, image_mode=ImageRefMode.REFERENCED)
        figures_path = config.DOCLING_DIR / f"{stem}.figures.json"
        figures_path.write_text(json.dumps(_figure_records(doc, dl_doc), indent=2))
    else:
        out_path.write_text(dl_doc.export_to_markdown())

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

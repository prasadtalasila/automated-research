"""Stage 1: Docling PDF parsing.

Layout-aware parsing (headings, tables, reading order) -- a step up from
the core pipeline's plain pdftotext. Needs `docling` from
pyproject.toml's "heavy" Poetry group, in a venv; heavy (its own
layout/OCR models), so this is the stage most likely to be slow or fail
on a small/CPU-only host. Output is Markdown, written per-doc so a
failure on one document doesn't lose progress on the others.

Not the same thing as `[parser].backend = "docling"`, and not made
redundant by it. That setting points src/pdf_text.py at the same library
to produce one flat .txt per citekey for BM25; this stage produces
structured Markdown plus the `<doc>.passages.json` sidecar for the whole
corpus, always, whatever that setting says. It also reads the **PDF**
rather than content/parsed/, so it is a second independent extraction and
not a refinement of the first -- including for `papers/pdfs/` documents,
which have no ledger row and so are never parsed by job 1 at all. The two
share no cache; DEVELOPER.md's "Job 1 throws away Docling's document
model" is the standing note on what that costs.

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
`config.DOCLING_IMAGES` and `config.PARSER_OCR` settings cover that
second axis: any one of them differing from the cache file invalidates
the whole cache rather than any single entry, since all three change
what every .md should contain.
"""

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from src import config, pdf_text
from src.heavy.corpus import CorpusDoc, safe_filename

# Bump when a change to what parse_doc() *writes* makes an existing .md
# stale even though its PDF hasn't changed -- the (size, mtime_ns)
# fingerprint below only sees the input, never the output shape, so
# without this an option change silently serves last run's files
# forever. Mirrors src/retrieval.py's _INDEX_SCHEMA_VERSION.
# config.DOCLING_IMAGES is stored alongside it for the same reason:
# it's a *runtime* toggle, so it can't be folded into this constant.
# 2: added <stem>.passages.json, so a cache written by version 1 has
# no sidecar for citation_provenance to read even though its .md is
# current.
_CACHE_VERSION = 2


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
    if (data.get("version") != _CACHE_VERSION
            or data.get("images") != config.DOCLING_IMAGES
            or data.get("ocr") != config.PARSER_OCR):
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
        payload = {
            "version": _CACHE_VERSION,
            "images": config.DOCLING_IMAGES,
            "ocr": config.PARSER_OCR,
            "items": cache,
        }
        tmp_path.write_text(json.dumps(payload))
        os.replace(tmp_path, config.DOCLING_CACHE_PATH)
    except OSError as exc:
        print(
            f"  WARNING: couldn't persist Docling's incremental cache "
            f"({exc}) -- next run will re-parse what was already done "
            "this run."
        )


# Leading "Figure 3." / "Fig. 1.1" / "Table 2:" in a caption -- the
# paper's *own* numbering, which is the only trustworthy source for it.
# Docling's picture order can't stand in: publisher logos and licence
# badges are pictures too (3 of the first 3 on a real MDPI paper), so
# the Nth picture is routinely not the paper's Figure N.
#
# The number has to be captured whole. Chapter-scoped numbering ("Fig.
# 1.1" ... "Fig. 1.4", the convention in every edited book chapter in
# this corpus) and sub-figures ("Figure 2a") are both common, and
# matching only the leading integer collapses all four of that chapter's
# distinct figures onto a single "Fig 1" -- a citation that points at
# the wrong picture, which is worse than declining to number it.
_CAPTION_LABEL_RE = re.compile(
    r"^\s*(Figure|Fig\.?|Table|Scheme)\s*(\d+(?:\.\d+)*[a-z]?)\b", re.IGNORECASE
)


def _build_converter(threads: int | None = None):
    """Always configured, never bare: `do_ocr` has to be set explicitly
    because Docling's own default is True and this project's is False
    (see config.toml's [parser].ocr for the measurement behind that).
    Picture bitmaps stay off unless config.DOCLING_IMAGES asks for them --
    they're what costs the extra decode time and the artifacts directory.

    Callers should build one of these per *corpus*, not per document:
    DocumentConverter keeps its `initialized_pipelines` cache on the
    instance, so a converter per document re-initialises the layout,
    table and OCR models every time -- 16.5s of measured cold start, on a
    corpus of 501 PDFs.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = config.PARSER_OCR
    opts.document_timeout = config.PARSER_DOCUMENT_TIMEOUT
    # Under parse_corpus's worker pool each process has claimed its own
    # GPU (pdf_text.init_worker) and been given a share of the host's
    # CPUs. Left alone in the single-worker case, so a default run gets
    # Docling's own accelerator settings unchanged.
    device = pdf_text.worker_device()
    if threads is not None or device is not None:
        from docling.datamodel.accelerator_options import AcceleratorOptions

        kwargs = {}
        if threads is not None:
            kwargs["num_threads"] = threads
        if device is not None:
            kwargs["device"] = device
        opts.accelerator_options = AcceleratorOptions(**kwargs)
    if config.DOCLING_IMAGES:
        opts.generate_picture_images = True
        opts.images_scale = config.DOCLING_IMAGE_SCALE
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


_IMAGE_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")


def _relativise_image_refs(md_path: Path) -> list[str]:
    """Rewrite the .md's image references to be relative to the .md, and
    return them in document order.

    Docling's `save_as_markdown` writes *absolute* paths, which bakes this
    host's directory layout into every file -- moving `content/docling/`,
    or generating it in a container and reading it elsewhere, breaks all
    of them. Relative refs keep the .md and its `_artifacts/` directory
    movable as a unit.

    The returned names are also what `_figure_records` records for each
    picture: the document's own `pic.image.uri` is a `data:` URI carrying
    the whole PNG base64-encoded, and `save_as_markdown` does not rewrite
    it, so the markdown is the only place the written filename appears.
    """
    text = md_path.read_text()
    base = md_path.parent
    names: list[str] = []

    def rewrite(match):
        target = match.group(2)
        path = Path(target)
        if path.is_absolute():
            try:
                # as_posix(), not str(): a Markdown image reference is a
                # URL-ish path and must use forward slashes. On Windows
                # str() yields "dir\image.png", which is not a valid
                # reference anywhere -- including on the Windows box that
                # produced it -- and would make content/docling/ readable
                # only on the platform it was generated on.
                target = path.relative_to(base).as_posix()
            except ValueError:
                # Somewhere outside the .md's own tree -- leave it alone
                # rather than emit a fragile chain of `../`.
                pass
        names.append(target)
        return match.group(1) + target + match.group(3)

    md_path.write_text(_IMAGE_REF_RE.sub(rewrite, text))
    return names


# Docling labels each text item. Running heads, page numbers and figure
# captions are not prose a claim can be supported by, so they are left
# out of the passage sidecar -- keeping them would let a claim "match"
# a journal name repeated on all 17 pages.
_PASSAGE_LABELS = frozenset({"text", "list_item", "section_header", "title"})


def _passage_records(dl_doc) -> list[dict]:
    """One record per prose text item: what it says and where it sits.

    This is what makes a *quotable* passage possible. `pdftotext -layout`
    preserves a page's visual arrangement rather than its reading order,
    so on a two-column paper each output line splices together two
    unrelated columns (82%-89% of long lines, measured over this
    project's own sample). Any excerpt drawn from that text is a
    two-argument collage. Docling resolves reading order, so an item here
    is a real paragraph that can be shown to a reviewer verbatim.

    The bounding box rides along because Docling already has it, and it
    is what a future click-through highlight would need; nothing in this
    repo consumes it yet.
    """
    records = []
    for item in getattr(dl_doc, "texts", []):
        label = str(getattr(item, "label", "")).split(".")[-1].lower()
        text = (getattr(item, "text", "") or "").strip()
        if label not in _PASSAGE_LABELS or not text:
            continue
        prov = item.prov[0] if getattr(item, "prov", None) else None
        record = {"text": text, "label": label,
                  "page": getattr(prov, "page_no", None) if prov else None}
        bbox = getattr(prov, "bbox", None) if prov else None
        if bbox is not None:
            record["bbox"] = [getattr(bbox, side, None) for side in ("l", "t", "r", "b")]
        records.append(record)
    return records


def _figure_records(doc: CorpusDoc, dl_doc, image_names: list[str] | None = None) -> list[dict]:
    """One record per extracted picture: where it sits in the source, and
    the exact string to cite it by.

    Deliberately produces a *textual* citation, never an instruction to
    reproduce the image -- see DEVELOPER.md's "Figures and copyright".
    A figure whose caption carries no number is cited by page, rather
    than by a number this module would otherwise have to invent.

    `image_names` pairs positionally with `dl_doc.pictures` (both are in
    document order). A count mismatch means that assumption broke, so
    every record drops the filename rather than risk pointing a figure at
    someone else's image.
    """
    if image_names is not None and len(image_names) != len(dl_doc.pictures):
        image_names = None
    records = []
    for index, pic in enumerate(dl_doc.pictures):
        caption = (pic.caption_text(dl_doc) or "").strip()
        page = pic.prov[0].page_no if pic.prov else None
        label_match = _CAPTION_LABEL_RE.match(caption)
        ref = f"[@{doc.citekey}]" if doc.citekey else f"({doc.doc_id} -- not citable)"
        if label_match:
            kind = label_match.group(1).rstrip(".")
            # "Fig"/"Fig." -> "Figure", so the citation reads the way a
            # reader would write it, rather than echoing the source's
            # abbreviation into the middle of a sentence.
            kind = "Figure" if kind.lower().startswith("fig") else kind.capitalize()
            cite = f"{kind} {label_match.group(2)} of {ref}" + (f", p.{page}" if page else "")
        else:
            cite = (f"the figure on p.{page} of {ref}" if page
                    else f"an unplaced figure in {ref}")
        records.append({
            "page": page,
            "caption": caption or None,
            "cite": cite,
            "image": image_names[index] if image_names else None,
        })
    return records


def _outputs_present(stem: str) -> bool:
    """Every file this stage writes for `stem`, not just the .md.

    The fingerprint only says the *input* PDF is unchanged. Checking one
    output was enough when the .md was the only one; now a deleted or
    corrupted `<stem>.passages.json` (or `<stem>.figures.json`, with
    images on) would be skipped over on every subsequent run and stay
    missing forever, because the .md it is paired with is still there.
    """
    expected = [
        config.DOCLING_DIR / f"{stem}.md",
        config.DOCLING_DIR / f"{stem}.passages.json",
    ]
    if config.DOCLING_IMAGES:
        expected.append(config.DOCLING_DIR / f"{stem}.figures.json")
    return all(path.exists() for path in expected)


def _fingerprint(doc: CorpusDoc) -> list:
    """(size, mtime_ns) of a doc's PDF -- the cache key parse_doc uses."""
    st = os.stat(doc.pdf_path)
    return [st.st_size, st.st_mtime_ns]


def _is_cached(doc: CorpusDoc, cache: dict) -> bool:
    """Whether parse_doc would skip this document.

    Duplicated from parse_doc's own check rather than refactored out of
    it, because parse_corpus needs the answer *before* dispatching work
    to a pool -- a cached document must not be sent to a worker, or the
    run pays a process and a model load to discover there was nothing to
    do. A stat is nanoseconds next to that.
    """
    try:
        return cache.get(doc.doc_id) == _fingerprint(doc) and _outputs_present(
            safe_filename(doc.doc_id))
    except OSError:
        return False


def _pdf_size(path: str | None) -> int:
    """Bytes, or 0 if it can't be stat'd -- only used to order work."""
    try:
        return os.path.getsize(path)
    except (OSError, TypeError):
        return 0


def _executor_for(workers: int):
    """Mirrors src/sync.py's: one GPU per worker, and whichever start
    method pdf_text.process_pool_context picks.

    Kept as its own function here rather than imported from sync so that
    src/heavy/ doesn't depend on the core entrypoint -- the dependency
    runs the other way everywhere else in this repo.

    That duplication is the reason this takes `usable_devices()` rather
    than a device count: the two builders have to agree about what
    init_worker is handed, and a count here would skip the free-card
    check that sync does -- which is the whole of what it is for.
    """
    ctx, complaint = pdf_text.process_pool_context()
    if complaint:
        print(complaint)
    devices, gpu_complaint = pdf_text.usable_devices()
    if gpu_complaint:
        print(gpu_complaint)
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=pdf_text.init_worker,
        initargs=(ctx.Value("i", 0), ctx.Lock(), devices),
    )


def parse_doc(doc: CorpusDoc, cache: dict | None = None, converter=None) -> Path:
    """cache, when passed explicitly (parse_corpus does this), is
    mutated in place but NOT persisted by this call -- the caller owns
    save timing. Call with cache=None (the default) for a one-off parse
    that should persist its own result immediately.

    converter follows the same injected-or-owned shape, for the same
    reason cache does: parse_corpus builds one and hands it to every
    document, because building one per document reloads every model per
    document. A standalone call builds its own -- but note that a loop
    of standalone parse_doc() calls pays that cost per document, which is
    what parse_corpus exists to avoid."""
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
    if cache.get(doc.doc_id) == fingerprint and _outputs_present(stem):
        return out_path

    # Built here rather than above the cache check, so a fully-cached run
    # never loads Docling's models at all.
    if converter is None:
        converter = _build_converter()
    result = converter.convert(doc.pdf_path)
    # Same hole src/pdf_text.py closed in v1.2.0, on the other call site:
    # convert(raises_on_error=True) raises only on FAILURE, so a
    # PARTIAL_SUCCESS would otherwise be written to
    # content/docling/<doc>.md as though complete -- and that .md feeds
    # embeddings, topic modelling and citation provenance, where a
    # truncated source is one a claim can be checked against and silently
    # pass. Raised before anything is written, so the document stays
    # uncached and is retried next run.
    pdf_text.check_docling_status(result)
    dl_doc = result.document
    if config.DOCLING_IMAGES:
        from docling_core.types.doc import ImageRefMode

        # save_as_markdown (not export_to_markdown) so Docling writes the
        # PNGs itself, into <stem>_artifacts/ beside the .md, and points
        # each reference at them. It writes those references as absolute
        # paths, so _relativise_image_refs rewrites them afterwards --
        # see its docstring.
        dl_doc.save_as_markdown(out_path, image_mode=ImageRefMode.REFERENCED)
        image_names = _relativise_image_refs(out_path)
        figures_path = config.DOCLING_DIR / f"{stem}.figures.json"
        figures_path.write_text(json.dumps(_figure_records(doc, dl_doc, image_names), indent=2))
    else:
        out_path.write_text(dl_doc.export_to_markdown())

    # Written for every doc, images on or off: src/citation_provenance.py
    # reads it to quote a real passage rather than a window sliced out of
    # column-spliced flat text. Cheap next to the parse that produced it.
    passages_path = config.DOCLING_DIR / f"{stem}.passages.json"
    passages_path.write_text(json.dumps(_passage_records(dl_doc), indent=2))

    cache[doc.doc_id] = fingerprint
    if owns_cache:
        _save_cache(cache)
    return out_path


class _LazyConverter:
    """One converter for the whole corpus, built on first actual use.

    Two things at once, both of which matter on a 501-PDF corpus:
    building it once means Docling's layout/table/OCR models load once
    rather than per document (16.5s of measured cold start each time),
    and deferring the build means a fully-cached run -- the common case
    for a re-run of `full_pipeline.py --stages docling` -- never loads
    them at all.
    """

    def __init__(self):
        self._converter = None

    def convert(self, pdf_path):
        if self._converter is None:
            self._converter = _build_converter()
        return self._converter.convert(pdf_path)


# One converter per worker *process*, not per document. A pool worker
# handles many documents over its life, and DocumentConverter keeps its
# initialized_pipelines cache on the instance -- so building one per
# document would reload Docling's layout, table and OCR models for every
# file, which is exactly the cost the serial path stopped paying in
# v0.12.0. Keyed on everything that changes what a converter *is*, so a
# changed setting can't be served a stale one.
_WORKER_CONVERTER = None
_WORKER_CONVERTER_KEY = None


def _worker_converter(threads: int | None):
    global _WORKER_CONVERTER, _WORKER_CONVERTER_KEY

    key = (threads, pdf_text.worker_device(), config.PARSER_OCR,
           config.DOCLING_IMAGES, config.DOCLING_IMAGE_SCALE,
           config.PARSER_DOCUMENT_TIMEOUT)
    if _WORKER_CONVERTER is None or _WORKER_CONVERTER_KEY != key:
        _WORKER_CONVERTER = _build_converter(threads)
        _WORKER_CONVERTER_KEY = key
    return _WORKER_CONVERTER


def _reset_worker_converter() -> None:
    """Test hook -- module state otherwise leaks between tests."""
    global _WORKER_CONVERTER, _WORKER_CONVERTER_KEY
    _WORKER_CONVERTER = None
    _WORKER_CONVERTER_KEY = None


def parse_one(job: tuple) -> tuple:
    """One worker's unit of work: (doc, threads) in, (doc_id, status,
    fingerprint) out.

    Module-level and exception-free by design -- both the argument and
    the result have to survive pickling to and from a worker process, and
    an arbitrary Docling exception may not. The fingerprint travels back
    so the *parent* owns every cache write, the same way src/sync.py
    keeps every ledger write on the main process.
    """
    doc, threads = job
    try:
        out_path = parse_doc(doc, cache={}, converter=_worker_converter(threads))
        return doc.doc_id, f"ok: {out_path}", _fingerprint(doc)
    except Exception as exc:  # noqa: BLE001 -- report per-doc, don't abort the batch
        return doc.doc_id, f"error: {exc}", None


def parse_corpus(docs: list[CorpusDoc]) -> dict[str, str]:
    """Returns {doc_id: 'ok' | 'error: ...'} -- never raises for a single doc failure.

    Parallelised by [parser].workers exactly like src/sync.py, and for
    the same reason: this is the slowest stage in the repository, and a
    first run over a real corpus is measured in tens of minutes. The
    default of 1 keeps the historical serial path, converter reuse and
    all.

    One constraint that comes with the worker pool: every start method it
    can pick (see _executor_for) re-imports the calling program's
    __main__ in each worker -- forkserver preloads torch and docling in
    its server process, but the worker still runs spawn's preparation
    step. A script that calls this must therefore guard its top level
    with `if __name__ == "__main__":`, or every worker re-runs it on
    startup and the pool dies with BrokenProcessPool.
    scripts/full_pipeline.py and src/sync.py both do; an ad-hoc script
    that doesn't will fail immediately rather than subtly.
    """
    cache = _load_cache()
    status = {}

    pending = [d for d in docs if d.pdf_path and not _is_cached(d, cache)]
    workers, complaint = pdf_text.resolve_workers(len(pending))
    if complaint:
        print(complaint)

    if workers > 1:
        threads = pdf_text.docling_threads(workers)
        # Biggest-file-first, same LPT reasoning as src/sync.py: one
        # 675-page document in this corpus would otherwise define the
        # wall clock all by itself if it were picked up last.
        jobs = [(d, threads) for d in sorted(pending, key=lambda d: -_pdf_size(d.pdf_path))]
        cached = [d for d in docs if d not in pending]
        for doc in cached:
            try:
                status[doc.doc_id] = f"ok: {parse_doc(doc, cache=cache)}"
            except Exception as exc:  # noqa: BLE001 -- as below
                status[doc.doc_id] = f"error: {exc}"
        # Explicit shutdown rather than `with`, for the reason src/sync.py
        # gives: the context manager waits for every queued job, so
        # Ctrl+C would drain the whole corpus before exiting.
        executor = _executor_for(workers)
        done = 0
        try:
            for doc_id, doc_status, fingerprint in executor.map(parse_one, jobs):
                status[doc_id] = doc_status
                if fingerprint is not None:
                    cache[doc_id] = fingerprint
                done += 1
                print(f"  [{done}/{len(jobs)}] {doc_id}")
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            pdf_text.terminate_workers(executor)
            print(f"\n  interrupted after {done}/{len(jobs)} document(s) -- "
                  "parsed output is kept; re-run to continue.")
            _save_cache(cache)
            raise
        finally:
            executor.shutdown(wait=False)
    else:
        converter = _LazyConverter()
        for doc in docs:
            try:
                out_path = parse_doc(doc, cache=cache, converter=converter)
                status[doc.doc_id] = f"ok: {out_path}"
            except Exception as exc:  # noqa: BLE001 -- report per-doc, don't abort the batch
                status[doc.doc_id] = f"error: {exc}"
    _save_cache(cache)
    return status

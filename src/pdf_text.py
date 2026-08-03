"""PDF text extraction: dispatches to whichever backend config.PARSER
names (config.toml's [parser].backend, or the PARSER env var) --
"pdftotext" (default) or "docling". Both write into
the same place, content/parsed/<citekey>.txt, so every downstream
consumer (src/ledger.py, src/retrieval.py, scripts/verbatim_check.py)
stays backend-agnostic; only this module needs to know which one is
configured.

pdftotext has no Python dependency (a subprocess call to poppler-utils)
and is the only backend whose output has page boundaries (form-feed
characters between pages) -- docling produces one continuous document.
See config.toml's [parser] comment for the full tradeoffs (speed,
page-boundary loss) before switching off the default.

The dispatch is deliberately a table rather than an if/else: adding a
backend is a `_extract_*` function plus one `_EXTRACTORS` entry, and
markitdown was removed through the same seam (see docs/PDF-PARSER.md for why).
"""

import importlib.util
import os
import re
import signal
import sys
import shutil
import subprocess
from pathlib import Path

from src import config

# Logical CPUs one docling worker actually occupies. Docling's
# AcceleratorOptions.num_threads defaults to 4, and a single docling
# process was measured holding ~300% CPU on the documented A40 host --
# so "one worker per CPU" would oversubscribe by about 4x. Used both as
# the divisor for `workers = "auto"` and as the ceiling an explicit
# request is clamped to.
_CPUS_PER_DOCLING_WORKER = 4

# How long a worker gets to honour SIGTERM before being killed outright.
_TERMINATE_GRACE_SECONDS = 2.0


def allowed_cpus() -> int:
    """How many CPUs this *process* may run on -- not how many the
    machine has.

    `os.cpu_count()` reports the machine. `os.sched_getaffinity(0)`
    reports the affinity mask actually in force, which a container,
    `taskset`, or a batch scheduler will have narrowed. On the host this
    was developed on the two disagree badly: 96 CPUs exist, 48 are
    permitted, so sizing a pool off `cpu_count()` would spawn twice as
    many workers as there are CPUs to run them.

    `sched_getaffinity` is Linux-only -- it does not exist on Windows or
    macOS, and this project's CI has a windows-latest leg -- hence the
    getattr rather than a bare call.

    Not covered: a cgroup CPU *quota* (`docker --cpus=2`) throttles
    without narrowing the affinity mask, so this still reports the full
    set there. config.toml.example says so, and an explicit
    [parser].workers is the answer on such a host.
    """
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is not None:
        return len(getaffinity(0))
    return os.cpu_count() or 1


def resolve_workers(n_docs: int) -> tuple[int, str | None]:
    """(workers, complaint) for a run that has `n_docs` to parse.

    The resolved count is the smallest of three independent ceilings,
    floored at 1: what was asked for, what the host can sustain, and how
    many documents there actually are. The third matters more than it
    looks -- standing up 12 docling workers to parse 3 documents pays 12
    model loads to save two documents' worth of work.

    An explicit request above the host ceiling is clamped *and reported*.
    Obeying it thrashes; ignoring it silently leaves someone believing
    they configured something they didn't.
    """
    cpus = allowed_cpus()
    if config.PARSER == "docling":
        ceiling = max(1, cpus // _CPUS_PER_DOCLING_WORKER)
    else:
        # Each pdftotext is a short, single-threaded subprocess, so
        # charging it a docling worker's 4 CPUs would under-use the host.
        ceiling = cpus

    requested = config.PARSER_WORKERS
    wanted = ceiling if requested == "auto" else requested
    workers = max(1, min(wanted, ceiling, n_docs or 1))

    complaint = None
    if requested != "auto" and requested > ceiling:
        complaint = (
            f"  WARNING [parser].workers={requested} exceeds what this host can "
            f"sustain ({cpus} CPUs available to this process"
            + (f", ~{_CPUS_PER_DOCLING_WORKER} per docling worker"
               if config.PARSER == "docling" else "")
            + f") -- using {workers}."
        )
    return workers, complaint


def terminate_workers(executor) -> None:
    """Kill the pool's worker processes outright.

    shutdown(cancel_futures=True) only drops jobs that have not started;
    the handful already running keep going, and ProcessPoolExecutor's
    atexit hook then *joins* them on the way out. With docling that is
    minutes per in-flight document, which is what "Ctrl+C took forever to
    exit" actually was.

    There is no public API for this -- ProcessPoolExecutor deliberately
    exposes no way to cancel running work -- so this reaches for
    `_processes`. Guarded with getattr because it is private and because
    a ThreadPoolExecutor (the pdftotext backend, and the tests) has no
    such attribute and needs no killing.
    """
    processes = list(getattr(executor, "_processes", {}).values())
    for process in processes:
        try:
            process.terminate()
        except Exception:  # noqa: BLE001 -- already exited, or already reaped
            pass
    # SIGTERM is a request, and a worker sitting in onnxruntime or torch
    # native code does not necessarily honour it promptly -- measured 21
    # processes still alive after terminate() alone, which would leave
    # orphans holding GPU memory. Give them a moment, then insist.
    for process in processes:
        try:
            process.join(timeout=_TERMINATE_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
        except Exception:  # noqa: BLE001 -- as above
            pass


class interrupt_guard:
    """Exit promptly and cleanly on Ctrl+C while a worker pool is running.

    An `except KeyboardInterrupt` around `as_completed()` does NOT work
    here, and that is the whole reason this exists. Reproduced in a
    minimal script with no docling in it: SIGINT reaches the parent, the
    result loop stops consuming, and the handler never runs -- the
    process then sits until its in-flight workers finish, which with
    docling is minutes per document. That is the reported symptom,
    "Ctrl+C took forever to exit", plus docling teardown tracebacks from
    workers still being driven.

    An explicit SIGINT handler does work: measured 0.0s to exit against
    60s+ (and counting) for the exception path. It terminates the pool's
    workers and calls os._exit, deliberately skipping interpreter
    shutdown -- ProcessPoolExecutor's atexit hook *joins* its workers,
    which is the thing being avoided. Skipping it is safe for this
    program's own state because the ledger commits incrementally and
    synchronously as each document lands: whatever finished is already on
    disk.

    Restores the previous handler on the way out, so a library caller
    that installed its own gets it back.
    """

    def __init__(self, executor, describe):
        self._executor = executor
        self._describe = describe
        self._previous = None

    def __enter__(self):
        try:
            self._previous = signal.signal(signal.SIGINT, self._on_sigint)
        except ValueError:
            # Not the main thread (a test, or an embedding caller):
            # signal handling isn't available, and the pool still works.
            self._previous = None
        return self

    def __exit__(self, *exc_info):
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
        return False

    def _on_sigint(self, _signum, _frame):
        print(f"\n  interrupted -- {self._describe()}. Work already "
              "finished is kept; re-run to continue.", file=sys.stderr, flush=True)
        terminate_workers(self._executor)
        os._exit(130)


def gpu_count() -> int:
    """CUDA devices Docling could use, or 0.

    Deliberately forgiving: no torch, a torch without CUDA, or a driver
    mismatch all mean "no GPUs to spread across", which is a perfectly
    good answer -- not a reason to take down a sync that would otherwise
    have run on the CPU.
    """
    if config.PARSER != "docling":
        return 0
    try:
        import torch

        return torch.cuda.device_count()
    except Exception:  # noqa: BLE001 -- see docstring: any failure means 0
        return 0


# The CUDA device this worker process was assigned, or None to leave
# Docling's own AUTO resolution alone. Process-global because it is set
# once per worker, at pool startup, and read whenever that worker builds
# a converter.
_WORKER_DEVICE = None


def worker_device() -> str | None:
    """The CUDA device this worker claimed, or None if it claimed none.

    The public read of the process-global set by init_worker, so other
    modules (src/heavy/docling_parse.py) don't reach into this one's
    internals to build their own converters.
    """
    return _WORKER_DEVICE


def _reset_worker_device() -> None:
    """Test hook -- module state otherwise leaks between tests."""
    global _WORKER_DEVICE
    _WORKER_DEVICE = None
    _reset_docling_converter()


def init_worker(counter, lock, n_gpus: int) -> None:
    """Pool initialiser: claim one GPU for this worker, round-robin.

    Docling's `AcceleratorDevice.AUTO` resolves to `cuda:0` in *every*
    process, so without this, N workers all pile onto one card. Measured
    on this project's corpus before it existed: at 12 workers GPU 0 ran
    pinned at 100% while GPUs 1-3 sat at 0%, and 12 workers were no
    faster than 4.

    The index comes from a shared counter rather than the worker's PID or
    position, because a ProcessPoolExecutor neither numbers its workers
    nor guarantees it starts all of them -- it creates them lazily, as
    work arrives. A counter handed out under a lock is the only thing
    that gives each *live* worker a distinct index.
    """
    global _WORKER_DEVICE
    if n_gpus <= 0:
        _WORKER_DEVICE = None
        return
    with lock:
        index = counter.value
        counter.value += 1
    _WORKER_DEVICE = f"cuda:{index % n_gpus}"


def docling_threads(workers: int) -> int:
    """Docling's per-worker thread count, divided down so that
    workers x threads still fits the host.

    Capped at Docling's own default of 4, so the single-worker default
    resolves to exactly what Docling would have picked on its own and
    this function changes nothing until someone raises [parser].workers.
    """
    return max(1, min(_CPUS_PER_DOCLING_WORKER, allowed_cpus() // max(workers, 1)))


class BackendUnavailable(RuntimeError):
    """config.PARSER's backend isn't usable on this host right now."""


class MissingBinary(BackendUnavailable):
    """pdftotext specifically isn't on PATH -- kept as its own subclass
    (predates the multi-backend dispatch) rather than folded into
    MissingDependency, since src/sync.py's early history and tests
    already reference it by this name."""


class MissingDependency(BackendUnavailable):
    """docling specifically isn't installed (not on PATH --
    a Python package, via pyproject.toml's "heavy" Poetry group)."""


class ExtractionError(RuntimeError):
    """The backend ran but failed on this particular PDF."""


_INSTALL_HINT = {
    "pdftotext": (
        "'pdftotext' not found on PATH. Install poppler-utils "
        "(scripts/install_full_pipeline.sh os-deps) to extract PDF text with it."
    ),
    "docling": (
        "the 'docling' package isn't usable (not installed, or a "
        "transitive dependency is broken). Run 'poetry install --with heavy' "
        "(scripts/install_full_pipeline.sh python-deps) to extract PDF text with it."
    ),
}


def _check_parser(parser: str) -> None:
    # Deliberately left to propagate uncaught out of sync.run() rather
    # than caught-and-printed like MissingBinary/MissingDependency below:
    # this is a misconfiguration (a typo'd PARSER value), not a host
    # missing an optional dependency, and sync.run() already has the same
    # shape for the other fundamental-misconfiguration case -- a missing
    # bib file raises FileNotFoundError uncaught from bib_reader.read_library(),
    # before this function's own try block even starts.
    if parser not in config.PARSER_BACKENDS:
        raise ValueError(
            f"Unknown parser backend {parser!r} (config.toml's [parser].backend, "
            f"or the PARSER env var) -- expected one of {config.PARSER_BACKENDS}."
        )


def unavailable_reason() -> str:
    """Human-readable explanation of why config.PARSER's backend isn't
    usable right now, and how to fix it. Meaningful when is_available()
    is False, and also reused as MissingDependency's message when a
    backend's import fails despite that probe passing (a broken
    transitive dependency -- see _extract_docling)."""
    _check_parser(config.PARSER)
    return _INSTALL_HINT[config.PARSER]


def is_available() -> bool:
    _check_parser(config.PARSER)
    if config.PARSER == "pdftotext":
        return shutil.which("pdftotext") is not None
    return importlib.util.find_spec(config.PARSER) is not None


def _extract_pdftotext(pdf_path: str, out_path: Path, threads: int | None = None) -> None:
    # threads is accepted and ignored: pdftotext is a single-threaded
    # external binary. The parameter exists so _EXTRACTORS stays a plain
    # uniform table rather than growing a per-backend call signature.
    try:
        subprocess.run(
            ["pdftotext", "-layout", pdf_path, str(out_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ExtractionError(exc.stderr or str(exc)) from exc


# One converter, reused for the whole process. Docling's
# DocumentConverter keeps its `initialized_pipelines` cache on the
# *instance*, so building one per PDF re-initialises the layout, table
# and OCR models for every single document -- measured at 16.5s of cold
# start on the documented A40 host, against a corpus of 501 PDFs.
#
# Keyed by the settings that change what a converter *is*, not merely
# memoised on "was one built already": otherwise flipping config.PARSER_OCR
# (which tests do, and a user editing config.toml mid-session would) keeps
# silently serving the converter built under the old setting.
_DOCLING_CONVERTER = None
_DOCLING_CONVERTER_KEY = None


def _reset_docling_converter() -> None:
    """Drop the cached converter. Exists for tests -- module-level state
    otherwise leaks one test's fake converter into the next."""
    global _DOCLING_CONVERTER, _DOCLING_CONVERTER_KEY
    _DOCLING_CONVERTER = None
    _DOCLING_CONVERTER_KEY = None


def _docling_converter(threads: int | None = None):
    global _DOCLING_CONVERTER, _DOCLING_CONVERTER_KEY

    key = (config.PARSER_OCR, threads, _WORKER_DEVICE)
    if _DOCLING_CONVERTER is not None and _DOCLING_CONVERTER_KEY == key:
        return _DOCLING_CONVERTER

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise MissingDependency(unavailable_reason()) from exc

    opts = PdfPipelineOptions()
    opts.do_ocr = config.PARSER_OCR
    if threads is not None or _WORKER_DEVICE is not None:
        # Only touched when a caller has worked out a thread budget or a
        # pool has claimed a GPU for this worker (i.e. when
        # [parser].workers > 1); left alone otherwise, so a default
        # single-worker run gets exactly Docling's own accelerator
        # settings and this module changes nothing about it.
        from docling.datamodel.accelerator_options import AcceleratorOptions

        kwargs = {}
        if threads is not None:
            kwargs["num_threads"] = threads
        if _WORKER_DEVICE is not None:
            kwargs["device"] = _WORKER_DEVICE
        opts.accelerator_options = AcceleratorOptions(**kwargs)
    _DOCLING_CONVERTER = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    _DOCLING_CONVERTER_KEY = key
    return _DOCLING_CONVERTER


def _check_docling_status(result) -> None:
    """Reject a conversion Docling only half-finished.

    `convert(raises_on_error=True)` -- the default -- raises only on
    FAILURE. A PARTIAL_SUCCESS returns quietly with a document that stops
    early: the page loop hit a bad page, or `document_timeout` expired.
    Without this check that truncated text is written to
    content/parsed/<citekey>.txt and the ledger records it as parsed, so
    every downstream consumer -- retrieval, the citation gate, provenance
    -- reasons about a source that silently ends at page k of n. On a
    citation-grounding pipeline that is worse than a visible failure.

    The parse-quality guard cannot stand in for this: it measures
    run-together words, not missing content, and its min_tokens floor
    makes it skip exactly the short documents truncation produces.
    """
    status = getattr(result, "status", None)
    name = getattr(status, "name", str(status))
    if status is None or name == "SUCCESS":
        return
    errors = "; ".join(
        str(getattr(e, "error_message", e)) for e in getattr(result, "errors", [])
    )
    raise ExtractionError(
        f"docling returned {name} rather than SUCCESS -- the extracted text would "
        f"be incomplete{': ' + errors if errors else ''}"
    )


def _extract_docling(pdf_path: str, out_path: Path, threads: int | None = None) -> None:
    converter = _docling_converter(threads)
    try:
        result = converter.convert(pdf_path)
        _check_docling_status(result)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- docling has no narrower
        # common exception type to catch (same reporting shape as
        # src/heavy/docling_parse.py's own parse_corpus loop).
        #
        # The converter is deliberately NOT discarded here: the failure
        # is in this one PDF, not in the models, and throwing it away
        # would charge the next document a full reload for its neighbour's
        # bad luck.
        raise ExtractionError(str(exc)) from exc
    out_path.write_text(result.document.export_to_markdown(), encoding="utf-8")


_EXTRACTORS = {
    "pdftotext": _extract_pdftotext,
    "docling": _extract_docling,
}

# A "word" for the run-together check below. Letters only: digits and
# punctuation produce long runs legitimately (DOIs, URLs, base64-ish
# identifiers, table rules) and would otherwise dominate the count.
#
# `[^\W\d_]` is "word character, but not a digit or underscore" -- i.e.
# any Unicode letter. Spelling it `[A-Za-z]` would silently split
# accented and non-Latin words ("Schroder" + "der" out of "Schröder"),
# which both hides real fusion, since a fused run containing an accent
# gets broken into short pieces, and shrinks the token count toward
# PARSE_MIN_TOKENS on non-English documents until the guard stops
# looking at them at all.
_ALPHA_RUN = re.compile(r"[^\W\d_]+")


def run_together_ratio(text: str) -> tuple[float, int]:
    """Fraction of alphabetic tokens longer than
    config.PARSE_LONG_WORD_CHARS, plus the total token count.

    A PDF text extractor decides where the spaces go by comparing glyph
    positions against a tolerance. Set that tolerance too coarse and
    adjacent words fuse -- "isaninputtooranoutputfromafunction" -- which
    is invisible in a spot check but silently wrecks retrieval, because
    src/retrieval.py tokenizes on whitespace and can no longer match a
    query term buried inside a fused run.

    Measured on this project's own corpus: pdftotext produced 9 such
    tokens out of 113,195 (0.01%) while a since-removed backend produced
    3,647 out of 87,395 (4.17%) over the same 10 PDFs -- three orders of
    magnitude apart, so any threshold between them separates a healthy
    parse from a broken one without needing to be tuned precisely.
    """
    tokens = _ALPHA_RUN.findall(text)
    if not tokens:
        return 0.0, 0
    long_tokens = sum(1 for tok in tokens if len(tok) > config.PARSE_LONG_WORD_CHARS)
    return long_tokens / len(tokens), len(tokens)


def quality_warning(text: str) -> str | None:
    """A one-line complaint about `text`, or None if it looks fine.

    Deliberately a warning rather than an error: the extraction did
    succeed, the text is usable, and a corpus of scanned or unusual
    documents could trip this legitimately. The point is that a
    systematic regression gets *reported* by sync instead of being
    noticed by eye in a retrieval snippet weeks later.
    """
    ratio, total = run_together_ratio(text)
    if total < config.PARSE_MIN_TOKENS or ratio <= config.PARSE_LONG_WORD_RATIO:
        return None
    return (
        f"{ratio:.1%} of words are longer than {config.PARSE_LONG_WORD_CHARS} "
        f"characters ({total} words checked) -- the parser is probably losing "
        f"spaces between words, which degrades retrieval"
    )


def extract_text(pdf_path: str, citekey: str, threads: int | None = None) -> Path:
    """Extract text from a PDF into content/parsed/<citekey>.txt using
    config.PARSER's backend.

    Raises MissingBinary/MissingDependency if that backend isn't usable
    on this host (probe-and-report, like every src/heavy/* stage -- see
    render_output.MissingBinary -- rather than letting the backend's own
    not-found error surface as an uncaught traceback), or ExtractionError
    if the backend runs but fails on this particular PDF.
    """
    if not is_available():
        exc_cls = MissingBinary if config.PARSER == "pdftotext" else MissingDependency
        raise exc_cls(unavailable_reason())

    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.PARSED_DIR / f"{citekey}.txt"
    _EXTRACTORS[config.PARSER](pdf_path, out_path, threads)
    return out_path


def extract_one(job: tuple[str, str, int | None]) -> tuple[str, str | None, Exception | None]:
    """Entry point for one pool worker: (pdf_path, citekey, threads) in,
    (citekey, out_path, exception) out.

    Defined at module level, and returning the exception rather than
    raising it, because both have to survive pickling across a process
    boundary. Returning it keeps the *type* -- src/sync.py distinguishes
    ExtractionError from BackendUnavailable and reports them differently,
    which a stringified error would lose.
    """
    pdf_path, citekey, threads = job
    try:
        return citekey, str(extract_text(pdf_path, citekey, threads)), None
    except (ExtractionError, BackendUnavailable) as exc:
        return citekey, None, exc

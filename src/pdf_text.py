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
markitdown was removed through the same seam (see PDF-PARSER.md for why).
"""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

from src import config


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


def _extract_pdftotext(pdf_path: str, out_path: Path) -> None:
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


def _docling_converter():
    global _DOCLING_CONVERTER, _DOCLING_CONVERTER_KEY

    key = (config.PARSER_OCR,)
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
    _DOCLING_CONVERTER = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    _DOCLING_CONVERTER_KEY = key
    return _DOCLING_CONVERTER


def _extract_docling(pdf_path: str, out_path: Path) -> None:
    converter = _docling_converter()
    try:
        result = converter.convert(pdf_path)
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


def extract_text(pdf_path: str, citekey: str) -> Path:
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
    _EXTRACTORS[config.PARSER](pdf_path, out_path)
    return out_path

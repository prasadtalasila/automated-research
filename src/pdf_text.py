"""PDF text extraction: dispatches to whichever backend config.PARSER
names (config.toml's [parser].backend, or the PARSER env var) --
"pdftotext" (default), "markitdown", or "docling". All three write into
the same place, content/parsed/<citekey>.txt, so every downstream
consumer (src/ledger.py, src/retrieval.py, scripts/verbatim_check.py)
stays backend-agnostic; only this module needs to know which one is
configured.

pdftotext is the only backend with no Python dependency (a subprocess
call to poppler-utils) and the only one whose output has page
boundaries (form-feed characters between pages) -- markitdown and
docling both produce one continuous document. See config.toml's
[parser] comment for the full tradeoffs (speed, page-boundary loss)
before switching off the default.
"""

import importlib.util
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
    """markitdown/docling specifically isn't installed (not on PATH --
    a Python package, via pyproject.toml's "heavy" Poetry group)."""


class ExtractionError(RuntimeError):
    """The backend ran but failed on this particular PDF."""


_INSTALL_HINT = {
    "pdftotext": (
        "'pdftotext' not found on PATH. Install poppler-utils "
        "(scripts/install_full_pipeline.sh os-deps) to extract PDF text with it."
    ),
    "markitdown": (
        "the 'markitdown' package is not installed. Run "
        "'poetry install --with heavy' (scripts/install_full_pipeline.sh "
        "python-deps) to extract PDF text with it."
    ),
    "docling": (
        "the 'docling' package is not installed. Run "
        "'poetry install --with heavy' (scripts/install_full_pipeline.sh "
        "python-deps) to extract PDF text with it."
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
    usable right now, and how to fix it. Only meaningful when
    is_available() is False."""
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


def _extract_markitdown(pdf_path: str, out_path: Path) -> None:
    from markitdown import MarkItDown, MarkItDownException

    try:
        result = MarkItDown().convert(pdf_path)
    except MarkItDownException as exc:
        raise ExtractionError(str(exc)) from exc
    out_path.write_text(result.text_content)


def _extract_docling(pdf_path: str, out_path: Path) -> None:
    from docling.document_converter import DocumentConverter

    try:
        result = DocumentConverter().convert(pdf_path)
    except Exception as exc:  # noqa: BLE001 -- docling has no narrower
        # common exception type to catch (same reporting shape as
        # src/heavy/docling_parse.py's own parse_corpus loop).
        raise ExtractionError(str(exc)) from exc
    out_path.write_text(result.document.export_to_markdown())


_EXTRACTORS = {
    "pdftotext": _extract_pdftotext,
    "markitdown": _extract_markitdown,
    "docling": _extract_docling,
}


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

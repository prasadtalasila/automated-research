"""PDF text extraction using pdftotext (poppler-utils).

This is the text layer available without root/Java on this host. It
does not recover structure (sections, tables, references) the way
Docling or GROBID would -- see docker/ for that heavier path. For
keyword-based retrieval and grounded drafting, layout-preserving plain
text is sufficient.
"""

import shutil
import subprocess
from pathlib import Path

from src import config


class MissingBinary(RuntimeError):
    pass


def is_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_text(pdf_path: str, citekey: str) -> Path:
    """Extract text from a PDF into content/parsed/<citekey>.txt.

    Raises MissingBinary if pdftotext isn't on PATH (probe-and-report,
    like every src/heavy/* stage -- see render_output.MissingBinary --
    rather than letting subprocess.run's bare FileNotFoundError surface
    as an uncaught traceback) or subprocess.CalledProcessError if
    pdftotext runs but fails on this particular PDF.
    """
    if not is_available():
        raise MissingBinary(
            "'pdftotext' is not on PATH. Install poppler-utils "
            "(scripts/install_full_pipeline.sh os-deps) to extract PDF text."
        )
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.PARSED_DIR / f"{citekey}.txt"
    subprocess.run(
        ["pdftotext", "-layout", pdf_path, str(out_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_path

"""PDF text extraction using pdftotext (poppler-utils).

This is the text layer available without root/Java on this host. It
does not recover structure (sections, tables, references) the way
Docling or GROBID would -- see docker/ for that heavier path. For
keyword-based retrieval and grounded drafting, layout-preserving plain
text is sufficient.
"""

import subprocess
from pathlib import Path

from src import config


def extract_text(pdf_path: str, citekey: str) -> Path:
    """Extract text from a PDF into content/parsed/<citekey>.txt.

    Raises subprocess.CalledProcessError if pdftotext fails.
    """
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.PARSED_DIR / f"{citekey}.txt"
    subprocess.run(
        ["pdftotext", "-layout", pdf_path, str(out_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_path

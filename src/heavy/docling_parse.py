"""Stage 1: Docling PDF parsing.

Layout-aware parsing (headings, tables, reading order) -- a step up from
the core pipeline's plain pdftotext. Needs `docling` from
pyproject.toml's "heavy" Poetry group, in a venv; heavy (its own
layout/OCR models), so this is the stage most likely to be slow or fail
on a small/CPU-only host. Output is Markdown, written per-doc so a
failure on one document doesn't lose progress on the others.
"""

from pathlib import Path

from src import config
from src.heavy.corpus import CorpusDoc, safe_filename


def parse_doc(doc: CorpusDoc) -> Path:
    from docling.document_converter import DocumentConverter

    if not doc.pdf_path:
        raise ValueError(f"{doc.doc_id}: no PDF to parse")

    config.DOCLING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.DOCLING_DIR / f"{safe_filename(doc.doc_id)}.md"

    converter = DocumentConverter()
    result = converter.convert(doc.pdf_path)
    out_path.write_text(result.document.export_to_markdown())
    return out_path


def parse_corpus(docs: list[CorpusDoc]) -> dict[str, str]:
    """Returns {doc_id: 'ok' | 'error: ...'} -- never raises for a single doc failure."""
    status = {}
    for doc in docs:
        try:
            out_path = parse_doc(doc)
            status[doc.doc_id] = f"ok: {out_path}"
        except Exception as exc:  # noqa: BLE001 -- report per-doc, don't abort the batch
            status[doc.doc_id] = f"error: {exc}"
    return status

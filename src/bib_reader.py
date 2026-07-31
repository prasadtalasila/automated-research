"""Reads the BibTeX-exported .bib file -- the source of truth for
citekeys and bibliographic metadata (project decision, 2026-07-28).

No auto-sync plugin is installed, so this file is a manual, point-in-time
export from your reference manager, not continuously auto-synced --
re-export it after adding papers, then re-run `python -m src.sync`.
Whatever citekey BibTeX assigns in this file IS the citekey everywhere
downstream (the ledger, citation_gate, generated drafts); this module
never invents its own.

Needs `bibtexparser` (pyproject.toml's main dependency group, installed
via scripts/install_full_pipeline.sh) -- the one dependency the
otherwise stdlib-only core pipeline requires, because hand-rolling a
correct BibTeX parser (nested braces, LaTeX escapes, multi-line values)
is a worse bet than using a maintained library for something
citation-critical.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from src import config

# Reference.pdf_resolution values -- *why* a PDF did or didn't resolve.
# Previously sync.py only ever saw a bare pdf_path of None and reported
# every one of these as one "no PDF attachment" bucket, which masked two
# very different problems: an item with only a non-PDF attachment saved
# (typically an HTML snapshot, but _resolve_pdf_path only actually checks
# for the *absence* of a pdf-mime entry, not the presence of a
# text/html one specifically -- invisible to retrieval/citation-gate the
# same as any other no-PDF item, but not surfaced as such) and an item
# whose PDF the bib file still points at, but which has since moved or
# been deleted (a silent data-loss failure, not a "never had a PDF" one).
PDF_RESOLVED = "resolved"
PDF_NO_FILE_FIELD = "no_file_field"
PDF_MALFORMED_FILE_FIELD = "malformed_file_field"
PDF_PATH_GONE = "pdf_path_gone"
PDF_NON_PDF_ATTACHMENT = "non_pdf_attachment"

# Dict order doubles as the fixed, deterministic order sync.py's
# no-PDF breakdown reports these in.
PDF_RESOLUTION_LABELS = {
    PDF_NO_FILE_FIELD: "no file field in bib entry",
    PDF_PATH_GONE: "PDF path no longer exists on disk",
    PDF_NON_PDF_ATTACHMENT: "non-PDF attachment only (e.g. an HTML snapshot)",
    PDF_MALFORMED_FILE_FIELD: "malformed file field (couldn't parse mime/path)",
}


@dataclass
class Reference:
    citekey: str
    item_type: str
    title: str
    authors: list[tuple[str, str]]  # (first, last)
    year: str
    doi: str | None
    url: str | None
    fields: dict[str, str] = field(default_factory=dict)
    pdf_path: str | None = None
    pdf_resolution: str = PDF_NO_FILE_FIELD


def _parse_authors(author_field: str) -> list[tuple[str, str]]:
    authors = []
    for name in author_field.split(" and "):
        name = name.strip()
        if not name:
            continue
        if "," in name:
            last, first = (p.strip() for p in name.split(",", 1))
        else:
            parts = name.rsplit(" ", 1)
            first, last = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
        authors.append((first, last))
    return authors


def _resolve_pdf_path(file_field: str, bib_dir: Path) -> tuple[str | None, str]:
    """The `file` field format in this project's bib export:
    `Desc:path:mimetype`, `;`-separated for multiple attachments (e.g. an
    HTML snapshot alongside the PDF) -- an export-tool convention, not
    part of the BibTeX standard itself.

    Returns (path, PDF_RESOLVED) on success, or (None, reason) where
    reason distinguishes *why*: PDF_PATH_GONE (a pdf-mime attachment was
    listed but its file no longer exists), PDF_NON_PDF_ATTACHMENT (every
    attachment parsed fine but none is pdf-mime -- typically an HTML
    snapshot saved instead of the PDF, but this only checks for the
    absence of a pdf-mime entry, not the presence of text/html
    specifically, so any other non-PDF mime lands here too), or
    PDF_MALFORMED_FILE_FIELD (not even one `;`-separated segment had the
    `Desc:path:mimetype` shape). If more than one attachment is present,
    a PDF path that's gone still wins over reporting a non-PDF attachment
    -- the presence of a pdf-mime entry is the more actionable signal (a
    paper this project's own bib once had a real PDF for, now missing)
    than "only ever had a non-PDF attachment".
    """
    saw_parseable_attachment = False
    saw_pdf_mime = False
    for attachment in file_field.split(";"):
        parts = attachment.split(":")
        if len(parts) < 3:
            continue
        saw_parseable_attachment = True
        mime = parts[-1]
        path_str = ":".join(parts[1:-1])
        if "pdf" not in mime.lower():
            continue
        saw_pdf_mime = True
        path = Path(path_str)
        if not path.is_absolute():
            path = bib_dir / path
        if path.is_file():
            return str(path), PDF_RESOLVED
    if saw_pdf_mime:
        return None, PDF_PATH_GONE
    if saw_parseable_attachment:
        return None, PDF_NON_PDF_ATTACHMENT
    return None, PDF_MALFORMED_FILE_FIELD


def _clean_title(title: str) -> str:
    return re.sub(r"[{}]", "", title)


def read_library() -> list[Reference]:
    if not config.BIB_FILE_PATH.exists():
        raise FileNotFoundError(
            f"No bib file at {config.BIB_FILE_PATH}. Export your reference "
            "manager's library to BibTeX at this path -- or point BIB_FILE / "
            "config.toml's [bib].path at wherever you keep it -- then re-run sync."
        )

    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    with open(config.BIB_FILE_PATH) as f:
        bib_database = bibtexparser.load(f, parser=parser)

    bib_dir = config.BIB_FILE_PATH.resolve().parent
    references = []
    for entry in bib_database.entries:
        if "file" in entry:
            pdf_path, pdf_resolution = _resolve_pdf_path(entry["file"], bib_dir)
        else:
            pdf_path, pdf_resolution = None, PDF_NO_FILE_FIELD
        references.append(
            Reference(
                citekey=entry["ID"],
                item_type=entry.get("ENTRYTYPE", "misc"),
                title=_clean_title(entry.get("title", "Untitled")),
                authors=_parse_authors(entry.get("author", "")),
                year=entry.get("year", "n.d."),
                doi=entry.get("doi"),
                url=entry.get("url"),
                fields=entry,
                pdf_path=pdf_path,
                pdf_resolution=pdf_resolution,
            )
        )
    return references

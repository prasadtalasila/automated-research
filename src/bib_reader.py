"""Reads the BibTeX-exported .bib file -- the source of truth for
citekeys and bibliographic metadata (project decision, 2026-07-28).

No auto-sync plugin is installed, so this file is a manual, point-in-time
export from your reference manager, not continuously auto-synced --
re-export it after adding papers, then re-run `python -m src.sync`.
Whatever citekey BibTeX assigns in this file IS the citekey everywhere
downstream (the ledger, citation_gate, generated drafts); this module
never invents its own.

Needs `bibtexparser` (docker/requirements-full.txt, install via
scripts/install_full_pipeline.sh) -- the one dependency the otherwise
stdlib-only core pipeline requires, because hand-rolling a correct
BibTeX parser (nested braces, LaTeX escapes, multi-line values) is a
worse bet than using a maintained library for something citation-critical.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from src import config


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


def _resolve_pdf_path(file_field: str, bib_dir: Path) -> str | None:
    """The `file` field format in this project's bib export:
    `Desc:path:mimetype`, `;`-separated for multiple attachments (e.g. an
    HTML snapshot alongside the PDF) -- an export-tool convention, not
    part of the BibTeX standard itself."""
    for attachment in file_field.split(";"):
        parts = attachment.split(":")
        if len(parts) < 3:
            continue
        mime = parts[-1]
        path_str = ":".join(parts[1:-1])
        if "pdf" not in mime.lower():
            continue
        path = Path(path_str)
        if not path.is_absolute():
            path = bib_dir / path
        if path.exists():
            return str(path)
    return None


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
                pdf_path=_resolve_pdf_path(entry["file"], bib_dir) if "file" in entry else None,
            )
        )
    return references

"""Reads bibliographic metadata directly from a Zotero SQLite library.

Better BibTeX (BBT) is not installed in this environment, so instead of
relying on its auto-exported .bib file, this module reads zotero.sqlite
directly (read-only, immutable) and derives the same kind of linkage BBT
would provide: stable citekeys, resolved attachment paths, and a
generated library.bib for LaTeX use. This has no dependency on any
Zotero plugin and works whether or not Zotero is currently running.

If Better BibTeX is installed later, its citekey convention
(author+year+titleword) is compatible with the one generated here, so
switching is low-friction.
"""

import re
import sqlite3
from dataclasses import dataclass, field

from src import config

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
    "towards", "toward", "using", "based", "via",
}

_BIB_TYPE_MAP = {
    "journalArticle": "article",
    "conferencePaper": "inproceedings",
    "book": "book",
    "bookSection": "incollection",
    "thesis": "phdthesis",
    "report": "techreport",
    "webpage": "misc",
    "preprint": "unpublished",
    "manuscript": "unpublished",
}


@dataclass
class Reference:
    citekey: str
    zotero_key: str
    item_type: str
    title: str
    authors: list[tuple[str, str]]  # (first, last)
    year: str
    doi: str | None
    url: str | None
    fields: dict[str, str] = field(default_factory=dict)
    pdf_path: str | None = None  # absolute path, as str, or None


def _connect_readonly() -> sqlite3.Connection:
    uri = f"file:{config.ZOTERO_SQLITE}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _extract_year(date_value: str | None) -> str:
    if not date_value:
        return "n.d."
    m = re.search(r"\d{4}", date_value)
    return m.group(0) if m else "n.d."


def _slugify_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _first_title_word(title: str) -> str:
    for word in re.findall(r"[A-Za-z0-9]+", title):
        slug = _slugify_word(word)
        if slug and slug not in _STOPWORDS:
            return slug
    return "untitled"


def _make_citekey(authors: list[tuple[str, str]], year: str, title: str, used: set[str]) -> str:
    author_part = _slugify_word(authors[0][1]) if authors else "anon"
    year_part = re.sub(r"[^a-z0-9]", "", year.lower()) or "nd"
    base = f"{author_part}{year_part}{_first_title_word(title)}"
    if base not in used:
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = base + suffix
        if candidate not in used:
            return candidate
    raise RuntimeError(f"Citekey collision exhausted for base '{base}'")


def _resolve_pdf_path(cur: sqlite3.Cursor, parent_item_id: int) -> str | None:
    cur.execute(
        """
        SELECT ia.itemID, i.key, ia.path, ia.contentType
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.parentItemID = ?
        ORDER BY ia.contentType = 'application/pdf' DESC
        """,
        (parent_item_id,),
    )
    for _item_id, attachment_key, path, content_type in cur.fetchall():
        if not path or not path.startswith("storage:"):
            continue
        if content_type != "application/pdf":
            continue
        filename = path[len("storage:"):]
        resolved = config.ZOTERO_STORAGE / attachment_key / filename
        if resolved.exists():
            return str(resolved)
    return None


def read_library() -> list[Reference]:
    """Read all non-attachment items from the Zotero library."""
    con = _connect_readonly()
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE it.typeName != 'attachment'
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            ORDER BY i.itemID
            """
        )
        parents = cur.fetchall()

        references = []
        used_citekeys: set[str] = set()
        for item_id, zotero_key, item_type in parents:
            cur.execute(
                """
                SELECT f.fieldName, idv.value
                FROM itemData id
                JOIN itemDataValues idv ON id.valueID = idv.valueID
                JOIN fieldsCombined f ON id.fieldID = f.fieldID
                WHERE id.itemID = ?
                """,
                (item_id,),
            )
            fields = dict(cur.fetchall())

            cur.execute(
                """
                SELECT c.firstName, c.lastName
                FROM itemCreators ic
                JOIN creators c ON ic.creatorID = c.creatorID
                JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
                WHERE ic.itemID = ? AND ct.creatorType = 'author'
                ORDER BY ic.orderIndex
                """,
                (item_id,),
            )
            authors = [(f or "", l or "") for f, l in cur.fetchall()]

            title = fields.get("title", "Untitled")
            year = _extract_year(fields.get("date") or fields.get("accessDate"))
            citekey = _make_citekey(authors, year, title, used_citekeys)
            used_citekeys.add(citekey)

            references.append(
                Reference(
                    citekey=citekey,
                    zotero_key=zotero_key,
                    item_type=item_type,
                    title=title,
                    authors=authors,
                    year=year,
                    doi=fields.get("DOI"),
                    url=fields.get("url"),
                    fields=fields,
                    pdf_path=_resolve_pdf_path(cur, item_id),
                )
            )
        return references
    finally:
        con.close()


def _bibtex_escape(value: str) -> str:
    return value.replace("{", "\\{").replace("}", "\\}")


def to_bibtex_entry(ref: Reference) -> str:
    entry_type = _BIB_TYPE_MAP.get(ref.item_type, "misc")
    lines = [f"@{entry_type}{{{ref.citekey},"]
    if ref.authors:
        author_str = " and ".join(f"{last}, {first}" if first else last for first, last in ref.authors)
        lines.append(f"  author = {{{_bibtex_escape(author_str)}}},")
    lines.append(f"  title = {{{_bibtex_escape(ref.title)}}},")
    lines.append(f"  year = {{{ref.year}}},")
    if ref.item_type == "journalArticle":
        if "publicationTitle" in ref.fields:
            lines.append(f"  journal = {{{_bibtex_escape(ref.fields['publicationTitle'])}}},")
        for key, bibkey in (("volume", "volume"), ("issue", "number"), ("pages", "pages"), ("publisher", "publisher")):
            if key in ref.fields:
                lines.append(f"  {bibkey} = {{{_bibtex_escape(ref.fields[key])}}},")
    if ref.doi:
        lines.append(f"  doi = {{{ref.doi}}},")
    if ref.url:
        lines.append(f"  url = {{{ref.url}}},")
    lines.append("}")
    return "\n".join(lines)


def write_library_bib(references: list[Reference]) -> None:
    config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "% Auto-generated from Zotero SQLite library. Do not hand-edit;\n"
        "% re-run `python -m src.sync` to regenerate.\n"
    )
    body = "\n\n".join(to_bibtex_entry(r) for r in references)
    config.LIBRARY_BIB_PATH.write_text(header + "\n" + body + "\n")

"""Builds a "## References" section for a genre draft's Markdown source,
sourced only from `content/ledger.sqlite` (populated by `sync` from
`bibliography.bib`, the source of truth). Stdlib-only (`sqlite3`, `json`),
like `citation_gate.py`, so it runs with bare `python3` -- no
`bibtexparser`/venv needed. Deliberately doesn't read `bibliography.bib`
itself; `src/bib_reader.py` is the only module that does (AGENTS.md), which
is why the fields an entry needs beyond title/year (authors, venue, volume,
pages, publisher) travel through the ledger's `bib_fields` column rather
than being re-read from the bib file here.

Only ever lists citekeys the draft already cites (found with
`citation_gate`'s own extraction regexes), so it can never introduce a
citekey that hasn't already passed the gate. Run this *after*
`python -m src.citation_gate` has reported `OK`. A cited key with no
matching ledger row is a hard error (AGENTS.md's citekey invariant), not
something to silently drop.

Entries are numbered IEEE-style ("[1] J. Doe and R. Roe, "Title," IEEE
Trans. Testing, vol. 1, pp. 1-10, 2021.") and ordered by first appearance
in the draft, which is the order pandoc's own citeproc numbers citations
in -- so this list and the rendered PDF's bibliography agree on which
source is [1]. Each entry keeps its citekey in a trailing code span:
inline citations in the draft source are still `[@citekey]`, so the key is
what makes an entry traceable from the text, and a code span is invisible
to `citation_gate` (it blanks code spans before scanning), so listing a
key here can never look like citing it.

Usage:
    python -m src.references <file.md> [--heading TEXT]
Appends a References section, or replaces one if this was already run on
the file (idempotent) -- built from exactly the citekeys `<file.md>`
cites.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from src import citation_gate, ledger

# Matches the References heading this module writes, bare ("## References")
# or numbered to match a draft's own heading convention ("## 6. References"),
# at any heading level -- used both to detect an existing section (for
# render_output.py, which strips it before handing the draft to pandoc)
# and to find where to splice in a replacement.
_HEADING_RE = re.compile(r"^#{1,6}\s*(?:\d+[.)]\s*)?References\s*$", re.IGNORECASE)


def used_citekeys(text: str) -> list[str]:
    """Every citekey cited in `text`, deduped, in order of first appearance.

    First-appearance order rather than alphabetical because the numbers
    this list gets ([1], [2], ...) have to be the same numbers pandoc's
    citeproc assigns when the same draft is rendered to PDF, and citeproc
    numbers by first appearance. Sorted order would produce a Markdown
    list whose [4] is the PDF's [7].
    """
    seen: dict[str, None] = {}
    for _, key in citation_gate.extract_citekeys(text):
        seen.setdefault(key, None)
    return list(seen)


def has_section(text: str) -> bool:
    return section_start(text.splitlines(keepends=True)) is not None


def section_start(lines: list[str]) -> int | None:
    """Index of the References heading in `lines`, or None.

    A heading inside a fenced code block doesn't count. Both callers act
    on the answer destructively -- `apply` replaces everything from here
    down, and render_output strips it from what pandoc sees -- so a
    tutorial that *shows* a `## References` line in an example would
    otherwise have the rest of its lesson silently truncated.
    citation_gate's own code-blanking is what the gate uses to avoid the
    same class of false positive, and it preserves line structure, so
    indices still line up with `lines`.
    """
    blanked = citation_gate._blank_code("".join(lines)).splitlines()
    for i, line in enumerate(blanked):
        if _HEADING_RE.match(line.strip()):
            return i
    return None


def _initials(first: str) -> str:
    """A given-name field as IEEE initials.

    `Jane Mary` -> `J. M.`, `J.-P.` -> `J.-P.`, `` -> ``.
    """
    out = []
    for part in first.replace(".", " ").split():
        # A hyphenated given name initializes on both halves ("Jean-Paul"
        # -> "J.-P."), which is IEEE's own rule and not what a naive
        # part[0] would give.
        out.append("-".join(f"{seg[0]}." for seg in part.split("-") if seg))
    return " ".join(out)


def _format_name(name: str) -> str:
    """One BibTeX author name in IEEE order: "Doe, Jane" -> "J. Doe"."""
    name = name.strip()
    # Braced corporate authors ("{IEEE Standards Association}") are a
    # single unit, never split into given/family or initialized.
    if name.startswith("{") and name.endswith("}"):
        return name[1:-1].strip()
    if "," in name:
        last, first = (p.strip() for p in name.split(",", 1))
    else:
        parts = name.rsplit(" ", 1)
        first, last = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    initials = _initials(first)
    return f"{initials} {last}".strip()


def _format_authors(field: str) -> str:
    """A BibTeX author/editor field as an IEEE author list.

    IEEE abbreviates to "first author et al." past six names; below that
    it lists all of them, with "and" before the last.
    """
    names = [n.strip() for n in field.split(" and ") if n.strip()]
    if not names:
        return ""
    formatted = [_format_name(n) for n in names]
    if len(formatted) > 6:
        return f"{formatted[0]} et al."
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"


# Where the containing work's name lives, in the order BibTeX/biblatex
# variants prefer it. "booktitle" is checked last because an @inbook entry
# can carry both, and there the journal-shaped field is the wrong one.
_VENUE_FIELDS = ("journal", "journaltitle", "booktitle")


def _md_escape(text: str) -> str:
    """Neutralizes Markdown emphasis in a value pasted into an entry.

    A title like "The C_str_ Problem" or "A*B benchmarks" would otherwise
    silently italicize part of the reference list, and a citekey-labelled
    bibliography that renders differently from the bib file it came from
    is exactly the sort of quiet drift this project's citation rules
    exist to prevent.
    """
    return re.sub(r"([*_`\[\]])", r"\\\1", text)


def format_entry(citekey: str, title: str, year: str, fields: dict[str, str]) -> str:
    """One IEEE-style bibliography entry, without its "[n] " number.

    `fields` is the ledger's `bib_fields` for this citekey (see
    ledger._BIB_FIELDS_KEPT), and may be empty -- a row synced before that
    column existed, or an entry that genuinely carries nothing but a
    title. The entry then degrades to title and year rather than failing:
    a thinner reference is still a true one, and `sync` is what fixes it.
    """
    fields = {k.lower(): v for k, v in fields.items()}
    parts: list[str] = []

    authors = _format_authors(fields.get("author", ""))
    if not authors and fields.get("editor"):
        authors = f"{_format_authors(fields['editor'])}, Eds."
    if authors:
        parts.append(_md_escape(authors))

    title = _md_escape((title or "").strip().rstrip("."))
    if title:
        # IEEE quotes the title of a work published *inside* something
        # else (an article in a journal, a paper in proceedings) and
        # italicizes the title of a work that is itself the publication (a
        # book, a thesis, a standalone report). The presence of a
        # container field is what distinguishes the two, and is more
        # reliable here than the entry type: this corpus's exports use
        # @misc for both preprints and books.
        has_container = any(fields.get(f) for f in _VENUE_FIELDS)
        parts.append(f'"{title},"' if has_container else f"*{title}*")

    venue = next((fields[f] for f in _VENUE_FIELDS if fields.get(f)), "")
    if venue:
        venue = _md_escape(venue.strip())
        # "in" only for a paper inside a proceedings/edited volume, which
        # is what a booktitle (rather than a journal) means.
        prefix = "in " if fields.get("booktitle") and not fields.get("journal") else ""
        parts.append(f"{prefix}*{venue}*")

    if fields.get("volume"):
        parts.append(f"vol. {_md_escape(fields['volume'])}")
    if fields.get("number"):
        parts.append(f"no. {_md_escape(fields['number'])}")
    if fields.get("pages"):
        # BibTeX page ranges are "1--10"; IEEE prints an en dash, and the
        # doubled hyphen is a TeX-ism that shouldn't reach a Markdown reader.
        pages = re.sub(r"-{2,}", "–", fields["pages"].strip())
        label = "pp." if re.search(r"[–,]", pages) else "p."
        parts.append(f"{label} {_md_escape(pages)}")

    for field_name in ("school", "institution", "publisher", "organization"):
        if fields.get(field_name):
            parts.append(_md_escape(fields[field_name].strip()))
            break

    if year:
        parts.append(_md_escape(str(year).strip()))

    entry = _join(parts)
    if not entry:
        return f"{citekey}."
    # A value can already end the sentence itself -- an undated entry's
    # year is the literal "n.d.", which would otherwise close as "n.d..".
    return entry if entry.endswith(".") else f"{entry}."


def _join(parts: list[str]) -> str:
    """Comma-joins entry parts without doubling punctuation.

    A quoted title already carries IEEE's comma *inside* the quotes
    (`"Title,"`), so the separator before the next part is a space, not
    another comma -- otherwise every article entry reads `"Title,",
    *Journal*`.
    """
    out = ""
    for part in (p for p in parts if p):
        if not out:
            out = part
        elif out.endswith(',"'):
            out += f" {part}"
        else:
            out += f", {part}"
    return out


def build_section(citekeys: list[str], con, heading: str = "References") -> str:
    placeholders = ",".join("?" * len(citekeys))
    rows = {
        citekey: (title, year, bib_fields)
        for citekey, title, year, bib_fields in con.execute(
            f"SELECT citekey, title, year, bib_fields FROM items WHERE citekey IN ({placeholders})",
            citekeys,
        )
    }
    missing = [k for k in citekeys if k not in rows]
    if missing:
        raise KeyError(
            "citekey(s) cited in the draft but missing from the ledger -- "
            "run `python -m src.sync`, or re-check `python -m src.citation_gate` "
            f"was run and passed first: {', '.join(missing)}"
        )

    lines = [f"## {heading}", ""]
    for number, key in enumerate(citekeys, start=1):
        title, year, bib_fields = rows[key]
        # A row written before the bib_fields column existed stores NULL;
        # a value that isn't valid JSON would mean a hand-edited ledger.
        # Both fall back to the title/year columns rather than failing --
        # the next `python -m src.sync` repopulates either one.
        try:
            fields = json.loads(bib_fields) if bib_fields else {}
        except (TypeError, ValueError):
            fields = {}
        entry = format_entry(key, title, year, fields)
        lines.append(f"[{number}] {entry} `{key}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply(path: Path, heading: str = "References") -> str:
    text = path.read_text()
    keys = used_citekeys(text)
    if not keys:
        return f"{path}: no citekeys cited -- nothing to do"

    con = ledger.connect()
    try:
        section = build_section(keys, con, heading)
    finally:
        con.close()

    lines = text.splitlines(keepends=True)
    idx = section_start(lines)
    head = "".join(lines[:idx]) if idx is not None else text
    new_text = head.rstrip() + "\n\n" + section.rstrip() + "\n"
    path.write_text(new_text)
    return f"{path}: wrote References section with {len(keys)} citekey(s)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append/replace a References section built from a Markdown draft's own cited citekeys."
    )
    parser.add_argument("input", help="Path to the draft file (Markdown)")
    parser.add_argument(
        "--heading", default="References",
        help='Heading text, e.g. "6. References" to match a draft\'s own numbered headings (default: "References")',
    )
    args = parser.parse_args()

    try:
        print(apply(Path(args.input), args.heading))
    except KeyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

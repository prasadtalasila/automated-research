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

For a reader who wants numbers inline as well, `numbered_markdown` (used
by `render_output --format md`) returns a *derived* copy with every
`[@citekey]` replaced by its number. That copy is never the draft: the
draft's literal citekeys are what `citation_gate` verifies and what
pandoc resolves, and a numbered body would leave the gate reporting
"0 citations ... OK" on a draft it can no longer check at all.

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


def build_section(citekeys: list[str], con, heading: str = "References",
                  label_citekeys: bool = True) -> str:
    """The References section for `citekeys`, numbered in the given order.

    `label_citekeys` appends each entry's key in a code span. On by
    default, because in the *draft* the inline markers are `[@citekey]`
    and the label is what ties one to its entry. The numbered copy
    (`numbered_markdown`) turns it off: there the inline markers are
    numbers, the numbers already index this list, and a trailing key
    would just be noise to a reader.
    """
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
        lines.append(f"[{number}] {entry} `{key}`" if label_citekeys else f"[{number}] {entry}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# A bracketed Pandoc citation containing nothing but citekeys separated
# by ";" -- `[@a]`, `[@a; @b; @c]`, `[-@a]`. Deliberately does NOT match a
# group carrying a prefix or locator (`[see @a, p. 33]`): collapsing that
# to a bare number would silently delete the words around it. Those are
# handled one key at a time by _BARE_KEY_RE below, which leaves the
# surrounding text alone.
_CITATION_GROUP_RE = re.compile(
    r"\[\s*-?@[A-Za-z][A-Za-z0-9_-]*(?:\s*;\s*-?@[A-Za-z][A-Za-z0-9_-]*)*\s*\]"
)
# citation_gate's own Pandoc-citation regex, not a second definition of
# one. Its negative lookbehind is what keeps `@` inside a larger token
# from reading as a citation -- this project's own tutorial draft carries
# an author's email address, and a looser pattern would rewrite the
# `@gmail` in it the moment a citekey happened to be named `gmail`.
# Sharing the gate's pattern also guarantees that what gets renumbered
# here is exactly what the gate verified and what used_citekeys() counted;
# two patterns that drifted apart would silently leave a real citation
# un-numbered, or number something that was never a citation.
_BARE_KEY_RE = citation_gate._PANDOC_CITE_RE
# IEEE, and the CSL style's own `collapse="citation-number"`, only
# contract a run of *three or more*: [1], [2] stays as it is, [3]-[5]
# collapses. Matching that keeps the numbered Markdown identical to what
# the same draft's PDF shows.
_MIN_COLLAPSIBLE_RUN = 3


def _format_numbers(numbers: list[int]) -> str:
    """`[1]`, `[1], [2]`, `[3]–[6]` -- IEEE's own contraction rules."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])

    out = []
    for run in runs:
        if len(run) >= _MIN_COLLAPSIBLE_RUN:
            out.append(f"[{run[0]}]–[{run[-1]}]")
        else:
            out.extend(f"[{n}]" for n in run)
    return ", ".join(out)


def renumber(text: str, numbers: dict[str, int]) -> str:
    """Rewrites `text`'s citekey markers as IEEE numbers from `numbers`.

    Scans a code-blanked copy to locate the citations, then edits the
    original at those offsets -- `citation_gate._blank_code` replaces a
    fenced block or code span with spaces while preserving every
    character position, so a `[@key]` shown inside an example (which the
    gate itself ignores) is left exactly as written here too.

    A key with no number -- which can only happen if a caller passes a
    partial map -- is left untouched rather than rendered as `[None]`.
    """
    blanked = citation_gate._blank_code(text)

    edits: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []
    for match in _CITATION_GROUP_RE.finditer(blanked):
        keys = _BARE_KEY_RE.findall(match.group())
        # Marked covered either way, so that a group holding even one
        # unnumbered key is left exactly as written. Without this the
        # per-key pass below would still rewrite its *known* keys and
        # leave `[[1]; @zzz]` -- a mangling that is worse than the
        # untouched marker, which at least reads as an obvious omission.
        covered.append((match.start(), match.end()))
        if any(k not in numbers for k in keys):
            continue
        edits.append((match.start(), match.end(), _format_numbers([numbers[k] for k in keys])))

    # Anything left: a bare `@key`, or one inside a group with a prefix or
    # locator. Replaced individually so the words around it survive.
    for match in _BARE_KEY_RE.finditer(blanked):
        if any(start <= match.start() < end for start, end in covered):
            continue
        number = numbers.get(match.group(1))
        if number is not None:
            edits.append((match.start(), match.end(), f"[{number}]"))

    out = []
    position = 0
    for start, end, replacement in sorted(edits):
        out.append(text[position:start])
        out.append(replacement)
        position = end
    out.append(text[position:])
    return "".join(out)


def numbered_markdown(text: str, con, heading: str | None = None) -> str:
    """`text` with its citekeys replaced by IEEE numbers, over a matching
    reference list.

    The draft keeps `[@citekey]` markers -- they are what
    `citation_gate` verifies, and what pandoc resolves when rendering --
    so this returns a *derived* copy for a reader who wants numbered
    Markdown rather than a PDF. Both are numbered by first appearance,
    so this copy, the draft's own reference list, and the rendered PDF
    all agree on which source is [1].

    Any existing References section is rebuilt rather than renumbered:
    the entries have to match the numbering this just applied, and
    rebuilding from the ledger is the only way to be sure they do. The
    draft's own heading text is reused when it has one, so a chapter
    numbering its headings (`## 6. References`) keeps doing so.
    """
    keys = used_citekeys(text)
    # Checked before anything is removed. A draft with no citations has
    # nothing to number and nothing to rebuild, so it comes back exactly
    # as it went in -- otherwise a document that merely *has* a section
    # matching the heading (a hand-written "References" of URLs, say)
    # would come back with that section silently deleted.
    if not keys:
        return text

    lines = text.splitlines(keepends=True)
    index = section_start(lines)
    if index is not None:
        if heading is None:
            heading = re.sub(r"^#+\s*", "", lines[index].strip())
        text = "".join(lines[:index]).rstrip() + "\n"

    body = renumber(text, {key: number for number, key in enumerate(keys, start=1)})
    section = build_section(keys, con, heading or "References", label_citekeys=False)
    return body.rstrip() + "\n\n" + section


def write_numbered(path: Path, out_dir: Path) -> Path:
    """Writes `path`'s numbered copy into `out_dir`, returning its path."""
    con = ledger.connect()
    try:
        rendered = numbered_markdown(path.read_text(), con)
    finally:
        con.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}.md"
    out_path.write_text(rendered)
    return out_path


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

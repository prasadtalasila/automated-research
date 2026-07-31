"""Builds a "## References" section for a genre draft's Markdown source,
sourced only from `content/ledger.sqlite` (citekey/title/year -- populated
by `sync` from `bibliography.bib`, the source of truth). Stdlib-only
(`sqlite3`), like `citation_gate.py`, so it runs with bare `python3` -- no
`bibtexparser`/venv needed. Deliberately doesn't read `bibliography.bib`
itself; `src/bib_reader.py` is the only module that does (AGENTS.md).

Only ever lists citekeys the draft already cites (found with
`citation_gate`'s own extraction regexes), so it can never introduce a
citekey that hasn't already passed the gate. Run this *after*
`python -m src.citation_gate` has reported `OK`. A cited key with no
matching ledger row is a hard error (AGENTS.md's citekey invariant), not
something to silently drop.

Format matches the one already documented in
`.claude/skills/deep-research/reference.md` §5: "citekey -- Title (Year)".

Usage:
    python -m src.references <file.md> [--heading TEXT]
Appends a References section, or replaces one if this was already run on
the file (idempotent) -- built from exactly the citekeys `<file.md>`
cites, listed in the same `[@citekey]`/`\\cite{...}` form the reader
already sees inline, so each entry is traceable by the literal key.
"""

import argparse
import re
import sys
from pathlib import Path

from src import citation_gate, ledger

# Matches the References heading this module writes, bare ("## References")
# or numbered to match a draft's own heading convention ("## 6. References"),
# at any heading level -- used both to detect an existing section (for
# suppress-bibliography in render_output.py) and to find where to splice
# in a replacement.
_HEADING_RE = re.compile(r"^#{1,6}\s*(?:\d+[.)]\s*)?References\s*$", re.IGNORECASE)


def used_citekeys(text: str) -> list[str]:
    """Every citekey already cited in `text`, deduped and sorted."""
    keys: set[str] = set()
    for line in text.splitlines():
        keys.update(citation_gate.extract_citekeys_from_line(line))
    return sorted(keys)


def has_section(text: str) -> bool:
    return any(_HEADING_RE.match(line.strip()) for line in text.splitlines())


def _section_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line.strip()):
            return i
    return None


def build_section(citekeys: list[str], con, heading: str = "References") -> str:
    placeholders = ",".join("?" * len(citekeys))
    rows = {
        citekey: (title, year)
        for citekey, title, year in con.execute(
            f"SELECT citekey, title, year FROM items WHERE citekey IN ({placeholders})",
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
    for key in citekeys:
        title, year = rows[key]
        lines.append(f"- **{key}** -- {title} ({year}).")
    lines.append("")
    return "\n".join(lines)


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
    idx = _section_start(lines)
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

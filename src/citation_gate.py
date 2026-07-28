"""Citation verification gate.

Every genre skill (survey/thesis-chapter/tutorial) MUST run this
against its own output before presenting a draft as finished. It is a
hard gate, not advisory: a citekey that doesn't resolve to something
`sync` actually pulled from the bib file is treated as fabricated and
blocks the draft.

This is not a hypothetical concern -- papers/DT-Simulation-Patterns/main.bib
in this same environment already contains entries a prior review marked
"WARNING: UNVERIFIABLE" (fabricated placeholders). A generative writer
must never be allowed to invent a citekey; it may only cite what is in
the ledger.

Usage:
    python -m src.citation_gate <file> [<file> ...]

Recognizes both LaTeX (\\cite, \\citep, \\citet, \\parencite, \\textcite,
\\autocite, \\citeauthor, \\citeyear, with optional * and [] options) and
Pandoc/Markdown ([@key], [@key1; @key2], bare @key) citation syntax.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src import ledger

_LATEX_CITE_RE = re.compile(
    r"\\(?:cite|citep|citet|parencite|textcite|autocite|citeauthor|citeyear)"
    r"\*?(?:\[[^\]]*\])*\{([^}]+)\}"
)
# Pandoc only treats @ as a citation marker when it isn't part of a larger
# token -- otherwise `\href{mailto:name@example.com}` (this project's own
# papers/ directory has author emails) would be misread as a citation.
# Citekey body includes '-' because bibtexparser-generated keys do (e.g.
# `jacoby_open-source_2023`, or a reference manager's own `-1`/`-2`
# disambiguation suffixes on duplicate entries) -- roughly a quarter of
# this project's synced citekeys contain one, so excluding it silently
# truncated matches.
_PANDOC_CITE_RE = re.compile(r"(?<![A-Za-z0-9._%+-])-?@([A-Za-z][A-Za-z0-9_-]*)")


@dataclass
class GateResult:
    path: Path
    unknown: list[tuple[int, str]] = field(default_factory=list)  # (line_no, citekey)
    total_citations: int = 0

    @property
    def ok(self) -> bool:
        return not self.unknown


def extract_citekeys_from_line(line: str) -> list[str]:
    keys: list[str] = []
    for match in _LATEX_CITE_RE.finditer(line):
        keys.extend(k.strip() for k in match.group(1).split(","))
    for match in _PANDOC_CITE_RE.finditer(line):
        keys.append(match.group(1))
    return keys


def check_document(path: Path, known_citekeys: set[str]) -> GateResult:
    result = GateResult(path=path)
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        for key in extract_citekeys_from_line(line):
            result.total_citations += 1
            if key not in known_citekeys:
                result.unknown.append((line_no, key))
    return result


def run(paths: list[str]) -> int:
    con = ledger.connect()
    try:
        known = ledger.known_citekeys(con)
    finally:
        con.close()

    if not known:
        print(
            "WARNING: ledger is empty -- run `python -m src.sync` first. "
            "Every citekey will be reported as unknown.",
            file=sys.stderr,
        )

    all_ok = True
    for p in paths:
        result = check_document(Path(p), known)
        if result.ok:
            print(f"OK    {p}: {result.total_citations} citation(s), all verified against the ledger.")
        else:
            all_ok = False
            print(f"FAIL  {p}: {len(result.unknown)} unresolved citekey(s):")
            for line_no, key in result.unknown:
                print(f"        {p}:{line_no}: @{key} not found in ledger -- not sourced from bib sync")

    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m src.citation_gate <file> [<file> ...]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(sys.argv[1:]))

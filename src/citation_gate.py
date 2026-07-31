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

Recognizes any LaTeX/biblatex/natbib command whose name contains "cite"
(\\cite, \\citep, \\citealp, \\footcite, \\nocite, capitalized biblatex
forms, ..., with optional * and [] options) and Pandoc/Markdown ([@key],
[@key1; @key2], bare @key, suppressed-author -@key) citation syntax. Code
fences, inline code spans, and LaTeX verbatim/lstlisting/minted
environments are excluded from scanning first (see _blank_code).
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src import ledger

# Matches the command name by substring ("contains cite/Cite") rather than
# an explicit list of the standard cite/citep/citet/... names -- an earlier,
# enumerated version of this regex silently missed \citealp, \citealt,
# \footcite, \smartcite, \fullcite, \nocite, \citenum, \citeyearpar, and
# every capitalized biblatex form (\Citep, \Textcite, ...): ordered
# alternation tries "cite" first, matches as a prefix of "citealp", then
# fails to find the "{" that must immediately follow and never backs off to
# try the longer alternatives. That's a false negative on the invariant
# this gate exists to enforce (a fabricated key in an unrecognized command
# reads as "0 citations" instead of "unresolved"), so err toward matching
# too much (a stray "\path{cite-me}"-shaped command) over too little.
# \s* before the star, each [...] option group, and the final {...}: TeX
# itself skips whitespace (including a newline) between a control word and
# its arguments, so `\citep {key}` and `\citep\n{key}` are both valid and
# equivalent to `\citep{key}` -- without this, either would silently miss
# a real (or fabricated) citekey, the same false-negative class the rest
# of this regex already exists to close.
_LATEX_CITE_RE = re.compile(
    r"\\[A-Za-z]*[Cc]ite[A-Za-z]*"
    r"\s*\*?(?:\s*\[[^\]]*\])*\s*\{([^}]+)\}"
)
# Pandoc only treats @ as a citation marker when it isn't part of a larger
# token -- otherwise `\href{mailto:name@example.com}` (this project's own
# papers/ directory has author emails) would be misread as a citation.
# Citekey body includes '-' because bibtexparser-generated keys do (e.g.
# `jacoby_open-source_2023`, or a reference manager's own `-1`/`-2`
# disambiguation suffixes on duplicate entries) -- roughly a quarter of
# this project's synced citekeys contain one, so excluding it silently
# truncated matches. Backslash is excluded too: LaTeX's internal
# @-as-letter idiom (\makeatletter ... \@ifundefined{...}{}{} ...
# \makeatother, pandoc's own rendered .tex templates use this) would
# otherwise misread as a citation on `\@ifundefined` -- found via a
# retro-sweep over rendered .tex output, and load-bearing now that
# thesis-chapter-writer's content/drafts/<slug>.tex is hook-gated too.
_PANDOC_CITE_RE = re.compile(r"(?<![A-Za-z0-9._%+\-\\])-?@([A-Za-z][A-Za-z0-9_-]*)")

# tutorial-writer's whole job is worked code examples, and code routinely
# contains @-tokens that look like a Pandoc citation (Python's @dataclass,
# @property) or a LaTeX-command-shaped string that isn't one -- these are
# false positives, not the false negatives above, but with a PostToolUse
# hook (.claude/hooks/citation_gate_hook.py) now treating a FAIL as
# blocking, a false positive here actively pushes the agent to delete
# valid teaching code instead of a real fabricated citation. Blank out
# (not delete -- must preserve every other character's offset, since line
# numbers are computed from position in the original text) fenced code,
# inline code spans, and LaTeX verbatim-style environments before
# extraction.
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_LATEX_VERBATIM_RE = re.compile(
    r"\\begin\{(verbatim|lstlisting|minted)\*?\}.*?\\end\{\1\*?\}", re.DOTALL
)


def _blank_code(text: str) -> str:
    def _blank_match(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    text = _FENCED_CODE_RE.sub(_blank_match, text)
    text = _LATEX_VERBATIM_RE.sub(_blank_match, text)
    text = _INLINE_CODE_RE.sub(_blank_match, text)
    return text


@dataclass
class GateResult:
    path: Path
    unknown: list[tuple[int, str]] = field(default_factory=list)  # (line_no, citekey)
    total_citations: int = 0

    @property
    def ok(self) -> bool:
        return not self.unknown


def extract_citekeys_from_line(line: str) -> list[str]:
    """Back-compat, single-line-scoped wrapper around extract_citekeys().

    Kept for src/citation_coverage.py (the remaining caller that only ever
    hands this one line at a time -- src/references.py was switched to
    call extract_citekeys() directly in this same change) and for the
    existing test suite, which exercises this shape extensively.

    This is NOT complete in two ways, both stemming from the same cause:
    a caller feeding one line at a time only ever hands this wrapper text
    that already had its newlines cut out by something like str.splitlines().
    - False positive: a fenced code block spanning multiple lines needs
      both its opening and closing ``` in the same string for _blank_code
      to recognize it, so an in-code @token on its own line can still read
      as a citation.
    - False negative: TeX allows whitespace -- including a newline --
      between a control word and its argument (\\citep\n{key} is valid,
      equivalent to \\citep{key}), but a command on one line and its
      {key} argument on the next arrive at this wrapper as two separate,
      independently-unmatchable calls; neither one contains the whole
      pattern. extract_citekeys(text) run on the whole document catches
      this (see test_whitespace_including_newline_between_command_and_brace)
      because the newline between them is still present in its input.
    citation_coverage.py (the only remaining per-line caller) is
    informational-only, never a gate, so both gaps are known and
    low-stakes rather than worth complicating this wrapper's contract to
    close. See extract_citekeys() for the whole-document scan that gets
    all of this right -- prefer it for any new caller that has the whole
    document available.
    """
    return [key for _, key in extract_citekeys(line)]


def extract_citekeys(text: str) -> list[tuple[int, str]]:
    """Every citekey in `text` as (1-based line number, key).

    Scans the whole document rather than line-by-line so a `\\citep{...}`
    argument wrapped across lines (common once a document has more than a
    couple of citekeys in one call) is still caught -- a per-line scan
    would match on neither line and silently drop every key inside it.
    """
    text = _blank_code(text)

    # (start_offset, key) from both regexes, sorted into true document
    # order first -- LaTeX and Pandoc matches were previously collected in
    # two separate passes and concatenated, so a FAIL report could list a
    # later-in-file LaTeX citation before an earlier Pandoc one, making it
    # harder to locate by reading top-to-bottom.
    matches: list[tuple[int, str]] = []
    for match in _LATEX_CITE_RE.finditer(text):
        matches.extend((match.start(), k.strip()) for k in match.group(1).split(",") if k.strip())
    for match in _PANDOC_CITE_RE.finditer(text):
        matches.append((match.start(), match.group(1)))
    matches.sort(key=lambda m: m[0])

    # One forward sweep instead of a fresh text.count("\n", 0, ...) per
    # match (previously O(text length) per citation, so O(N*M) overall) --
    # each match only needs the newlines since the previous match's start.
    keys: list[tuple[int, str]] = []
    line_no = 1
    pos = 0
    for start, key in matches:
        line_no += text.count("\n", pos, start)
        pos = start
        keys.append((line_no, key))
    return keys


def check_document(path: Path, known_citekeys: set[str]) -> GateResult:
    result = GateResult(path=path)
    for line_no, key in extract_citekeys(path.read_text()):
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

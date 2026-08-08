"""The working state that produced a draft, kept on disk so a later
session can revise it without re-running the drafting pipeline.

`citation_gate`, `references`, `render_output` and `citation_provenance`
all operate statelessly on a draft *file*: hand any of them a draft from
last month and they work. The drafting layer is the only stateful part
of this pipeline, and until this module none of that state was written
down -- it lived in one chat session and died with it. So "shorten
section 3" cost a full re-run: retrieve, score every candidate again,
re-cluster, rewrite. This module is the missing half, and
docs/DRAFT-ITERATION.md is the argument for why it is shaped this way.

**One dossier per draft, mirroring the draft's own path.** A dossier
directory is `content/dossiers/` plus the draft's path relative to
`content/drafts/`, minus the suffix:

    content/drafts/dt-for-engineers/survey.md
    -> content/dossiers/dt-for-engineers/survey/

That rule is mechanical, needs no registry to map one to the other, and
handles both layouts this repo actually contains -- the flat
`content/drafts/<slug>.md` the genre skills describe, and the
`content/drafts/<topic>/<genre>.md` the shipped example content uses.

**Markdown, not JSON.** Everything a dossier holds is read by a model or
a human, both of which read Markdown natively; nothing here is a data
structure some other module consumes. Markdown also means a restored
tarball is legible on its own a year later, without this code.

**Several files, not one**, because a revision should load only what it
needs: the scope and the section map are small and always relevant, the
rejected-candidate list is the largest and is only needed when a change
opens a sub-theme up for re-searching, and `retrieval.md` is written by
the tooling and read by nobody until someone asks what a run cost.

Deliberately *not* a gate and not a lock-taker. Nothing here blocks a
draft, and nothing here writes to the corpus layer -- the ledger is only
ever opened read-only, and only to answer "has the corpus moved since
this draft was written?". A dossier that is missing, stale or
hand-edited degrades the next revision's efficiency; it can never make a
draft wrong.

That is why `status()` reports rather than raising: a missing ledger, a
missing draft, an unparsable fingerprint and a hand-edited file all come
back as something to print. The one thing its *CLI* treats as an error is
a dossier that does not exist at all -- `_cmd_status` exits 1 there,
because "there is nothing to report yet, run `init`" is an actionable
condition a script should be able to branch on, unlike "this dossier
exists and the corpus has moved".

`status --all` extends the same posture across every dossier at once:
which drafts cite a citekey the ledger has since lost, and which new
papers this draft's own recorded queries would have surfaced. It always
exits 0 -- drift is the normal state of a live corpus -- and, like
everything else here, it writes nothing: see `_ephemeral_index` for why
`src.retrieval.search()` cannot be used to do the matching.

Stdlib only (re/sqlite3/tarfile/hashlib), like citation_gate.py,
references.py and citation_provenance.py -- runs with bare `python3`, no
venv, on a machine where the corpus was never built.

Usage:
    python3 -m src.dossier init content/drafts/<name>.md --genre survey
    python3 -m src.dossier status content/drafts/<name>.md
    python3 -m src.dossier status --all [--json]
    python3 -m src.dossier sections content/drafts/<name>.md
    python3 -m src.dossier list
    python3 -m src.dossier export [<name> ...] [--out FILE] [--with-rendered]
    python3 -m src.dossier restore <archive.tar.gz> [--force]
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tarfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from src import config

# The files a dossier holds, in the order `init` writes them and `status`
# reports them. The value is how `status` counts entries in that file --
# see `_count`, and the "counts are advisory" note there.
FILES: dict[str, str] = {
    "scope.md": "prose",
    "evidence.md": "blocks",
    "rejected.md": "rows",
    "sections.md": "rows",
    "steering.md": "prose",
    "revisions.md": "prose",
    "retrieval.md": "rows",
}

# Top-level directories a bundle may contain, and the only ones `restore`
# will unpack. A whitelist rather than a blocklist: an archive member
# naming anything else is refused outright, so a hand-edited or
# hostile tarball cannot write outside the three directories this
# module owns.
ARCHIVE_ROOTS = ("drafts", "dossiers", "rendered")


class DossierError(Exception):
    """A path that isn't a draft, or an archive that isn't safe to unpack."""


# --------------------------------------------------------------------------
# Locating a dossier
# --------------------------------------------------------------------------


def dossier_dir(draft: Path) -> Path:
    """Where `draft`'s dossier lives.

    Raises rather than guessing if the draft isn't under
    `content/drafts/`: the mirroring rule is the only thing tying the two
    together, and a dossier written somewhere unmirrored would be found
    by nothing later.
    """
    resolved = Path(draft).resolve()
    drafts_dir = config.DRAFTS_DIR.resolve()
    try:
        relative = resolved.relative_to(drafts_dir)
    except ValueError:
        raise DossierError(
            f"{draft} is not under {config.DRAFTS_DIR}. A dossier mirrors its "
            "draft's path, so the draft has to live where the genre skills "
            "save it."
        ) from None
    return config.DOSSIERS_DIR / relative.with_suffix("")


def draft_name(draft: Path) -> str:
    """The draft's path relative to `content/drafts/`, suffix dropped --
    the name `export` matches against and `list` prints."""
    resolved = Path(draft).resolve()
    try:
        relative = resolved.relative_to(config.DRAFTS_DIR.resolve())
    except ValueError:
        return Path(draft).stem
    return relative.with_suffix("").as_posix()


def find_draft(dossier: Path) -> Path | None:
    """The draft a dossier belongs to, if it is still on disk.

    The inverse of `dossier_dir`, except that the suffix was dropped on
    the way in -- so this looks for any suffix a genre skill emits
    (`.md` from four of them, `.tex` from thesis-chapter-writer).
    """
    try:
        relative = dossier.resolve().relative_to(config.DOSSIERS_DIR.resolve())
    except ValueError:
        return None
    for suffix in (".md", ".tex"):
        candidate = config.DRAFTS_DIR / relative.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def all_dossiers() -> list[Path]:
    """Every dossier directory, nearest-first by name."""
    if not config.DOSSIERS_DIR.is_dir():
        return []
    found = {
        path.parent
        for path in config.DOSSIERS_DIR.rglob("*.md")
        if path.name in FILES
    }
    return sorted(found)


# --------------------------------------------------------------------------
# The corpus fingerprint
# --------------------------------------------------------------------------


def _corpus_rows() -> list[sqlite3.Row] | None:
    """Every ledger item, or None if there is no readable ledger.

    Opened read-only and with `timeout=0`, exactly as `src.ledger`'s own
    CLI does and for the same reason: this is an inspection, and it must
    not take a write lock, run a migration, or block behind a sync that
    happens to be mid-run. `src.ledger.connect()` would do all three --
    it mkdirs `content/`, executes the schema and runs migrations -- so
    nothing here goes through it, and `src.retrieval.search()`, which
    does, is off limits for the same reason (see `_ephemeral_index`).

    Three columns rather than one because the drift scan needs the same
    fields `src.retrieval` indexes on: `title` and `parsed_path` are what
    a BM25 entry is built from, and `title` is also what makes a reported
    candidate legible without a second lookup.
    """
    if not config.LEDGER_PATH.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{config.LEDGER_PATH}?mode=ro", uri=True, timeout=0)
    except sqlite3.Error:
        return None
    try:
        con.row_factory = sqlite3.Row
        return con.execute("SELECT citekey, title, parsed_path FROM items").fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


def known_citekeys() -> set[str] | None:
    """Every citekey in the ledger, or None if there is no readable one.

    None (rather than an empty set) distinguishes "no corpus on this
    machine" from "a corpus with nothing in it" -- `status` says
    different things about those two.
    """
    rows = _corpus_rows()
    return None if rows is None else {row["citekey"] for row in rows}


def digest(citekeys: set[str]) -> str:
    """A short, order-independent fingerprint of a set of citekeys.

    Twelve hex characters, which is plenty to answer the only question
    asked of it -- "is this the same corpus the draft was written
    against?" -- and short enough to sit on one line of `scope.md`
    without looking like something a reader has to parse.
    """
    joined = "\n".join(sorted(citekeys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


# `- corpus: 501 citekeys, digest `a1b2c3d4e5f6`` in scope.md. Written by
# `init`, read by `status`, and safe to be absent -- a hand-written
# dossier that never recorded one just loses the drift check.
_CORPUS_LINE = re.compile(
    r"^-\s*corpus:\s*(\d+)\s+citekeys?,\s*digest\s*`?([0-9a-f]+)`?", re.MULTILINE
)


def recorded_corpus(dossier: Path) -> tuple[int, str] | None:
    """(citekey count, digest) as recorded in `scope.md` at draft time."""
    scope = dossier / "scope.md"
    if not scope.is_file():
        return None
    match = _CORPUS_LINE.search(scope.read_text(encoding="utf-8"))
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _citekeys_in(dossier: Path, names: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for name in names:
        path = dossier / name
        if path.is_file():
            found |= set(_CITEKEY_TOKEN.findall(path.read_text(encoding="utf-8")))
    return found


# The files that mean "this draft *stands on* that paper", as opposed to
# "this draft *looked at* it". The split matters for drift: a citekey
# that leaves the ledger is a finding when the draft cites it and a
# non-event when the draft turned it down, and `MENTIONED_FILES` would
# report the second as the first.
CITED_FILES = ("evidence.md", "sections.md")
MENTIONED_FILES = ("evidence.md", "rejected.md", "sections.md")


def cited_citekeys(dossier: Path) -> set[str]:
    """Every citekey the dossier mentions, kept or rejected.

    Used to answer "which papers in the corpus were never considered for
    this draft?" -- a more actionable drift signal than a count, because
    it names what to go and look at. Matched loosely (any backticked
    token that looks like a BibTeX key) so that a hand-edited
    `evidence.md` still contributes.
    """
    return _citekeys_in(dossier, MENTIONED_FILES)


def rejected_reasons(dossier: Path) -> dict[str, str]:
    """citekey -> why `rejected.md` says this draft turned it down.

    The reason is the part that does not survive being reduced to a set.
    "Turned down because the corpus had nothing better" and "turned down
    because it is about a different field" age completely differently,
    and only the first is worth revisiting when the corpus grows -- so a
    re-grounding pass needs the sentence, not just the membership.
    """
    path = dossier / "rejected.md"
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in _ROW_SPLIT.split(stripped.strip("|"))]
        if len(cells) != 3:
            continue
        for citekey in _CITEKEY_TOKEN.findall(cells[0]):
            found[citekey] = cells[2]
    return found


def section_citekeys(dossier: Path) -> dict[str, list[str]]:
    """citekey -> the `sections.md` sections that cite it.

    The point is scope, not bookkeeping: a reviser handed "this citekey
    left the ledger" still has to find the prose that leans on it, and
    reading the whole draft to find out is the cost `sections` and this
    both exist to avoid. Absent or hand-mangled rows map nothing, like
    every other read here.
    """
    path = dossier / "sections.md"
    if not path.is_file():
        return {}
    found: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in _ROW_SPLIT.split(stripped.strip("|"))]
        if len(cells) != 2:
            continue
        for citekey in _CITEKEY_TOKEN.findall(cells[1]):
            found.setdefault(citekey, []).append(cells[0])
    return found


# A citekey as the dossier templates write one: inside backticks, starting
# with a letter, and carrying at least one run of `_`/`:`/`-` separators
# followed by more alphanumerics -- the shape BibTeX gives a key
# (`talasila_composable_2025`). Requiring a separator is what keeps
# ordinary backticked prose out: `status` and `content` have none, and
# `--force` also fails the letter start.
#
# The separator run is `+`, not a single character, because a real key in
# this project's own corpus is `zech_digital-twins-as--service_2024` --
# BibTeX collapses "as-a-service" into a doubled hyphen. Matching only one
# separator dropped it silently.
#
# Only false *negatives* matter here. This set is subtracted from the
# ledger's citekeys to find what a dossier never considered, so a prose
# token that looks key-shaped (`draft-reviser`) is inert -- it is not in
# the ledger, so subtracting it changes nothing. A missed real key, by
# contrast, gets reported as "never considered" when it was cited.
_CITEKEY_TOKEN = re.compile(r"`([A-Za-z][A-Za-z0-9]*(?:[_:-]+[A-Za-z0-9]+)+)`")


# --------------------------------------------------------------------------
# Section anchors
# --------------------------------------------------------------------------


@dataclass
class Section:
    title: str
    level: int
    start: int  # 1-indexed line of the heading itself
    end: int  # 1-indexed last line before the next heading

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


# Headings for *outline extraction*: where does each section start and
# stop, so a revision can Read and Edit one section instead of the whole
# file. src/citation_provenance.py has a similar-looking pair of regexes
# doing a different job -- segmenting claim-bearing blocks for scoring --
# and the two are deliberately not shared: that module needs list items
# and table rows to be blocks, which would be noise in an outline.
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TEX_HEADING = re.compile(r"^\s*\\(chapter|(?:sub){0,2}section|paragraph)\*?\{(.*)$")
_TEX_LEVELS = {
    "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4, "paragraph": 5,
}
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_VERBATIM_BEGIN = re.compile(r"\\begin\{(verbatim|lstlisting|minted|Verbatim)\*?\}")
_VERBATIM_END = re.compile(r"\\end\{(verbatim|lstlisting|minted|Verbatim)\*?\}")


def _braced(text: str) -> str:
    """The contents of a `{...}` group, given everything after the `{`.

    Brace-balanced rather than matched by regex, because both regex
    readings are wrong on titles this project actually produces: a lazy
    `.*?` stops at the first `}` and truncates `\\emph{twin}` mid-word, a
    greedy `.*` runs past the closing brace and swallows a trailing
    `\\label{...}`. A backslash escape is consumed as a pair so that a
    literal `\\{` in a title doesn't open a group.
    """
    depth, out, index = 1, [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(text[index:index + 2])
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        out.append(char)
        index += 1
    return "".join(out)


def sections(text: str) -> list[Section]:
    """The draft's outline: one `Section` per heading, with line ranges.

    Code is skipped first, which is not a nicety. `tutorial.md` in the
    shipped example content is mostly shell and Python, and a `# Step 1`
    comment inside a fenced block is indistinguishable from a Markdown
    heading to anything that doesn't track fences -- so an outline built
    without this reports sections that don't exist and hands a reviser
    line ranges that cut a code block in half.

    Markdown and LaTeX are both recognised, since thesis-chapter-writer
    emits `.tex` and the other four emit `.md`.
    """
    lines = text.splitlines()
    found: list[Section] = []
    in_fence = False
    in_verbatim = False

    for number, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if _VERBATIM_BEGIN.search(line):
            in_verbatim = True
        if _VERBATIM_END.search(line):
            in_verbatim = False
            continue
        if in_fence or in_verbatim:
            continue

        md = _MD_HEADING.match(line)
        if md:
            found.append(Section(md.group(2).strip(), len(md.group(1)), number, number))
            continue
        tex = _TEX_HEADING.match(line)
        if tex:
            found.append(Section(
                _braced(tex.group(2)).strip(),
                _TEX_LEVELS.get(tex.group(1), 2),
                number,
                number,
            ))

    for current, following in zip(found, found[1:]):
        current.end = following.start - 1
    if found:
        found[-1].end = len(lines)
    return found


# --------------------------------------------------------------------------
# Creating a dossier
# --------------------------------------------------------------------------


def _readme(name: str, draft: Path, genre: str) -> str:
    return f"""# Dossier: {name}

The working state that produced `{draft_relpath(draft)}` -- what a later
session needs in order to revise it without re-running the drafting
pipeline. Genre: {genre}.

| File | What it holds |
|---|---|
| `scope.md` | reader, what the draft covers and excludes, glossary, corpus fingerprint |
| `evidence.md` | each citekey kept, why, and the supporting quote or paraphrase |
| `rejected.md` | candidates retrieved and turned down, with the reason |
| `sections.md` | section heading -> the citekeys cited under it |
| `steering.md` | what the user asked for in chat that the draft doesn't show |
| `revisions.md` | append-only log of what changed and why |
| `retrieval.md` | every retrieval call and the size of what it returned |

This directory is gitignored, like the draft it describes. Back it up and
restore it with:

    python3 -m src.dossier export {name}
    python3 -m src.dossier restore <archive.tar.gz> --force

A bundle carries drafts and dossiers, not the corpus: `content/ledger.sqlite`
is regenerable with `python -m src.sync`, and `papers/bibliography.bib` is
your reference manager's export, which belongs in that tool's backup rather
than in a copy this pipeline keeps.

See `docs/DRAFT-ITERATION.md`.
"""


def _scope(draft: Path, genre: str, corpus: tuple[int, str] | None) -> str:
    corpus_line = (
        f"- corpus: {corpus[0]} citekeys, digest `{corpus[1]}`"
        if corpus
        else "- corpus: not recorded (no ledger on this machine when the dossier was created)"
    )
    return f"""# Scope

- genre: {genre}
- draft: {draft_relpath(draft)}
- created: {date.today().isoformat()}
{corpus_line}

## Reader

<!-- One concrete sentence: who is this draft for, and what do they
     already know? Every later revision is judged against this. -->

## Covers

## Does not cover

<!-- Including any sub-theme the corpus turned out too thin to support,
     so a reader can tell an omission from an oversight. -->

## Glossary

<!-- Each recurring term with the one definition the whole draft uses. -->
"""


_EVIDENCE_TEMPLATE = """# Kept evidence

<!-- One block per citekey that survived relevance scoring. A citekey the
     draft cites should appear here; one that was retrieved and turned
     down belongs in rejected.md instead. -->

"""

_REJECTED_TEMPLATE = """# Rejected candidates

<!-- Retrieved, read, and turned down. Recording these is what stops the
     next revision re-searching and re-judging the same papers -- it is
     the single most expensive thing a fresh session repeats. -->

| citekey | query that surfaced it | why rejected |
|---|---|---|
"""

_SECTIONS_TEMPLATE = """# Sections and their citekeys

<!-- Rebuildable from the draft, and worth keeping anyway: a revision can
     see which section owns a citation without reading the draft. -->

| section | citekeys |
|---|---|
"""

_STEERING_TEMPLATE = """# Steering

<!-- What the user asked for in chat that the draft itself doesn't show:
     "don't lead with tooling", "shorter", "drop the adoption angle".
     This is the only part of a drafting session that has nowhere else to
     live. One dated entry per instruction. -->

"""

_REVISIONS_TEMPLATE = """# Revisions

<!-- Append-only, newest last. One entry per revision session: what
     changed, which sections, and why. -->

"""

_RETRIEVAL_TEMPLATE = """# Retrieval calls

<!-- Appended by `python3 -m src.retrieval ... --log <draft>`, never by
     hand.

     `asked` is how much that call requested -- `--k` for search,
     `--windows` for evidence. `chars` is the size of the payload it
     handed back: the thing that then sits in the caller's context for
     the rest of the run. Together with evidence.md's and rejected.md's
     counts, this is what turns "retrieval is where the tokens go" from
     an estimate into a measurement for a particular draft. -->

| date | mode | query | asked | results | chars |
|---|---|---|---|---|---|
"""

_TEMPLATES = {
    "evidence.md": _EVIDENCE_TEMPLATE,
    "rejected.md": _REJECTED_TEMPLATE,
    "sections.md": _SECTIONS_TEMPLATE,
    "steering.md": _STEERING_TEMPLATE,
    "revisions.md": _REVISIONS_TEMPLATE,
    "retrieval.md": _RETRIEVAL_TEMPLATE,
}


def log_retrieval(
    draft: Path, mode: str, query: str, k: int, results: int, chars: int
) -> Path:
    """Append one retrieval call to the dossier's `retrieval.md`.

    Creates the file if the dossier exists but predates it, and creates
    the dossier directory if a skill logged before running `init` --
    losing a measurement because the skeleton wasn't there yet would be a
    silly way to fail, and this writes nothing a later `init` would
    clobber.

    The query is flattened onto one line before it is written. A pipe
    would split the row into extra cells and a newline would split it
    into two rows -- and `retrieval_cost` reads rows positionally, so
    either one turns a logged call into a silently miscounted one rather
    than a visible error. Whitespace is collapsed with `split()`, which
    covers newlines, tabs and carriage returns together.

    **Nothing here ever writes at an offset.** That matters because
    `--log` is a flag on the retrieval CLI and a skill dispatching
    parallel subagents could hand it to all of them, so two processes
    can reach this function at once. The file is opened once, in append
    mode, and the template is written only when that open finds it
    empty -- so both the template and the row go through `O_APPEND` and
    land at whatever the end of the file is *at the time of the write*.
    A writer can therefore never overwrite what another one put there.

    Two earlier shapes could, and both are worth naming because each
    looks correct:

    - `if not path.exists(): path.write_text(TEMPLATE)` truncates, and
      the check goes stale between the two calls.
    - Creating with mode `"x"` fixes that, but publishes an empty file
      and then writes the template to it from offset 0. A second writer
      that appends a row in between has it overwritten.

    What this does *not* promise: that the template is written exactly
    once. Two writers that both find the file empty both write one, so
    the file can carry a duplicate header. That is deliberately the
    failure left in, because it loses nothing -- `retrieval_cost` skips
    any row whose last cell isn't an integer, which both the header and
    its separator are -- and `_count`'s advisory total is one high.
    Buying exactly-once would need a lock or a link-into-place dance,
    for a file whose whole point is to be cheap. See the module
    docstring, and docs/TOKENS.md for why a lock is the wrong instrument
    here.

    Write atomicity is deliberately *not* claimed. Both writes go
    through one buffered handle and may well reach the filesystem as a
    single small write -- but that is an implementation detail of how
    the template's size compares to a buffer, not behaviour to rely on:
    buffered text I/O can flush at points of its own choosing, closing
    may still issue more than one write, and POSIX does not promise that
    a write to a regular file arrives unsplit. Nothing here depends on
    any of that. `retrieval_cost` skips
    any row it cannot parse, so a torn row costs that one measurement
    and leaves every other row intact -- while a row overwritten at an
    offset would have been silently gone. The guarantee this function
    makes is the weaker, sufficient one: no writer addresses a position,
    so no writer can destroy what another wrote.
    """
    target = dossier_dir(draft)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "retrieval.md"
    safe_query = " ".join(query.split()).replace("|", "\\|")
    row = f"| {date.today().isoformat()} | {mode} | {safe_query} | {k} | {results} | {chars} |\n"
    with path.open("a", encoding="utf-8") as handle:
        if not handle.tell():
            handle.write(_RETRIEVAL_TEMPLATE)
        handle.write(row)
    return path


def _retrieval_rows(dossier: Path) -> list[list[str]]:
    """The parseable rows of `retrieval.md`, six cells each.

    An integer `chars` cell is what separates a logged call from the
    template's own header and separator rows, which otherwise parse to
    six cells like any other. Advisory like every other read here: a
    hand-edited row that doesn't parse is skipped rather than raising.
    """
    path = dossier / "retrieval.md"
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Split on unescaped pipes only: `log_retrieval` writes a query
        # containing a pipe as `\|`, which is markdown's literal, and
        # splitting there would cut the row into seven cells.
        cells = [cell.strip() for cell in _ROW_SPLIT.split(line.strip().strip("|"))]
        if len(cells) != 6:
            continue
        try:
            int(cells[5])
        except ValueError:
            continue
        rows.append(cells)
    return rows


def retrieval_cost(dossier: Path) -> tuple[int, int]:
    """(calls, characters returned) recorded in `retrieval.md`."""
    rows = _retrieval_rows(dossier)
    return len(rows), sum(int(row[5]) for row in rows)


def recorded_queries(dossier: Path) -> list[str]:
    """The distinct queries this draft was retrieved with, first seen first.

    `retrieval.md` was written to measure what a run cost, and this is
    the second thing it turns out to be good for: it is the only record
    of *what this draft went looking for*, which is what makes "the
    corpus grew" answerable as "and here is the part of the growth this
    draft would have wanted". Deduplicated because a reformulated search
    logs the same query more than once, and running it twice would just
    report the same candidate twice.
    """
    seen: dict[str, None] = {}
    for cells in _retrieval_rows(dossier):
        # `log_retrieval` escapes a pipe on the way in; unescape it so the
        # query goes to the ranker as the caller actually typed it.
        query = cells[2].replace("\\|", "|").strip()
        if query:
            seen[query] = None
    return list(seen)


def draft_relpath(draft: Path) -> str:
    """`draft` relative to the repo root where possible, for display."""
    try:
        return Path(draft).resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return str(draft)


def init(draft: Path, genre: str) -> list[Path]:
    """Create the dossier skeleton for `draft`. Returns what it wrote.

    Only ever creates missing files, so re-running it on a dossier that
    a skill has since filled in adds whatever is absent and touches
    nothing else. That matters because `init` is the one command a genre
    skill runs before it knows what it will find -- it must not be able
    to destroy the thing it exists to protect.
    """
    target = dossier_dir(draft)
    target.mkdir(parents=True, exist_ok=True)
    corpus_keys = known_citekeys()
    corpus = (len(corpus_keys), digest(corpus_keys)) if corpus_keys is not None else None

    written: list[Path] = []
    contents = {
        "README.md": _readme(draft_name(draft), draft, genre),
        "scope.md": _scope(draft, genre, corpus),
        **_TEMPLATES,
    }
    for name, body in contents.items():
        path = target / name
        if path.exists():
            continue
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


@dataclass
class FileStatus:
    name: str
    present: bool
    entries: int
    shape: str = "prose"


@dataclass
class Status:
    dossier: Path
    draft: Path | None
    files: list[FileStatus] = field(default_factory=list)
    outline: list[Section] = field(default_factory=list)
    recorded: tuple[int, str] | None = None
    current: tuple[int, str] | None = None
    unconsidered: set[str] = field(default_factory=set)
    retrieval_calls: int = 0
    retrieval_chars: int = 0

    @property
    def drifted(self) -> bool:
        return bool(self.recorded and self.current and self.recorded[1] != self.current[1])


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ROW_SPLIT = re.compile(r"(?<!\\)\|")


def _count(text: str, shape: str) -> int:
    """How many entries a dossier file holds. Advisory, not exact.

    Nothing depends on the number being right -- `status` prints it so a
    reader can see at a glance whether a file was filled in or left as
    the skeleton, and a hand-edited dossier that counts a little wrong
    still revises fine.
    """
    body = _COMMENT.sub("", text)
    if shape == "blocks":
        return sum(1 for line in body.splitlines() if line.startswith("## "))
    if shape == "rows":
        return sum(
            1
            for line in body.splitlines()
            if line.lstrip().startswith("|") and not set(line) <= set("|-: \t")
        ) - 1  # the header row, which every template ships with
    return sum(
        1
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _resolve_dossier(draft_or_dossier: Path) -> Path:
    """A dossier directory, given either it or the draft it belongs to."""
    path = Path(draft_or_dossier)
    return path if path.is_dir() else dossier_dir(path)


def status(draft_or_dossier: Path) -> Status:
    """What this dossier holds, and whether the corpus has moved since.

    Never raises on a missing ledger or a missing dossier: this is the
    command someone runs to find out what they have, including on a
    machine where the corpus was never built and only a restored backup
    exists.
    """
    path = Path(draft_or_dossier)
    if path.is_dir():
        dossier, draft = path, find_draft(path)
    else:
        dossier, draft = dossier_dir(path), (path if path.is_file() else None)

    report = Status(dossier=dossier, draft=draft)
    for name, shape in FILES.items():
        file_path = dossier / name
        if file_path.is_file():
            entries = _count(file_path.read_text(encoding="utf-8"), shape)
            report.files.append(FileStatus(name, True, max(entries, 0), shape))
        else:
            report.files.append(FileStatus(name, False, 0, shape))

    if draft is not None:
        report.outline = sections(draft.read_text(encoding="utf-8"))
    report.retrieval_calls, report.retrieval_chars = retrieval_cost(dossier)

    report.recorded = recorded_corpus(dossier)
    corpus_keys = known_citekeys()
    if corpus_keys is not None:
        report.current = (len(corpus_keys), digest(corpus_keys))
        report.unconsidered = corpus_keys - cited_citekeys(dossier)
    return report


# --------------------------------------------------------------------------
# Drift
#
# `status <draft>` answers "did the corpus move under this one draft?".
# This answers the other half: which drafts on this machine have gone
# stale, and what specifically about each. Read-only, lock-free, and
# never fatal -- a missing ledger, a missing dossier file and an
# unparsable row are all things to report, not to fail on.
# --------------------------------------------------------------------------


# How deep to look down each recorded query's ranking. 15 matches
# `survey-writer`'s own `search(sub_theme, k=15)`: the report should
# surface a new paper if and only if the draft's original search would
# have put it in front of the writer, and a different number here would
# quietly mean something else by "would have been considered".
CANDIDATE_K = 15


def _ephemeral_index(rows: list[sqlite3.Row]) -> dict:
    """A BM25 term-frequency index built in memory and thrown away.

    `src.retrieval.search()` cannot be used here, for two reasons that
    are both about this being a *report*. It connects through
    `ledger.connect()`, which mkdirs `content/`, executes the schema and
    runs migrations -- a write connection, which is exactly what
    `_corpus_rows` avoids. And it goes through `retrieval._load_index`,
    which calls `_save_cache` whenever any document's fingerprint moved
    -- which, after the sync that caused the drift being reported, is
    guaranteed. Either one would make an inspection mutate the corpus
    layer it is inspecting.

    The index itself is not the problem, though: `_tokenize_item` and
    `_bm25_scores` are pure, and the only thing that persists in
    `retrieval` is the cache write between them. So this composes the
    same two halves and skips the middle -- seeding from the on-disk
    cache where a fingerprint still matches (`_load_cache` only reads),
    tokenizing the rest into memory, and never writing back. A warm cache
    makes this nearly free; a cold or absent one costs one tokenization
    of the corpus, paid once per scan and dropped when it returns.

    Imported lazily so that `import src.dossier` stays as cheap as the
    rest of the module -- and it stays stdlib-only either way, since
    `src.retrieval` is too.
    """
    from src import retrieval

    cached = retrieval._load_cache()
    index = {}
    for row in rows:
        entry = cached.get(row["citekey"])
        if isinstance(entry, dict) and entry.get("fingerprint") == retrieval._fingerprint(row):
            index[row["citekey"]] = entry
        else:
            index[row["citekey"]] = retrieval._tokenize_item(row)
    return index


class Corpus:
    """The ledger read once, plus the throwaway index built from it.

    Held as one object so that a sweep over every dossier pays for the
    table read and the tokenization once between them all, rather than
    once each. The index is built on first use, so a sweep over dossiers
    that logged no queries never builds one at all.
    """

    def __init__(self, rows: list[sqlite3.Row]):
        self.rows = rows
        self.citekeys = {row["citekey"] for row in rows}
        self.titles = {row["citekey"]: row["title"] or "" for row in rows}
        self._index: dict | None = None

    @property
    def index(self) -> dict:
        if self._index is None:
            self._index = _ephemeral_index(self.rows)
        return self._index

    def matches(self, queries: list[str], k: int = CANDIDATE_K) -> dict[str, list[str]]:
        """citekey -> the recorded queries whose top-k it would land in."""
        from src import retrieval

        hits: dict[str, list[str]] = {}
        for query in queries:
            terms = retrieval._tokenize(query)
            if not terms:
                continue
            scores = retrieval._bm25_scores(self.index, terms)
            # Ties broken by citekey so that two runs over an unchanged
            # corpus report the same candidates in the same order.
            ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
            for citekey, _score in ranked:
                hits.setdefault(citekey, []).append(query)
        return hits


@dataclass
class Candidate:
    """A paper in the ledger that this dossier has never weighed, which
    one of the dossier's own recorded queries would have surfaced."""
    citekey: str
    title: str
    queries: list[str]


@dataclass
class Reconsider:
    """A paper this draft read and declined, which its queries still
    reach -- carried with the reason it was declined."""
    citekey: str
    title: str
    queries: list[str]
    reason: str


@dataclass
class Drift:
    dossier: Path
    name: str
    draft: Path | None
    corpus_available: bool = False
    recorded: tuple[int, str] | None = None
    current: tuple[int, str] | None = None
    missing: dict[str, list[str]] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    reconsider: list[Reconsider] = field(default_factory=list)
    unconsidered: int = 0

    @property
    def drifted(self) -> bool:
        return bool(self.recorded and self.current and self.recorded[1] != self.current[1])

    @property
    def clean(self) -> bool:
        # `reconsider` is deliberately not part of this. A rejection that
        # still matches its query was true before the corpus moved and
        # will be true on every sweep after it -- counting it as drift
        # would mark every dossier that ever declined a paper permanently
        # stale, which is exactly the signal this command exists to give.
        return not self.missing and not self.candidates

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "dossier": draft_relpath(self.dossier),
            "draft": draft_relpath(self.draft) if self.draft else None,
            "corpus_available": self.corpus_available,
            "recorded": list(self.recorded) if self.recorded else None,
            "current": list(self.current) if self.current else None,
            "drifted": self.drifted,
            "missing": self.missing,
            "candidates": [
                {"citekey": c.citekey, "title": c.title, "queries": c.queries}
                for c in self.candidates
            ],
            "reconsider": [
                {"citekey": r.citekey, "title": r.title,
                 "queries": r.queries, "reason": r.reason}
                for r in self.reconsider
            ],
            "unconsidered": self.unconsidered,
        }


def dossier_name(dossier: Path) -> str:
    """A dossier's path under `content/dossiers/` -- what `list` prints."""
    try:
        return dossier.resolve().relative_to(config.DOSSIERS_DIR.resolve()).as_posix()
    except ValueError:
        return dossier.name


def drift(dossier: Path, corpus: "Corpus | None" = None) -> Drift:
    """What has gone stale about one dossier since its draft was written.

    Two findings, and they are not the same kind of thing. A **missing**
    citekey is a defect: the draft cites a paper the corpus no longer
    has, and something has to be swapped or dropped. A **candidate** is
    an opportunity: a paper the corpus has gained that this draft's own
    recorded queries would have put in front of the writer. The first is
    work; the second is a decision, and drift is still not itself a
    reason to redraft.

    Pass `corpus` to share one ledger read and one index across a sweep;
    omit it and this reads the ledger for itself.
    """
    dossier = Path(dossier)
    if corpus is None:
        rows = _corpus_rows()
        corpus = Corpus(rows) if rows is not None else None

    report = Drift(
        dossier=dossier,
        name=dossier_name(dossier),
        draft=find_draft(dossier),
        recorded=recorded_corpus(dossier),
    )
    if corpus is None:
        return report

    report.corpus_available = True
    report.current = (len(corpus.citekeys), digest(corpus.citekeys))

    sections_citing = section_citekeys(dossier)
    cited = _citekeys_in(dossier, CITED_FILES)
    report.missing = {
        citekey: sections_citing.get(citekey, [])
        for citekey in sorted(cited - corpus.citekeys)
    }

    # Everything the dossier ever weighed -- rejections included, which is
    # the point. Re-offering a paper the draft already turned down as if
    # it were new would cost exactly the re-judging that `rejected.md`
    # exists to prevent.
    mentioned = cited_citekeys(dossier)
    report.unconsidered = len(corpus.citekeys - mentioned)

    declined = rejected_reasons(dossier)
    matched = sorted(corpus.matches(recorded_queries(dossier)).items())
    report.candidates = [
        Candidate(citekey, corpus.titles.get(citekey, ""), queries)
        for citekey, queries in matched
        if citekey not in mentioned
    ]
    # A declined paper its queries still reach, reported separately and
    # with the reason. `cited` wins the tie: a citekey that is both cited
    # and listed as rejected is a stale `rejected.md` row, not an open
    # question, and offering it back would send a reviser to re-decide
    # something the draft already acts on.
    report.reconsider = [
        Reconsider(citekey, corpus.titles.get(citekey, ""), queries, declined[citekey])
        for citekey, queries in matched
        if citekey in declined and citekey not in cited
    ]
    return report


def drift_all() -> list[Drift]:
    """One drift report per dossier on this machine, nearest-first."""
    rows = _corpus_rows()
    corpus = Corpus(rows) if rows is not None else None
    return [drift(path, corpus) for path in all_dossiers()]


# --------------------------------------------------------------------------
# Backup and restore
# --------------------------------------------------------------------------


def _matches(relative: PurePosixPath, names: list[str]) -> bool:
    if not names:
        return True
    text = relative.as_posix()
    stem = relative.with_suffix("").as_posix()
    return any(
        text == name or stem == name or text.startswith(f"{name}/") for name in names
    )


def bundle_members(names: list[str], with_rendered: bool) -> list[tuple[Path, str]]:
    """(file on disk, name inside the archive) for everything to back up.

    Archive names are relative to `content/`, not to the repo root, so a
    bundle restores correctly into a checkout whose `[content].dir`
    points somewhere else.
    """
    roots = [("drafts", config.DRAFTS_DIR), ("dossiers", config.DOSSIERS_DIR)]
    if with_rendered:
        roots.append(("rendered", config.RENDERED_DIR))

    members: list[tuple[Path, str]] = []
    for label, root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = PurePosixPath(path.relative_to(root).as_posix())
            # A dossier lives one directory deeper than its draft, so
            # match its parent: `dossiers/topic/survey/scope.md` belongs
            # to the draft named `topic/survey`.
            match_against = relative.parent if label == "dossiers" else relative
            if _matches(match_against, names):
                members.append((path, f"{label}/{relative.as_posix()}"))
    return members


def export(names: list[str], out: Path, with_rendered: bool = False) -> tuple[Path, int]:
    """Write a gzipped tar of the named drafts and their dossiers."""
    members = bundle_members(names, with_rendered)
    if not members:
        raise DossierError(
            "Nothing to export"
            + (f" matching {', '.join(names)}" if names else f" under {config.CONTENT_DIR}")
            + "."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as archive:
        for path, name in members:
            archive.add(path, arcname=name)
    return out, len(members)


def _checked_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Every member, having refused the whole archive if any is unsafe.

    Refusing wholesale rather than skipping the bad member: a partially
    extracted backup is worse than none, because it looks like it worked.
    `extractall(filter="data")` below repeats the traversal checks -- this
    is not redundant, it is the layer that can say *which* member was
    wrong and that only the three directories this module owns are
    writable.
    """
    checked: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        if not (member.isfile() or member.isdir()):
            raise DossierError(
                f"{member.name!r} is not a regular file or directory "
                "(a link or device node). Refusing the whole archive."
            )
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts:
            raise DossierError(
                f"{member.name!r} escapes the extraction directory. "
                "Refusing the whole archive."
            )
        if not name.parts or name.parts[0] not in ARCHIVE_ROOTS:
            raise DossierError(
                f"{member.name!r} is not under {'/, '.join(ARCHIVE_ROOTS)}/. "
                "Refusing the whole archive."
            )
        checked.append(member)
    return checked


@dataclass
class RestorePlan:
    archive: Path
    new: list[Path] = field(default_factory=list)
    overwrite: list[Path] = field(default_factory=list)
    performed: bool = False


def restore(archive: Path, force: bool = False) -> RestorePlan:
    """Unpack a bundle under `content/`. A dry run unless `force`.

    Reporting first is the default because restoring is the only
    destructive thing in this module, and the case it exists for --
    "I need last month's draft back" -- is exactly the case where the
    working copy might be something you'd rather not lose to a
    mistyped archive name.
    """
    plan = RestorePlan(archive=archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = _checked_members(tar)
        for member in members:
            if not member.isfile():
                continue
            target = config.CONTENT_DIR / member.name
            (plan.overwrite if target.exists() else plan.new).append(target)
        if force:
            config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
            tar.extractall(config.CONTENT_DIR, members=members, filter="data")
            plan.performed = True
    return plan


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    draft = Path(args.draft)
    written = init(draft, args.genre)
    target = dossier_dir(draft)
    if not written:
        print(f"Dossier already complete: {draft_relpath(target)}")
        return 0
    print(f"Dossier: {draft_relpath(target)}")
    for path in written:
        print(f"  created {path.name}")
    if known_citekeys() is None:
        print(f"\n  No ledger at {config.LEDGER_PATH}, so no corpus fingerprint was")
        print("  recorded. Drift checks will be unavailable for this dossier.")
    return 0


# How many findings of one kind to print before summarising the rest.
# A drift report is read to decide what to do next, not as a manifest;
# what it must never do is truncate silently, so the remainder is always
# counted out loud.
_SHOWN = 10


def _print_drift(report: Drift) -> None:
    marker = "" if report.draft else "   (draft missing)"
    if not report.corpus_available:
        print(f"  {report.name}{marker}\n    drift unavailable -- no readable ledger.")
        return
    if report.clean:
        moved = " (corpus moved, nothing this dossier relies on)" if report.drifted else ""
        print(f"  {report.name}{marker}\n    no drift{moved}.")
        return

    print(f"  {report.name}{marker}")
    if report.missing:
        print(f"    {len(report.missing)} cited citekey(s) no longer in the ledger:")
        for citekey, in_sections in list(report.missing.items())[:_SHOWN]:
            where = f"  cited in: {', '.join(in_sections)}" if in_sections else ""
            print(f"      {citekey}{where}")
        if len(report.missing) > _SHOWN:
            print(f"      ... and {len(report.missing) - _SHOWN} more")
    if report.candidates:
        print(f"    {len(report.candidates)} new candidate(s) matching this "
              "dossier's recorded queries:")
        for candidate in report.candidates[:_SHOWN]:
            title = f"  {candidate.title}" if candidate.title else ""
            print(f"      {candidate.citekey}{title}")
            print(f"        surfaced by: {'; '.join(candidate.queries)}")
        if len(report.candidates) > _SHOWN:
            print(f"      ... and {len(report.candidates) - _SHOWN} more")
    # Only alongside a real finding. On its own this is true on every
    # sweep forever, so printing it unconditionally would bury the drift
    # it is meant to help act on.
    if report.reconsider:
        print(f"    {len(report.reconsider)} previously rejected paper(s) these "
              "queries still reach:")
        for entry in report.reconsider[:_SHOWN]:
            title = f"  {entry.title}" if entry.title else ""
            print(f"      {entry.citekey}{title}")
            print(f"        rejected because: {entry.reason}")
        if len(report.reconsider) > _SHOWN:
            print(f"      ... and {len(report.reconsider) - _SHOWN} more")


def _cmd_status_all(reports: list[Drift], as_json: bool) -> int:
    if as_json:
        print(json.dumps({"dossiers": [r.as_dict() for r in reports]}, indent=2))
        return 0
    if not reports:
        print(f"No dossiers under {draft_relpath(config.DOSSIERS_DIR)}.")
        return 0

    print(f"Corpus drift across {len(reports)} dossier(s):\n")
    for report in reports:
        _print_drift(report)
    stale = [r for r in reports if not r.clean]
    # A dossier with no readable ledger has no findings, which is not the
    # same as having none to find. Reporting it as current would be the
    # one way this command could actively mislead: "nothing to do here"
    # asserted about a check that never ran.
    unknown = [r for r in reports if not r.corpus_available]
    print()
    if unknown:
        print(f"  {len(unknown)} of {len(reports)} dossier(s) could not be checked: "
              f"no readable ledger at {config.LEDGER_PATH}.")
        print("  Run `python -m src.sync` to build one; until then drift is unknown,")
        print("  not absent.")
    if not stale:
        if not unknown:
            print("  Every dossier is current against the corpus.")
        return 0
    print(f"  {len(stale)} of {len(reports)} dossier(s) have drifted.")
    print("  A missing citekey is a defect: the draft cites what the corpus no")
    print("  longer has. A candidate is a decision, not a defect -- re-search only")
    print("  if the change you are making touches a sub-theme it could bear on.")
    if any(r.reconsider for r in stale):
        print("  Candidates exclude everything in `rejected.md`; the reconsider list is")
        print("  the exception, shown with its reason so you can judge whether it holds.")
    else:
        print("  `rejected.md` was already subtracted, so nothing here was turned "
              "down before.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if args.all and args.draft:
        print("[error] Give a draft path or --all, not both.", file=sys.stderr)
        return 2
    if not args.all and not args.draft:
        print("[error] Give a draft path, or --all for every dossier.", file=sys.stderr)
        return 2
    if args.all:
        return _cmd_status_all(drift_all(), args.json)
    if args.json:
        return _cmd_status_all([drift(_resolve_dossier(Path(args.draft)))], True)

    report = status(Path(args.draft))
    if not report.dossier.is_dir():
        print(f"No dossier at {draft_relpath(report.dossier)}.")
        print(f"Create one with `python3 -m src.dossier init {args.draft} --genre <genre>`.")
        return 1

    print(f"Dossier: {draft_relpath(report.dossier)}")
    if report.draft is not None:
        print(f"  draft         {draft_relpath(report.draft)} ({len(report.outline)} sections)")
    else:
        print("  draft         MISSING -- the dossier outlived its draft")
    for entry in report.files:
        # A count means something for the two files that are lists
        # (evidence blocks, rejected/section rows) and nothing for the
        # three that are prose -- "scope.md: 40 entries" would be a
        # number dressed up as information.
        if not entry.present:
            print(f"  {entry.name:<14}absent")
        elif not entry.entries:
            print(f"  {entry.name:<14}empty (skeleton only)")
        elif entry.shape == "prose":
            print(f"  {entry.name:<14}filled in")
        else:
            print(f"  {entry.name:<14}{entry.entries} entr{'y' if entry.entries == 1 else 'ies'}")

    if report.retrieval_calls:
        kept = next((f.entries for f in report.files if f.name == "evidence.md"), 0)
        rejected = next((f.entries for f in report.files if f.name == "rejected.md"), 0)
        print(f"\nRetrieval: {report.retrieval_calls} call(s) returned "
              f"{report.retrieval_chars:,} characters")
        if kept or rejected:
            print(f"  {kept} kept, {rejected} rejected")
        else:
            # Searched, and recorded nothing it found. Reported rather
            # than blocked, like every other check outside the citation
            # gate: it costs a comparison of two numbers already on this
            # report, and nothing else in the pipeline can see it -- the
            # draft looks finished and the judgment behind it is gone.
            #
            # "no entries" rather than "empty": both counts are 0 for an
            # absent file too, and the per-file lines above already
            # distinguish `absent` from `empty (skeleton only)`. Calling
            # a missing file empty would contradict them.
            print("  but evidence.md and rejected.md hold no entries -- this run")
            print("  searched and recorded nothing it found, so a revision will")
            print("  have to re-retrieve and re-judge the same candidates.")

    print()
    if report.current is None:
        print(f"Corpus drift: unavailable -- no readable ledger at {config.LEDGER_PATH}.")
        return 0
    if report.recorded is None:
        print("Corpus drift: unavailable -- scope.md records no corpus fingerprint.")
        print(f"  now: {report.current[0]} citekeys, digest {report.current[1]}")
        return 0

    print("Corpus drift since this draft:")
    print(f"  recorded  {report.recorded[0]} citekeys, digest {report.recorded[1]}")
    print(f"  now       {report.current[0]} citekeys, digest {report.current[1]}")
    if not report.drifted:
        print("  unchanged -- the dossier's evidence is current.")
        return 0
    print(f"  CHANGED ({report.current[0] - report.recorded[0]:+d} citekeys)")
    if report.unconsidered:
        shown = sorted(report.unconsidered)[:10]
        print(f"\n  {len(report.unconsidered)} citekey(s) in the ledger appear nowhere in "
              "this dossier:")
        for citekey in shown:
            print(f"    {citekey}")
        if len(report.unconsidered) > len(shown):
            print(f"    ... and {len(report.unconsidered) - len(shown)} more")
        print("\n  Re-search only if the change you are making touches a sub-theme")
        print("  these could bear on. Drift is not itself a reason to redraft.")
    return 0


def _cmd_sections(args: argparse.Namespace) -> int:
    draft = Path(args.draft)
    if not draft.is_file():
        print(f"No such draft: {draft}", file=sys.stderr)
        return 1
    outline = sections(draft.read_text(encoding="utf-8"))
    if not outline:
        print(f"No headings in {draft_relpath(draft)}.")
        return 0
    print(f"{draft_relpath(draft)}")
    for section in outline:
        indent = "  " * (section.level - 1)
        span = f"{section.start}-{section.end}"
        print(f"  {span:>12}  ({section.lines:>4} lines)  {indent}{section.title}")
    print("\n  Read one section with offset=<start>, limit=<lines>; edit inside that")
    print("  range rather than rewriting the file.")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    found = all_dossiers()
    if not found:
        print(f"No dossiers under {config.DOSSIERS_DIR}.")
        return 0
    for dossier in found:
        draft = find_draft(dossier)
        name = dossier.resolve().relative_to(config.DOSSIERS_DIR.resolve()).as_posix()
        marker = "" if draft else "   (draft missing)"
        print(f"  {name}{marker}")
    print(f"\n  {len(found)} dossier(s) under {draft_relpath(config.DOSSIERS_DIR)}.")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    if args.out:
        out = Path(args.out)
    else:
        label = "-".join(name.replace("/", "-") for name in args.names) or "all"
        out = Path(f"drafts-{label}-{date.today().isoformat()}.tar.gz")
    try:
        written, count = export(args.names, out, args.with_rendered)
    except DossierError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    size = written.stat().st_size
    print(f"  {written}  ({count} file(s), {size / 1024:.1f} KiB)")
    print("\n  Restore with:")
    print(f"    python3 -m src.dossier restore {written} --force")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    if not archive.is_file():
        print(f"No such archive: {archive}", file=sys.stderr)
        return 1
    try:
        plan = restore(archive, args.force)
    except (DossierError, tarfile.TarError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    verb = "Restored" if plan.performed else "Would restore"
    print(f"{verb} into {draft_relpath(config.CONTENT_DIR)}:")
    print(f"  {len(plan.new)} new file(s)")
    print(f"  {len(plan.overwrite)} existing file(s) {'overwritten' if plan.performed else 'would be OVERWRITTEN'}")
    for path in plan.overwrite[:10]:
        print(f"    {draft_relpath(path)}")
    if len(plan.overwrite) > 10:
        print(f"    ... and {len(plan.overwrite) - 10} more")
    if not plan.performed:
        print("\n  Dry run. Re-run with --force to write:")
        print(f"    python3 -m src.dossier restore {archive} --force")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.dossier",
        description="The working state behind a draft: create it, inspect it, "
                    "back it up, restore it. Stdlib only; never writes to the "
                    "corpus layer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a dossier skeleton for a draft")
    p_init.add_argument("draft", help="Path to the draft under content/drafts/")
    p_init.add_argument("--genre", required=True,
                        help="survey, thesis-chapter, textbook-chapter, tutorial, deep-research")
    p_init.set_defaults(func=_cmd_init)

    p_status = sub.add_parser("status", help="What a dossier holds, and corpus drift since")
    p_status.add_argument("draft", nargs="?",
                          help="Draft path, or the dossier directory itself")
    p_status.add_argument("--all", action="store_true",
                          help="One drift report over every dossier instead")
    p_status.add_argument("--json", action="store_true",
                          help="Machine-readable drift report (for draft-reviser)")
    p_status.set_defaults(func=_cmd_status)

    p_sections = sub.add_parser(
        "sections", help="Heading -> line range, for reading and editing one section")
    p_sections.add_argument("draft", help="Path to the draft")
    p_sections.set_defaults(func=_cmd_sections)

    p_list = sub.add_parser("list", help="Every dossier on this machine")
    p_list.set_defaults(func=_cmd_list)

    p_export = sub.add_parser("export", help="Back up drafts and dossiers to a tar.gz")
    p_export.add_argument("names", nargs="*",
                          help="Draft names to include (default: everything)")
    p_export.add_argument("--out", help="Archive path (default: drafts-<name>-<date>.tar.gz)")
    p_export.add_argument("--with-rendered", action="store_true",
                          help="Include content/rendered/ (large: PDFs)")
    p_export.set_defaults(func=_cmd_export)

    p_restore = sub.add_parser("restore", help="Unpack a bundle (dry run unless --force)")
    p_restore.add_argument("archive", help="Path to a tar.gz written by `export`")
    p_restore.add_argument("--force", action="store_true",
                           help="Actually write, overwriting what is already there")
    p_restore.set_defaults(func=_cmd_restore)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DossierError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

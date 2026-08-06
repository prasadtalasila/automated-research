"""Citation provenance report: for each citation in a draft, what in the
cited source supports it, and where.

`citation_gate` answers "is this citekey real?" -- exactly, as a hard
gate. This answers the question that comes next and can't be answered
exactly: *does the cited paper actually say this?* A claim that drifted
away from its source during drafting passes the gate cleanly, because
the citekey is real; only reading the source catches it.

Deliberately a review aid, not a gate -- the same position as
src/citation_coverage.py and scripts/verbatim_check.py, and for a
concrete reason. Matching is lexical, so it cannot separate "the source
doesn't say this" from "the source says it in words I didn't recognise".
A check that blocked on that distinction would train people to work
around it, which is exactly the corrosion citation_gate avoids by only
ever asserting something it can verify exactly.

Passages come from `src/passages.py`, which owns the sidecar -> pages ->
`pdftotext` ladder and the rule that a source with no reading order
reports a page rather than a quotation. This module scores claims
against whatever that ladder hands back; it no longer decides where the
text comes from.

Stdlib only (sqlite3/re), like citation_gate.py and references.py --
runs with bare `python3`, no venv.

Usage:
    python -m src.citation_provenance content/drafts/<slug>.md
    python -m src.citation_provenance <draft.md> --formats md,tex,pdf
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src import citation_gate, config, ledger
from src.passages import Passage, distinctive, source_passages


@dataclass
class Finding:
    line: int
    citekey: str
    claim: str
    score: float
    passage: Passage | None = None
    note: str | None = None


@dataclass
class Report:
    draft: str
    findings: list[Finding] = field(default_factory=list)
    unreadable: dict[str, str] = field(default_factory=dict)


def _paragraph_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    """(first line, last line, joined text) per blank-line-separated block."""
    spans, start, buf = [], None, []
    for index, line in enumerate(lines, 1):
        if line.strip():
            if start is None:
                start = index
            buf.append(line.strip())
        elif start is not None:
            spans.append((start, index - 1, " ".join(buf)))
            start, buf = None, []
    if start is not None:
        spans.append((start, len(lines), " ".join(buf)))
    return spans


# Markdown block openers. A table row is a block by itself (a row cannot
# span lines); a list item or a heading opens one that runs until the next
# opener or the end of the paragraph, so a hard-wrapped bullet stays whole.
_TABLE_ROW = re.compile(r"^\s*\|")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_OPENS_BLOCK = re.compile(r"^\s*(?:\||[-*+]\s|\d+[.)]\s|#{1,6}\s)")
# A row and a heading are complete in one line: whatever follows starts
# its own block, so prose under a heading is not glued to the heading.
_STANDS_ALONE = re.compile(r"^\s*(?:\||#{1,6}\s)")
# A cell of the |---|:--:|---| separator row: alignment, not content.
_SEPARATOR_CELL = re.compile(r":?-{2,}:?")
# Split a row on its unescaped pipes only -- `\|` is markdown's way of
# putting a literal pipe inside a cell, and splitting there would cut a
# cell in half.
_ROW_SPLIT = re.compile(r"(?<!\\)\|")

# The same two shapes in LaTeX, since every genre skill exports .tex and
# .pdf alongside the Markdown. Two differences from Markdown: a tabular
# row ends at `\\` rather than at a newline, so one row can span several
# hard-wrapped lines; and the environment and rule commands are structure
# that would otherwise be scored as though `tabular` and `toprule` were
# words the cited paper ought to contain.
_TEX_ITEM = re.compile(r"^\s*\\item\b\s*")
_TEX_ROW_END = re.compile(r"\\\\\s*$")
_TEX_HEADING = re.compile(r"^\s*\\(?:chapter|(?:sub){0,2}section|paragraph)\*?\{")
_TEX_STRUCTURE = re.compile(
    r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\]|\{[^}]*\})*"
    r"|\\(?:top|mid|bottom)rule|\\hline|\\cline\{[^}]*\}"
)
_TEX_CELL_SPLIT = re.compile(r"(?<!\\)&")


def _cells_prose(cells: list[str]) -> str:
    """Table cells as something quotable: joined with " -- ".

    Cells are phrases, not sentences, and a row's own delimiters would
    otherwise reach the report inside a blockquote -- where pandoc renders
    every `|` as `\\textbar{}`, so a cited table arrived as a wall of
    escapes.
    """
    stripped = (cell.strip() for cell in cells)
    return " -- ".join(c for c in stripped if c and not _SEPARATOR_CELL.fullmatch(c))


def _row_prose(row: str) -> str:
    """A markdown table row, flattened."""
    return _cells_prose(_ROW_SPLIT.split(row.strip().strip("|")))


def _tex_row_prose(text: str) -> str:
    """A LaTeX row or environment fragment, flattened.

    Structure first, then cells: dropping `\\begin{tabular}{lll}` before
    splitting keeps its `{lll}` column spec out of the first cell.
    """
    without_structure = _TEX_ROW_END.sub("", _TEX_STRUCTURE.sub(" ", text)).strip()
    # `\begin{itemize} \item ...` on one line reaches here rather than the
    # marker branch, and "item" is not a word the source has to contain.
    without_marker = _TEX_ITEM.sub("", without_structure, count=1)
    return _cells_prose(_TEX_CELL_SPLIT.split(without_marker))


def _claim_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    """(first line, last line, text) per *block*, subdividing paragraphs.

    A paragraph of prose comes back as one span, exactly as before. A
    paragraph that is a table or a list comes back as one span per row or
    item, because those carry no sentence boundary for `_sentence_around`
    to find and so would otherwise be quoted and scored whole.
    """
    spans = []
    for start, end, _ in _paragraph_spans(lines):
        block: list[str] = []
        block_start = start
        for index in range(start, end + 1):
            line = lines[index - 1]
            if block and (_OPENS_BLOCK.match(line) or _TEX_ITEM.match(line)):
                spans.append((block_start, index - 1, _block_text(block)))
                block, block_start = [], index
            block.append(line)
            # A markdown row or heading ends with its line; a LaTeX row
            # ends at `\\`, wherever in the block that falls; a sectioning
            # command is a heading either way.
            if _STANDS_ALONE.match(line) or _TEX_ROW_END.search(line) or _TEX_HEADING.match(line):
                spans.append((block_start, index, _block_text(block)))
                block, block_start = [], index + 1
        if block:
            spans.append((block_start, end, _block_text(block)))
    return spans


def _block_text(block: list[str]) -> str:
    """The block's text as prose: a row flattened, a marker dropped."""
    if _TABLE_ROW.match(block[0]):
        return _row_prose(block[0])
    joined = " ".join(line.strip() for line in block)
    if _TEX_STRUCTURE.search(joined) or _TEX_ROW_END.search(joined):
        return _tex_row_prose(joined)
    for marker in (_LIST_ITEM, _TEX_ITEM, _HEADING):
        if marker.match(block[0]):
            return marker.sub("", joined, count=1)
    return joined


def claims(draft_text: str) -> list[tuple[int, str, str]]:
    """(line number, citekey, the sentence carrying it) for every citation.

    The sentence is reconstructed from the whole *paragraph*, not from the
    single line the citekey sits on. Every draft this project produces is
    hard-wrapped, so a sentence routinely spans three or four lines and
    the citation lands on whichever one happens to hold it -- reading just
    that line yields fragments like "." or ", or equivalently as
    combinations of", which score against nothing and make the report
    worthless precisely where it is meant to be used.

    Still the sentence rather than the whole paragraph, though: a
    paragraph citing three papers would otherwise be scored identically
    against all three, which tells a reviewer nothing about which
    citation is the weak one.

    "Paragraph" means a *block*, not everything between two blank lines.
    A table or a list is one blank-line block containing no sentence
    boundary at all, so reading it whole quoted the entire table back as
    the claim -- once per citekey in it -- and scored each row against the
    whole table's vocabulary, which is the same identical-claims failure
    one level up. `_claim_spans` splits those into rows and items, in
    Markdown and in LaTeX -- every genre skill exports `.tex` and `.pdf`
    beside the `.md`, so both syntaxes reach this module. Ordinary prose
    is unaffected: it is one block, read exactly as before.
    """
    lines = draft_text.splitlines()
    spans = _claim_spans(lines)
    out = []
    for line_no, citekey in citation_gate.extract_citekeys(draft_text):
        block = next(
            (text for start, end, text in spans if start <= line_no <= end),
            lines[line_no - 1] if 0 < line_no <= len(lines) else "",
        )
        out.append((line_no, citekey, _sentence_around(block, citekey)))
    return out


_CITE_MARKUP = re.compile(r"\[@[^\]]+\]|\\cite[tp]?\{[^}]*\}")


def _sentence_around(text: str, citekey: str) -> str:
    """The sentence within `text` containing `citekey`, citation markup
    stripped so the markers themselves don't score as content.

    Sentence splitting avoids breaking on the abbreviations these drafts
    actually contain -- "Fig. 1", "e.g.", "Sect. 1.2" -- since splitting
    there would reintroduce the fragment problem one level down.
    """
    for part in _SENTENCE_SPLIT.split(text.strip()):
        if citekey in part:
            return _tidy(part)
    return _tidy(text)


def _tidy(text: str) -> str:
    """Drop citation markup and close the gap it leaves behind.

    Removing `[@key]` from "processes [@key], or equivalently" otherwise
    leaves "processes , or equivalently" -- a space before the comma and
    a double space where the marker was. Small, but this text is quoted
    back to a reviewer, and the artefacts read as sloppiness in the
    *draft* rather than in this tool.
    """
    stripped = _CITE_MARKUP.sub("", text)
    stripped = re.sub(r"\s+([.,;:!?)])", r"\1", stripped)
    stripped = re.sub(r"\(\s+", "(", stripped)
    return re.sub(r"\s{2,}", " ", stripped).strip(" ,;:")


# Split after . ! ? only when followed by whitespace and a capital or an
# opening bracket, and not when the preceding token is a known
# abbreviation or a single initial.
_SENTENCE_SPLIT = re.compile(
    r"(?<![A-Z])(?<!\bFig)(?<!\bSect)(?<!\bEq)(?<!\bRef)(?<!\be\.g)(?<!\bi\.e)(?<!\bcf)"
    r"(?<=[.!?])\s+(?=[A-Z\[(])"
)


def score_claim(claim: str, passages: list[Passage]) -> tuple[float, Passage | None]:
    """Best lexical-overlap score over `passages`, and the passage that
    achieved it.

    Overlap rather than verbatim n-grams: a correct paraphrase keeps most
    of its content words while changing order and function words, so it
    scores well here and scores *zero* under the >=8-word exact runs that
    scripts/verbatim_check.py's `overlap` mode uses. That mode is looking
    for borrowed wording; this one is looking for support, and paraphrase
    is the normal case rather than the exception.
    """
    wanted = distinctive(claim)
    if not wanted or not passages:
        return 0.0, None
    best_score, best = 0.0, None
    for passage in passages:
        hits = len(wanted & passage.words)
        score = hits / len(wanted)
        if score > best_score:
            best_score, best = score, passage
    return best_score, best


def build_report(draft_path: Path) -> Report:
    text = draft_path.read_text(encoding="utf-8")
    report = Report(draft=draft_path.name)
    con = ledger.connect()
    try:
        cache: dict[str, list[Passage]] = {}
        for line_no, citekey, claim in claims(text):
            if citekey not in cache:
                passages, reason = source_passages(con, citekey)
                cache[citekey] = passages
                if reason:
                    report.unreadable[citekey] = reason
            score, passage = score_claim(claim, cache[citekey])
            note = report.unreadable.get(citekey)
            report.findings.append(
                Finding(line=line_no, citekey=citekey, claim=claim,
                        score=score, passage=passage, note=note)
            )
    finally:
        con.close()
    # Worst first: the report should open on what deserves attention,
    # not make a reviewer read forty entries to find three.
    report.findings.sort(key=lambda f: (f.score, f.line))
    return report


def _band(score: float) -> str:
    if score < config.PROVENANCE_WEAK_SCORE:
        return "no support found"
    if score < config.PROVENANCE_GOOD_SCORE:
        return "weak"
    return "supported"


def render_markdown(report: Report) -> str:
    weak = config.PROVENANCE_WEAK_SCORE
    good = config.PROVENANCE_GOOD_SCORE
    lines = [
        f"# Citation provenance: {report.draft}",
        "",
        f"Generated {date.today().isoformat()} by `python -m src.citation_provenance`.",
        "",
        "## How to read this",
        "",
        "Each entry pairs a citing sentence from the draft with the passage of",
        "the cited paper that best matches it, scored by how many of the",
        "sentence's distinctive words appear there. Entries are ordered",
        "**worst match first**, so the ones worth checking come first.",
        "",
        "This is a **review aid, not a gate**. A low score means *go look* --",
        "it does not mean the citation is wrong. A claim correctly paraphrased",
        "into different vocabulary scores low, and a claim that happens to",
        "share wording with its source scores high while misrepresenting it.",
        "The report tells you where to spend attention; it does not adjudicate.",
        "",
        f"Bands: **no support found** below {weak:.0%}, **weak** below "
        f"{good:.0%}, **supported** at or above {good:.0%}.",
        "",
        "**Scores are comparable within a source kind, not across them.** A",
        "quoted paragraph is a much smaller haystack than a whole page, so",
        "the same quality of support scores lower against a paragraph than",
        "against a page. On one real draft the identical citations banded as",
        "8 weak / 5 supported page-level and 12 weak / 1 supported once",
        "paragraphs were available -- the matches did not get worse, the",
        "denominator got smaller. Compare entries with each other, and treat",
        "the band as a rough reading order rather than a measurement.",
        "",
    ]

    if not report.findings:
        lines += ["No citations found in this draft.", ""]
        return "\n".join(lines)

    counts: dict[str, int] = {}
    for finding in report.findings:
        counts[_band(finding.score)] = counts.get(_band(finding.score), 0) + 1
    lines += ["## Summary", ""]
    for band in ("no support found", "weak", "supported"):
        if counts.get(band):
            lines.append(f"- {counts[band]} {band}")
    lines.append("")

    if report.unreadable:
        lines += ["## Sources that could not be read", ""]
        for citekey, reason in sorted(report.unreadable.items()):
            lines.append(f"- `{citekey}`: {reason}")
        lines += ["", "Findings for these show a score of 0 because there was "
                      "nothing to compare against, not because the claim is "
                      "unsupported.", ""]

    lines += ["## Findings", ""]
    current = None
    for finding in report.findings:
        band = _band(finding.score)
        if band != current:
            lines += [f"### {band.capitalize()}", ""]
            current = band
        lines += [
            f"#### Line {finding.line} -- `[@{finding.citekey}]` "
            f"({finding.score:.0%} match)",
            "",
            f"> {finding.claim}" if finding.claim else "> (no sentence text)",
            "",
        ]
        if finding.note:
            lines += [f"*Source unavailable: {finding.note}*", ""]
        elif finding.passage is None:
            lines += ["*No passage in the source matched any distinctive word "
                      "from this sentence.*", ""]
        elif finding.passage.quotable:
            page = f", p.{finding.passage.page}" if finding.passage.page else ""
            lines += [f"Best match in the source{page}:", ""]
            lines += [f"> {finding.passage.text}", ""]
        else:
            page = finding.passage.page
            lines += [
                f"Best match is on **page {page}** of the source. The text for "
                "this citekey has no reading order (see src/passages.py), "
                "so the page is reported without quoting from it.",
                "",
            ]
    return "\n".join(lines)


def write_report(draft_path: Path, formats: list[str]) -> dict[str, Path]:
    """Writes the report and returns {format: path} for what succeeded.

    `md` is produced directly. `tex`/`pdf` go through
    src/render_output.py, the same path every genre draft uses --
    it needs pandoc/pdflatex on PATH, so a missing binary is reported and
    skipped rather than failing the whole run, matching how every other
    stage in this project treats an absent optional tool.
    """
    report = build_report(draft_path)
    config.PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)
    md_path = config.PROVENANCE_DIR / f"{draft_path.stem}.provenance.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    written = {"md": md_path}

    remaining = [f for f in formats if f != "md"]
    if not remaining:
        return written

    # Imported here rather than at module top only to keep the import
    # cost off the md-only path; render_output is itself stdlib-only, so
    # there is no optional dependency to guard against.
    from src import render_output

    for fmt in remaining:
        try:
            written[fmt] = render_output.render(str(md_path), fmt)
        except render_output.MissingBinary as exc:
            print(f"  WARNING: skipped {fmt} -- {exc}", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report what in each cited source supports the claim citing it.",
    )
    parser.add_argument("draft", help="Markdown draft to check")
    parser.add_argument(
        "--formats", default="md,tex,pdf",
        help="Comma-separated output formats (default: md,tex,pdf). "
             "tex/pdf need pandoc/pdflatex on PATH.",
    )
    args = parser.parse_args(argv)

    draft_path = Path(args.draft)
    if not draft_path.is_file():
        print(f"No such draft: {draft_path}", file=sys.stderr)
        return 1

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    written = write_report(draft_path, formats)
    for fmt in ("md", "tex", "pdf"):
        if fmt in written:
            print(f"  {fmt:3s} {written[fmt]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

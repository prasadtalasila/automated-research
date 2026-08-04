"""Where a citekey's supporting text comes from, and whether it may be
quoted.

One ladder, tried best-first, for any consumer that needs to point at
*part* of a source rather than at the whole thing:

1. `content/docling/<citekey>.passages.json`, if the heavy Docling stage
   has run. Real reading-ordered paragraphs, semantically labelled.
2. `content/parsed/<citekey>.txt` split on form feeds -- page-level only.
3. `pdftotext -layout` on the PDF the ledger recorded, same shape as (2),
   for a citekey parsed by a backend that left no page breaks.

The difference between (1) and (2)/(3) is not cosmetic, and it is the
reason this module exists as its own seam. `pdftotext -layout` preserves
a page's *visual* arrangement rather than its reading order, so on a
two-column paper each output line splices together two unrelated columns
-- 82%-89% of long lines on 4 of the 10 papers measured in this project's
own sample. Bag-of-words *scoring* survives that, because splicing moves
words around within a page rather than between pages. *Quoting* does not:
an excerpt cut from that text is a collage of two arguments, which is
worse than no excerpt at all because it reads as evidence.

So the guarantee is structural rather than advisory: a page-level
`Passage` carries `text=None`, and a caller that wants to quote has
nothing to quote. `quotable` reports that fact; it does not gate a field
that is sitting there anyway.

Extracted from src/citation_provenance.py, which owned this ladder when
it was the only consumer. `src/retrieval.py` is the second -- a snippet
shown to a drafting agent as evidence is under exactly the same
constraint as a passage shown to a reviewer, and the two must not answer
"what does this source say here?" from different text.

Stdlib only (sqlite3/re/subprocess), like citation_gate.py and
references.py -- runs with bare `python3`, no venv.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src import config

# Lowercase alphanumeric runs, stopwords and very short words dropped, so
# matching keys off the words that actually distinguish one claim from
# another.
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
    "is", "are", "be", "this", "that", "as", "by", "from", "at",
    "it", "its", "can", "has", "have", "was", "were", "which", "such",
    "these", "those", "their", "than", "then", "but", "not", "also",
}


def distinctive(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


@dataclass
class Passage:
    """A candidate span of source text. `text` is None when the source
    couldn't be read in reading order, in which case the passage stands
    for a whole page and must not be quoted."""
    page: int | None
    words: set[str]
    text: str | None = None
    label: str | None = None

    @property
    def quotable(self) -> bool:
        return self.text is not None


def _ledger_row(con, citekey: str):
    row = con.execute(
        "SELECT parsed_path, pdf_path, title FROM items WHERE citekey = ?", (citekey,)
    ).fetchone()
    return row


def _from_sidecar(citekey: str) -> list[Passage] | None:
    path = config.DOCLING_DIR / f"{citekey}.passages.json"
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # UnicodeDecodeError alongside the other two: a sidecar truncated
        # mid-write by a killed process can split a multi-byte character,
        # which fails to decode before json ever sees it. Falling back to
        # page-level costs a re-parse at worst; raising would take down a
        # whole report over one damaged file.
        return None
    if not isinstance(records, list) or not records:
        return None
    found = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        text = (rec.get("text") or "").strip()
        if not text:
            continue
        found.append(Passage(page=rec.get("page"), words=distinctive(text),
                             text=text, label=rec.get("label")))
    return found or None


def _from_pages(raw: str) -> list[Passage]:
    """One passage per form-feed-delimited page, not quotable.

    Deliberately whole pages rather than windows within them: a window
    cut from column-spliced text reads as a quotation while being a
    collage, and there is no way to tell from the text alone which
    documents are affected.

    A blank page is dropped but still consumes its number, so a page
    reported here is the page a reader will turn to.
    """
    return [Passage(page=i, words=distinctive(page))
            for i, page in enumerate(raw.split("\f"), 1) if page.strip()]


def source_passages(con, citekey: str) -> tuple[list[Passage], str | None]:
    """Best available passages for `citekey`, plus a reason if there are
    none."""
    sidecar = _from_sidecar(citekey)
    if sidecar:
        return sidecar, None

    row = _ledger_row(con, citekey)
    if row is None:
        return [], "not in the ledger -- run `python -m src.sync`"

    parsed_path, pdf_path, _title = row
    if parsed_path and Path(parsed_path).exists():
        raw = Path(parsed_path).read_text(encoding="utf-8", errors="replace")
        found = _from_pages(raw)
        # A backend that emits no form feeds yields exactly one "page",
        # which would report every hit as p.1. Fall through to the PDF.
        if len(found) > 1:
            return found, None

    if pdf_path and Path(pdf_path).exists():
        try:
            out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                                 capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            return [], f"couldn't run pdftotext on the PDF ({exc})"
        return _from_pages(out.stdout), None

    return [], "no parsed text with page breaks and no readable PDF"

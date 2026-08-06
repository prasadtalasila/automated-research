"""Where a citekey's supporting text comes from, and whether it may be
quoted.

One ladder, tried best-first, for any consumer that needs to point at
*part* of a source rather than at the whole thing:

1. `content/docling/<citekey>.passages.json`, if the enrichment layer's
   Docling stage has run. Real reading-ordered paragraphs, semantically labelled.
2. `content/parsed/<citekey>.passages.json`, if the corpus layer parsed
   this citekey with `[parser].backend = "docling"`. Same records, from
   the same `passage_records()` below.
3. `content/parsed/<citekey>.txt` split on form feeds -- page-level only.
4. `pdftotext -layout` on the PDF the ledger recorded, same shape as (3),
   for a citekey parsed by a backend that left no page breaks.

Rungs 1 and 2 are the same data from two independent parses, and they are
separate files rather than one because the two layers own different
directories and run on their own schedules. The corpus layer must be able
to invalidate *its* sidecar on every re-parse -- including a switch back
to `pdftotext`, which leaves no passages at all -- without deleting an
enrichment sidecar it did not write and cannot reproduce. Rung 1 wins when
both exist: the enrichment stage parses the PDF a second time, under its
own OCR and figure settings, and is the richer of the two.

The difference between (1)/(2) and (3)/(4) is not cosmetic, and it is the
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

`passage_records()` lives here rather than beside either writer for the
same reason the ladder does: this module is stdlib-only and is already
the sidecar's reader, so both producers -- `src/pdf_text.py` in the
corpus layer and `src/enrich/docling_parse.py` in the enrichment layer --
share one definition of what a passage is. It takes a Docling document by
duck-typing (`getattr` only, no import), which is what lets a module with
no venv dependency describe a document only a venv can build.

Extracted from src/citation_provenance.py, which owned this ladder when
it was the only consumer, and kept as its own seam for a second one that
has not been built yet: `src/retrieval.py` still cuts its snippets as a
character window straight out of `content/parsed/`. A snippet shown to a
drafting agent as evidence is under exactly the same constraint as a
passage shown to a reviewer, and the two should not answer "what does
this source say here?" from different text -- but today they do.

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


# Docling labels each text item. Running heads, page numbers and figure
# captions are not prose a claim can be supported by, so they are left
# out of the passage sidecar -- keeping them would let a claim "match"
# a journal name repeated on all 17 pages.
PASSAGE_LABELS = frozenset({"text", "list_item", "section_header", "title"})


def passage_records(dl_doc) -> list[dict]:
    """One record per prose text item: what it says and where it sits.

    This is what makes a *quotable* passage possible. `pdftotext -layout`
    preserves a page's visual arrangement rather than its reading order,
    so on a two-column paper each output line splices together two
    unrelated columns (82%-89% of long lines, measured over this
    project's own sample). Any excerpt drawn from that text is a
    two-argument collage. Docling resolves reading order, so an item here
    is a real paragraph that can be shown to a reviewer verbatim.

    The bounding box rides along because Docling already has it, and it
    is what a future click-through highlight would need; nothing in this
    repo consumes it yet.

    `dl_doc` is a Docling `DoclingDocument`, read entirely through
    `getattr` so that this module -- which must import under a bare
    `python3` -- never names the library. Both writers pass one in: the
    corpus layer's `pdf_text._extract_docling` and the enrichment layer's
    `docling_parse.parse_doc`.
    """
    records = []
    for item in getattr(dl_doc, "texts", []):
        label = str(getattr(item, "label", "")).split(".")[-1].lower()
        text = (getattr(item, "text", "") or "").strip()
        if label not in PASSAGE_LABELS or not text:
            continue
        prov = item.prov[0] if getattr(item, "prov", None) else None
        record = {"text": text, "label": label,
                  "page": getattr(prov, "page_no", None) if prov else None}
        bbox = getattr(prov, "bbox", None) if prov else None
        if bbox is not None:
            record["bbox"] = [getattr(bbox, side, None) for side in ("l", "t", "r", "b")]
        records.append(record)
    return records


def sidecar_path(citekey: str) -> Path:
    """The corpus layer's passage sidecar for `citekey` (rung 2).

    Built from the citekey in one place, so the writer in
    `src/pdf_text.py` and the reader below cannot drift apart. The
    enrichment layer's own sidecar (rung 1) is *not* this path -- it lives
    under `config.DOCLING_DIR` and is keyed by doc_id, which covers
    `papers/pdfs/` documents that have no citekey at all.
    """
    return config.PARSED_DIR / f"{citekey}.passages.json"


def write_sidecar(citekey: str, records: list[dict]) -> Path:
    path = sidecar_path(citekey)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def clear_sidecar(citekey: str) -> None:
    """Drop any corpus-layer sidecar for `citekey`.

    Called before every re-parse rather than after a failed one. A
    sidecar quotes the PDF *as parsed at the time it was written*, so it
    outlives its own truth in three ways: the backend changes to one that
    produces no passages, the parse of an edited PDF fails outright, or
    the same backend re-runs and produces different text. Removing it up
    front makes all three land on "no sidecar" instead of "last week's
    sentences, attributed to today's document".
    """
    sidecar_path(citekey).unlink(missing_ok=True)


def _page_number(raw) -> int | None:
    """A sidecar's `page` as a 1-based page number, or None.

    `passage_records` writes Docling's own `page_no` here, so the
    machine-written case is always an int -- but the file is JSON on
    disk, it may have been hand-edited, and `Passage.page` is both
    rendered straight into "p.{page}" and typed `int | None` for callers
    that store it. Anything that isn't a page number a reader could turn
    to becomes None, which the report already knows how to omit, rather
    than propagating as one.

    `bool` is excluded explicitly because it is an `int` subclass in
    Python, and `True` would otherwise report as page 1.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


def _ledger_row(con, citekey: str):
    row = con.execute(
        "SELECT parsed_path, pdf_path, title FROM items WHERE citekey = ?", (citekey,)
    ).fetchone()
    return row


def _from_sidecar(path: Path) -> list[Passage] | None:
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
        text = rec.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        label = rec.get("label")
        found.append(Passage(page=_page_number(rec.get("page")),
                             words=distinctive(text), text=text,
                             label=label if isinstance(label, str) else None))
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
    # Rung 1 before rung 2: both hold the same kind of record, but the
    # enrichment layer's is a second, independent parse of the PDF under
    # its own OCR and figure settings, so it is the richer of the two
    # whenever a run has paid for it.
    for path in (config.DOCLING_DIR / f"{citekey}.passages.json",
                 sidecar_path(citekey)):
        sidecar = _from_sidecar(path)
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
        # Both shipped backends do emit them, so this now catches a
        # genuinely single-page source (where rung 4 says the same thing)
        # rather than every docling parse.
        if len(found) > 1:
            return found, None

    if pdf_path and Path(pdf_path).exists():
        try:
            # encoding/errors rather than a bare text=True: that decodes
            # with the *platform* encoding under strict error handling, so
            # a single undecodable byte anywhere in a paper -- a ligature,
            # a stray control character, anything under a C-locale host --
            # raises UnicodeDecodeError, which is not in the except clause
            # below and would take down a whole report over one PDF. Same
            # guard the parsed-text branch above already applies.
            out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                                 capture_output=True, check=True,
                                 encoding="utf-8", errors="replace")
        except (OSError, subprocess.CalledProcessError) as exc:
            return [], f"couldn't run pdftotext on the PDF ({exc})"
        return _from_pages(out.stdout), None

    return [], "no parsed text with page breaks and no readable PDF"

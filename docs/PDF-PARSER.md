# PDF parser tradeoffs for automated-research

## Short summary

This repository needs PDF processing that balances speed, text quality, structure preservation, and portability.

The main candidates are:
- `pdftotext`
- `markitdown` -- **removed 2026-08-01**, see ["Why markitdown was removed"](#why-markitdown-was-removed)
- `docling`

`grobid` was evaluated as a fourth candidate and **removed from the repo on 2026-08-01**. It is kept in the comparison below as a record of that decision, not as an available backend -- see ["Why GROBID was removed"](#why-grobid-was-removed).

## Comparison table

| Tool | Best at | Strengths | Weaknesses | Relative speed vs `pdftotext` | Fit for this repo |
|---|---|---|---|---|---|
| `pdftotext` | Plain text extraction | Very fast, simple, stable, low dependency footprint | Weak on layout, tables, headings, and reading order | 1x | Best lightweight baseline |
| `markitdown` | General file-to-Markdown conversion | Flexible normalization, multi-format support | Fuses adjacent words on this corpus (4.19% of tokens), which breaks whitespace-tokenized retrieval | ~17x slower (measured, 5 real bib PDFs) | **Removed 2026-08-01** -- see below |
| `docling` | Layout-aware PDF parsing | Better reading order, sections, tables, and structured Markdown | Heavy, slower, model/runtime complexity | ~42x slower (measured, 5 real bib PDFs, OCR on -- see note below) | Best quality parser for the heavy path |
| `grobid` | Scholarly structure and references | Excellent for title, abstract, sections, and references | Not a general-purpose plain-text extractor; needs a JDK 21 build and a long-running service | Separate from the main speed scale | **Removed 2026-08-01** -- see below |

## Likely behavior in practice

### `pdftotext`
This is the fastest option and the easiest to operate. It is well suited to the repo's lightweight core pipeline when the goal is simply to get searchable text into the ledger and retrieval index.

### `markitdown`
A general conversion tool rather than a scholarly parser, and meaningfully slower than `pdftotext` (~17x measured). Measurement on this repo's own corpus later showed it loses word boundaries here, which is why it is no longer a backend -- see below.

### `docling`
This is the best fit when the PDF's structure matters: headings, tables, reading order, and section boundaries. It is much slower and heavier (~42x measured), but the output is more useful for later chunking, retrieval, and topic modeling.

**The ~42x figure predates the OCR default.** It was measured with
Docling's OCR stage on, which is Docling's default but has not been this
project's since 0.12.0 (`config.toml`'s `[parser].ocr = false`). Over a
separate, larger sample -- 16 bib PDFs, 943 pages -- turning OCR off was
**2.46x** faster, so the current default sits well below 42x. The two
measurements used different samples, so they don't compose into a single
honest number; treat 42x as the OCR-on ceiling and see
`bench/RESULTS.md` in the repository (developer-only -- it is not part of the release zip) for the corpus-wide
figures that replaced it (a full 501-PDF parse: ~39 minutes with OCR off, ~1.6 hours
with it on).

Turning OCR off is a trade-off, not a free win: it drops text that the
PDF stores as a bitmap rather than as characters, which on this sample
was mostly publisher furniture and figure sub-captions but on one
document included two whole tables. See README's ["OCR: off by default,
and why that is a trade-off"](../README.md#ocr-off-by-default-and-why-that-is-a-trade-off).

### `grobid`
GROBID is most valuable for reference extraction and scholarly structure. It was never a drop-in replacement for the other tools, and is no longer part of this repo -- see below.

## Recommended use in this repository

A practical tiered strategy:

1. **`pdftotext`** for the fast baseline path
2. **`docling`** for high-quality structured parsing

That tiering matches the repository's design philosophy:
- probe first
- degrade gracefully
- keep the core pipeline usable even when heavy dependencies are absent

## Quality tradeoff for this repo

### If speed is the priority
Use `pdftotext`.

### If PDF structure is the priority
Use `docling`.

### If references and scholarly metadata are the priority
`papers/bibliography.bib` already supplies these -- it is the source of truth for title, authors, year, and DOI (see README's "Configuration"). No parser needs to re-derive them.

## Notes on cross-platform support

- `pdftotext` depends on an external system package, so it is not the most portable option.
- `docling` is the heaviest option and may be the hardest to support consistently across operating systems.

If cross-platform support is important, the best approach is to treat these as **optional backends** and keep a fallback ladder rather than relying on a single tool.

## Suggested architecture

A robust design for this repo would be:

- core pipeline: `pdftotext`
- heavy structured path: `docling`

That gives a good balance of:
- speed
- fidelity
- portability
- downstream retrieval quality

## Conclusion

For this repository:
- `pdftotext` is the fastest and simplest
- `docling` is the best structured PDF parser

The best overall outcome is not choosing one tool, but combining them in a layered backend strategy.

## Why GROBID was removed

GROBID's role here was bibliographic-quality header and reference
extraction, and it only ever called one endpoint
(`/api/processHeaderDocument`) for title/authors/abstract. That is
metadata `papers/bibliography.bib` already provides for every document
the project cares about: the goal is to parse the PDFs the bib file
names, and those arrive with real metadata already attached via
`src/bib_reader.py`.

What GROBID uniquely offered -- parsing a paper's own reference list into
structured author/title/year/DOI records, via the
`/api/processFulltextDocument` endpoint this repo never called -- serves
*corpus discovery* ("which papers do my papers cite that I don't have
yet"), not grounding. Extracted references are not in the bib file, so
per AGENTS.md's citekey invariant they can never be cited anyway.

Against that, the operational cost was a JDK 21 pinned exactly (its
bundled Kotlin compiler cannot parse a JDK 25 version string), a
multi-GB multi-minute Gradle build, and a long-running service on port
8070. Not worth it for a capability the project doesn't use.

If corpus-growth-by-snowballing later becomes a real workflow, the case
to revisit is for `/api/processFulltextDocument` specifically -- not the
header endpoint that was here.

## Why markitdown was removed

Removed 2026-08-01, after measurement on this repository's own corpus
rather than on its stated feature set.

**The symptom.** Over the same 10 CPS papers, `markitdown` produced
**3,647 alphabetic tokens longer than 20 characters (4.17% of all
tokens)** against `pdftotext`'s **9 (0.01%)** -- a factor of 400 -- and
23% fewer total words, because words were being *fused* rather than
dropped. It is visible directly in retrieval snippets:

```
isaninputtooranoutputfromafunction
AnnualReviewsinControl51(2021)357-373
theapplicationofthevery same principles
```

**Why that matters.** `src/retrieval.py` is BM25 over whitespace
tokens. A query for "cyber physical" cannot match text fused into
`cyberphysicalsystems`, so this is a silent ranking failure, not a
cosmetic one.

**The cause.** `markitdown` extracts PDFs via `pdfplumber`, calling
`page.extract_text()` with no arguments. pdfplumber's default
`x_tolerance` is 3 points: glyphs closer than that are treated as one
word. These papers set inter-word spacing below 3pt. Measured on four
of them, dropping to `x_tolerance=1` eliminated every over-long token
(179 -> 0, 83 -> 0, 164 -> 0, 141 -> 0) and roughly doubled the word
count. `pdftotext` reads the same files correctly, so the spacing
information is present in the PDFs -- this is the extractor's threshold,
not damaged input.

**Why it wasn't fixable here.** `markitdown`'s PDF converter hardcodes
both `page.extract_text()` and `extract_words(x_tolerance=3,
y_tolerance=3)`. Its `convert()` accepts `**kwargs` but never forwards
them, so the tolerance is unreachable through its public API. Its own
source comments that the heuristic is "not for multi-column text layouts
in scientific documents" -- which is this entire corpus.

**What replaced it.** Nothing: `markitdown` sat in the middle of a
three-way ladder while being worse than `pdftotext` on text and worse
than `docling` on structure, so the ladder is now two rungs. Using
`pdfplumber` directly with a tuned tolerance was considered and
deferred -- it would no longer be "markitdown", and no current use case
needs a tier between the two remaining backends.

**What was added instead.** A parse-quality guard
(`src/pdf_text.quality_warning`, wired into `sync`) that warns when more
than 1% of a document's words exceed 20 characters. The two backends sit
three orders of magnitude apart on that measure, so the threshold does
not need precise tuning. Had it existed earlier, this would have been
reported by `sync` on the first run instead of being noticed by eye in a
retrieval snippet.

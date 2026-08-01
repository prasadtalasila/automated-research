# PDF parser tradeoffs for automated-research

## Short summary

This repository needs PDF processing that balances speed, text quality, structure preservation, and portability.

The main candidates are:
- `pdftotext`
- `markitdown`
- `docling`

`grobid` was evaluated as a fourth candidate and **removed from the repo on 2026-08-01**. It is kept in the comparison below as a record of that decision, not as an available backend -- see ["Why GROBID was removed"](#why-grobid-was-removed).

## Comparison table

| Tool | Best at | Strengths | Weaknesses | Relative speed vs `pdftotext` | Fit for this repo |
|---|---|---|---|---|---|
| `pdftotext` | Plain text extraction | Very fast, simple, stable, low dependency footprint | Weak on layout, tables, headings, and reading order | 1x | Best lightweight baseline |
| `markitdown` | General file-to-Markdown conversion | Flexible normalization, multi-format support, good Markdown output | Less specialized for scholarly PDF structure; may miss layout details | ~17x slower (measured, 5 real bib PDFs) | Good fallback / normalization layer |
| `docling` | Layout-aware PDF parsing | Better reading order, sections, tables, and structured Markdown | Heavy, slower, model/runtime complexity | ~42x slower (measured, 5 real bib PDFs) | Best quality parser for the heavy path |
| `grobid` | Scholarly structure and references | Excellent for title, abstract, sections, and references | Not a general-purpose plain-text extractor; needs a JDK 21 build and a long-running service | Separate from the main speed scale | **Removed 2026-08-01** -- see below |

## Likely behavior in practice

### `pdftotext`
This is the fastest option and the easiest to operate. It is well suited to the repo's lightweight core pipeline when the goal is simply to get searchable text into the ledger and retrieval index.

### `markitdown`
This is more of a general conversion tool than a scholarly parser. For mixed document collections or when Markdown normalization matters, it can be useful. It is meaningfully slower than `pdftotext` (~17x measured), not just marginally.

### `docling`
This is the best fit when the PDF's structure matters: headings, tables, reading order, and section boundaries. It is much slower and heavier (~42x measured), but the output is more useful for later chunking, retrieval, and topic modeling.

### `grobid`
GROBID is most valuable for reference extraction and scholarly structure. It was never a drop-in replacement for the other tools, and is no longer part of this repo -- see below.

## Recommended use in this repository

A practical tiered strategy:

1. **`pdftotext`** for the fast baseline path
2. **`markitdown`** for broader conversion / fallback
3. **`docling`** for high-quality structured parsing

That tiering matches the repository's design philosophy:
- probe first
- degrade gracefully
- keep the core pipeline usable even when heavy dependencies are absent

## Quality tradeoff for this repo

### If speed is the priority
Use `pdftotext`.

### If normalized Markdown is the priority
Use `markitdown`.

### If PDF structure is the priority
Use `docling`.

### If references and scholarly metadata are the priority
`papers/bibliography.bib` already supplies these -- it is the source of truth for title, authors, year, and DOI (see README's "Configuration"). No parser needs to re-derive them.

## Notes on cross-platform support

- `pdftotext` depends on an external system package, so it is not the most portable option.
- `markitdown` is more flexible as a general converter, but still depends on available backend support.
- `docling` is the heaviest option and may be the hardest to support consistently across operating systems.

If cross-platform support is important, the best approach is to treat these as **optional backends** and keep a fallback ladder rather than relying on a single tool.

## Suggested architecture

A robust design for this repo would be:

- core pipeline: `pdftotext`
- optional normalization path: `markitdown`
- heavy structured path: `docling`

That gives a good balance of:
- speed
- fidelity
- portability
- downstream retrieval quality

## Conclusion

For this repository:
- `pdftotext` is the fastest and simplest
- `markitdown` is the best general converter fallback
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

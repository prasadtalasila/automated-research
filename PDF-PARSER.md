# PDF parser tradeoffs for automated-research

## Short summary

This repository needs PDF processing that balances speed, text quality, structure preservation, and portability.

The main candidates are:
- `pdftotext`
- `markitdown`
- `docling`

`grobid` is different enough that it should usually be treated separately, because it is strongest at scholarly metadata, references, and document structure rather than plain text extraction.

## Comparison table

| Tool | Best at | Strengths | Weaknesses | Relative speed vs `pdftotext` | Fit for this repo |
|---|---|---|---|---|---|
| `pdftotext` | Plain text extraction | Very fast, simple, stable, low dependency footprint | Weak on layout, tables, headings, and reading order | 1x | Best lightweight baseline |
| `markitdown` | General file-to-Markdown conversion | Flexible normalization, multi-format support, good Markdown output | Less specialized for scholarly PDF structure; may miss layout details | ~17x slower (measured, 5 real bib PDFs) | Good fallback / normalization layer |
| `docling` | Layout-aware PDF parsing | Better reading order, sections, tables, and structured Markdown | Heavy, slower, model/runtime complexity | ~42x slower (measured, 5 real bib PDFs) | Best quality parser for the heavy path |
| `grobid` | Scholarly structure and references | Excellent for title, abstract, sections, and references | Not a general-purpose plain-text extractor | Separate from the main speed scale | Keep separate; use for reference strengthening |

## Likely behavior in practice

### `pdftotext`
This is the fastest option and the easiest to operate. It is well suited to the repo's lightweight core pipeline when the goal is simply to get searchable text into the ledger and retrieval index.

### `markitdown`
This is more of a general conversion tool than a scholarly parser. For mixed document collections or when Markdown normalization matters, it can be useful. It is meaningfully slower than `pdftotext` (~17x measured), not just marginally.

### `docling`
This is the best fit when the PDF's structure matters: headings, tables, reading order, and section boundaries. It is much slower and heavier (~42x measured), but the output is more useful for later chunking, retrieval, and topic modeling.

### `grobid`
GROBID should usually stay separate because it is most valuable for reference extraction and scholarly structure. It is not a drop-in replacement for the other tools, but it can complement them.

## Recommended use in this repository

A practical tiered strategy would be:

1. **`pdftotext`** for the fast baseline path
2. **`markitdown`** for broader conversion / fallback
3. **`docling`** for high-quality structured parsing
4. **`grobid`** for scholarly metadata and references

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
Use `grobid` in addition to one of the above, not instead of them.

## Notes on cross-platform support

- `pdftotext` depends on an external system package, so it is not the most portable option.
- `markitdown` is more flexible as a general converter, but still depends on available backend support.
- `docling` is the heaviest option and may be the hardest to support consistently across operating systems.
- `grobid` is usually most comfortable on Linux or in Docker.

If cross-platform support is important, the best approach is to treat these as **optional backends** and keep a fallback ladder rather than relying on a single tool.

## Suggested architecture

A robust design for this repo would be:

- core pipeline: `pdftotext`
- optional normalization path: `markitdown`
- heavy structured path: `docling`
- reference-strengthening path: `grobid`

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
- `grobid` is best kept separate for scholarly metadata and references

The best overall outcome is not choosing one tool, but combining them in a layered backend strategy.

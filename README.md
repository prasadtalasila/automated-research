# Automated Research Pipeline

Turns a Zotero library into grounded survey papers, thesis chapters, and
undergraduate tutorial chapters, with every citation traceable back to a
paper Zotero actually holds.

## Architecture

Two layers, per the original design:

- **Content layer** (shared, deterministic, safe to run unattended):
  `Zotero SQLite -> src/zotero_reader.py -> src/ledger.py (content/ledger.sqlite)
  -> src/pdf_text.py (content/parsed/*.txt) -> src/retrieval.py`
  Run via `python -m src.sync`. Idempotent and incremental -- a paper is only
  re-parsed if its PDF content actually changed.
- **Genre layer** (generative, on-demand, reviewed by you): three Claude Code
  skills in `.claude/skills/` -- `survey-writer`, `thesis-chapter-writer`,
  `tutorial-writer` -- each a thin template over the same content layer.

Every genre skill runs `python -m src.citation_gate` on its own output before
presenting a draft, and refuses to invent a citekey. See [CLAUDE.md](CLAUDE.md)
for why this is a hard gate rather than a style suggestion.

## Quickstart

```bash
# 1. Sync the content layer from Zotero (reads zotero.sqlite directly;
#    no Better BibTeX plugin required, no dependencies to install)
python3 -m src.sync

# 2. Inspect what it found
sqlite3 content/ledger.sqlite 'select citekey, status, title from items;'
cat content/library.bib

# 3. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a tutorial chapter introducing digital twin asset reuse"
# The matching skill in .claude/skills/ picks this up automatically.

# 4. Manually re-check citations in any draft yourself
python3 -m src.citation_gate path/to/draft.md
```

By default it reads `/home/TestUserDTaaS/Zotero`. Override with
`ZOTERO_DATA_DIR=/path/to/other/Zotero python3 -m src.sync` if you point it at
a different library.

## What runs here vs. what needs the Docker path

This host has no root, no Java, no TeX Live, no Pandoc, and `pip install`
outside a venv is blocked (PEP 668 / externally-managed-environment). The
core pipeline is written to need none of that:

| Capability | Here | Needs `docker/` |
|---|---|---|
| Read Zotero library, generate citekeys + `.bib` | stdlib `sqlite3` | -- |
| Extract PDF text | `pdftotext` (already present) | -- |
| Track parse status incrementally | stdlib `sqlite3` | -- |
| Keyword-based retrieval | stdlib only | -- |
| Citation verification gate | stdlib `re` | -- |
| Bibliographic-quality parsing (references, sections) | -- | GROBID |
| Embedding-based retrieval + topic clustering | -- | sentence-transformers, Chroma, BERTopic |
| Compiling generated `.tex` chapters to PDF | -- | TeX Live |
| Format conversion (e.g. to DOCX) | -- | Pandoc |

`docker/Dockerfile` + `docker/setup.sh` scaffold that heavier environment
(Ubuntu 24.04, per the original container design). **Neither has been run in
this session** -- no Docker daemon is available on this host to build or test
them against. Validate before relying on them.

Retrieval today is a keyword-overlap ranker (`src/retrieval.py`), not
embeddings -- deliberately: this Zotero library currently holds 2 items, and
installing sentence-transformers/Chroma/BERTopic for a 2-document corpus
would be pure overhead. The function signature is stable so swapping in real
embeddings later (via the Docker path, once the corpus is larger) doesn't
require touching the genre skills that call it.

## Repository layout

```
src/                   deterministic pipeline (stdlib only)
  config.py              paths, env var overrides
  zotero_reader.py       reads zotero.sqlite, generates citekeys, exports .bib
  ledger.py              per-citekey status tracking (content/ledger.sqlite)
  pdf_text.py            pdftotext wrapper
  sync.py                orchestrates the above -- the "job 1" entrypoint
  retrieval.py           keyword search over the content layer
  citation_gate.py       hard citation-verification gate -- "job 2" must pass this
content/                generated, gitignored (regenerate with sync)
  ledger.sqlite
  library.bib
  parsed/<citekey>.txt
  provenance/            genre skills log which citekeys backed which section
.claude/skills/          genre layer: survey-writer, thesis-chapter-writer, tutorial-writer
docker/                  optional heavier toolchain (GROBID, TeX Live, embeddings) -- unverified
```

# CLAUDE.md

Guidance for working in this repository specifically.

## The hard invariant: never fabricate a citekey

`papers/DT-Simulation-Patterns/main.bib` (a sibling project on this same
machine) already contains entries a prior review marked
`WARNING: UNVERIFIABLE` -- fabricated placeholder references that made it
into a real paper. That is the failure mode this pipeline is built to
prevent.

Rule: a citekey may only be used if it appears in `bibliography.bib`
(source of truth -- see below) and was picked up into `content/ledger.sqlite`
by `python -m src.sync`. If a citation would help an argument but isn't in
the bib file, say so in prose to the user -- do not invent a key for it, and
do not "fix" a gate failure by making up a plausible-looking key instead of
removing the claim or sourcing a real one.

Every genre skill (`survey-writer`, `thesis-chapter-writer`, `tutorial-writer`
in `.claude/skills/`) must run `python -m src.citation_gate <file>` on its own
output and only present the draft once it exits 0. This is a gate, not a
lint suggestion -- treat a `FAIL` the same way you'd treat a failing test.

## The bib file is the source of truth (not this pipeline)

`bibliography.bib` (repo root, path configurable via `config.toml`'s
`[bib].path` or the `BIB_FILE` env var) is a manual export from Zotero
(File > Export Library > BibTeX) -- no Better BibTeX plugin is installed, so
it is not continuously auto-synced. Whatever citekey BibTeX assigns there
(e.g. `talasila_composable_2025`, or `noauthor_digital_nodate` for an
item Zotero couldn't find an author for) is the citekey everywhere
downstream. `src/bib_reader.py` parses it and is the only place that reads
it; nothing else should ever generate or guess a citekey.

This was a deliberate pivot (2026-07-28) away from an earlier design that
read `zotero.sqlite` directly and generated its own citekeys
(`author+year+titleword`) -- that approach is gone. **If you find old
generated content citing keys in that old format (e.g.
`talasila2025composable` instead of `talasila_composable_2025`), those
citations are now stale and will fail the gate** -- that's expected, not a
regression; re-cite using whatever's actually in `bibliography.bib`.

To add papers: add them in Zotero, re-export `bibliography.bib`, re-run
`python -m src.sync`. There is no watch/auto-export step here.

## Two-job split

- **Job 1 -- deterministic pipeline** (`python -m src.sync`): bib file read
  -> ledger update -> PDF text extraction (paths come straight from the bib
  file's `file` field). No LLM calls, no judgment calls, idempotent. Safe to
  run unattended or on a schedule.
- **Job 2 -- generative drafting** (the three `.claude/skills/`, or the
  heavier `scripts/full_pipeline.py` stages): invoked on demand, reviewed by
  the user. These read the content layer; they never write to
  `content/ledger.sqlite` directly (only `sync` does).

## Config lives in `config.toml`

`src/config.py` loads `config.toml` (repo root) via stdlib `tomllib`, with
every setting overridable by an env var of the same name (e.g.
`BIB_FILE=/other/path.bib python -m src.sync`). Add new settings there, not
as hardcoded values in `config.py`.

## Environment constraints on this host

No root/sudo, no Java, no TeX Live, no Pandoc. `pip install` outside a venv
is blocked (PEP 668) -- **this now matters for the core pipeline too**:
`python -m src.sync` needs `bibtexparser` (parsing `bibliography.bib`
correctly -- nested braces, LaTeX escapes -- isn't worth hand-rolling), so
it must be run via the installed venv, not the bare system interpreter.
`python -m src.citation_gate` is the exception -- it only reads
`content/ledger.sqlite` (stdlib `sqlite3`) and still runs with bare `python3`.

Install everything (core `bibtexparser` + the full `src/heavy/` stack) with:
```
bash scripts/install_full_pipeline.sh
```
This is **the single install script for both the host and Docker** --
`docker/Dockerfile` calls this exact script (with `SKIP_VENV=1`) rather than
having its own separate pip install logic. If you find a dependency-order
issue (there was a real one: installing `paper-qa` and `knowledge-storm`
sequentially breaks `paper-qa` via a `litellm`/`openai` version conflict --
see `docker/requirements-full.txt`), fix it once in
`docker/requirements-full.txt` and both targets pick it up. Don't add a
second install path.

`docker/` (Dockerfile + `docker/setup.sh` for GROBID) is scaffolded to
additionally unlock Java/GROBID, TeX Live, and Pandoc, none of which are
installable here without root -- it has not been built or run in this
environment (no Docker daemon here). Treat it as a draft to validate, not a
tested artifact, except for the parts of `docker/requirements-full.txt` that
were verified in a host venv (see its header comment for exactly what was
and wasn't).

## The heavy pipeline (`src/heavy/`, `scripts/full_pipeline.py`)

Implements Docling -> GROBID/Zotero -> sentence-transformers/Chroma ->
BERTopic -> PaperQA2 -> STORM -> Pandoc/LaTeX, one script for both host and
Docker (`scripts/full_pipeline.py --target host|docker`). Each stage
self-probes its own prerequisites (reachable GROBID, an LLM API key,
pandoc/pdflatex on PATH) and reports honestly (`skipped`/`no-api-key`/
`missing-binary`) rather than assuming the target implies availability --
don't "fix" a skip by hardcoding target-specific behavior; fix the probe if
it's wrong.

`src/heavy/corpus.py` unifies two identifier namespaces: ledger items get
`doc_id == citekey` (real, citable); `source-pdfs/*.pdf` (raw PDFs gathered
outside Zotero, e.g. an open metadata-API search) get `doc:<filename stem>`,
which can never collide with a bib citekey (those never contain a colon)
and which `citation_gate.py` will always reject. Keep it that way -- don't
give a `source-pdfs` doc anything citekey-shaped.

## Retrieval

`src/retrieval.py` (keyword overlap, stdlib-only) is what the genre skills
use by default -- the corpus is still small enough that embeddings are
overhead without payoff. `src/heavy/embed_index.py` (sentence-transformers +
Chroma) is a verified, working upgrade path once that stops being true;
its `search(query, k)` shape matches `src/retrieval.py`'s so callers don't
need to change when you swap one for the other.

## Don't touch `papers/`

`papers/DT-Simulation-Patterns/` is a separate, already-written paper (not
this pipeline's input or output) -- it happens to sit on the same machine
and is referenced above only as a cautionary example and, in
`thesis-chapter-writer`, as a LaTeX structural reference. Don't ingest it
into `content/` or treat it as part of this repo's corpus.

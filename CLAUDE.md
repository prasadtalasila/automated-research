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
`[bib].path` or the `BIB_FILE` env var) is a manual export from your
reference manager's BibTeX export feature -- no auto-sync plugin is
installed, so it is not continuously auto-synced. Whatever citekey BibTeX
assigns there (e.g. `talasila_composable_2025`, or `noauthor_digital_nodate`
for an item with no discoverable author) is the citekey everywhere
downstream. `src/bib_reader.py` parses it and is the only place that reads
it; nothing else should ever generate or guess a citekey.

This was a deliberate pivot (2026-07-28) away from an earlier design that
read the reference manager's own database directly and generated its own
citekeys (`author+year+titleword`) -- that approach is gone. **If you find
old generated content citing keys in that old format (e.g.
`talasila2025composable` instead of `talasila_composable_2025`), those
citations are now stale and will fail the gate** -- that's expected, not a
regression; re-cite using whatever's actually in `bibliography.bib`.

To add papers: add them in your reference manager, re-export
`bibliography.bib`, re-run `python -m src.sync`. There is no
watch/auto-export step here.

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

`pip install` outside a venv is blocked (PEP 668) -- unconditionally, on
every host, regardless of root access. **This matters for the core
pipeline too**: `python -m src.sync` needs `bibtexparser` (parsing
`bibliography.bib` correctly -- nested braces, LaTeX escapes -- isn't
worth hand-rolling), so it must be run via the installed venv, not the
bare system interpreter. `python -m src.citation_gate` is the exception --
it only reads `content/ledger.sqlite` (stdlib `sqlite3`) and still runs
with bare `python3`.

Root/sudo, a JDK, TeX Live, and Pandoc were previously assumed unavailable
on this host -- **that assumption no longer holds** (verified
2026-07-28): root is available via `sudo`, and a JDK, TeX Live, and Pandoc
are all installed and working. Don't assume this generalizes to every
host running this repo, though -- treat availability as something to
probe, not assume, in either direction:

- **When heavy-pipeline dependencies are present:** stages that need them
  (GROBID; Pandoc/TeX Live rendering) work directly on the host, not only
  inside `docker/` -- there is nothing docker-exclusive about GROBID
  other than that `docker/setup.sh` happens to script it for that target.
  Building GROBID standalone needs a JDK **21 specifically, not whatever's
  newest**: its `build.gradle` pins a Java 21 toolchain, and its bundled
  Kotlin compiler (2.0.21) cannot parse a JDK 25 version string. See
  README's "Building GROBID standalone" section for the exact recipe and
  failure mode.
- **When they're absent:** don't hang, stack-trace, or silently skip
  without saying so. Every `src/heavy/*` stage already self-probes its
  own prerequisites and reports honestly (`ok`/`skipped`/`no-api-key`/
  `missing-binary`) via `scripts/full_pipeline.py` rather than assuming
  the target implies availability -- keep any new stage consistent with
  that pattern instead of inventing a new fallback policy.

Install everything with:
```
bash scripts/install_full_pipeline.sh              # Python deps only (default) -- what every host needs regardless of OS packages
bash scripts/install_full_pipeline.sh os-deps      # apt-get: JDK 21, TeX Live, Pandoc, poppler-utils -- needs root, opt-in
bash scripts/install_full_pipeline.sh grobid       # fetch + build GROBID standalone -- multi-GB, opt-in
bash scripts/install_full_pipeline.sh all          # os-deps + python-deps (not grobid -- too heavy to bundle by default)
```
This is **the single install script for both the host and Docker** --
`docker/Dockerfile` calls it once per stage (`os-deps`, `grobid`,
`python-deps` with `SKIP_VENV=1`) as separate `RUN` lines, each its own
cached layer, rather than having its own separate apt-get/pip install
logic. If you find a dependency-order issue (there was a real one:
installing `paper-qa` and `knowledge-storm` sequentially breaks
`paper-qa` via a `litellm`/`openai` version conflict -- see
`docker/requirements-full.txt`), fix it once in
`docker/requirements-full.txt` and both targets pick it up. Don't add a
second install path.

`docker/` (Dockerfile + `docker/setup.sh`) builds the same GROBID/TeX
Live/Pandoc stack inside a container instead, for hosts where the
`os-deps` assumption above doesn't hold (no root, or root deliberately
withheld). **It has still not been built or run in this environment** (no
Docker daemon here) -- treat it as a draft to validate, not a tested
artifact, except for the parts of `docker/requirements-full.txt` that were
verified in a host venv (see its header comment for exactly what was and
wasn't).

## The heavy pipeline (`src/heavy/`, `scripts/full_pipeline.py`)

Implements Docling -> GROBID -> sentence-transformers/Chroma ->
BERTopic -> PaperQA2 -> STORM -> Pandoc/LaTeX, one script for both host and
Docker (`scripts/full_pipeline.py --target host|docker`). Each stage
self-probes its own prerequisites (reachable GROBID, an LLM API key,
pandoc/pdflatex on PATH) and reports honestly (`skipped`/`no-api-key`/
`missing-binary`) rather than assuming the target implies availability --
don't "fix" a skip by hardcoding target-specific behavior; fix the probe if
it's wrong.

`src/heavy/corpus.py` unifies two identifier namespaces: ledger items get
`doc_id == citekey` (real, citable); `source-pdfs/*.pdf` (raw PDFs gathered
outside the bib file, e.g. an open metadata-API search) get `doc:<filename stem>`,
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

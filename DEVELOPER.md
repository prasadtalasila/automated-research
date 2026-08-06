# Developer guide

Material for working on this repository itself, as opposed to using it to
draft content -- test running, the full source layout, and known gaps.
See [README.md](README.md) for the user-facing Quickstart/Configuration/
Architecture docs and [DOCKER.md](DOCKER.md) for the container build.

## Table of contents

- [Running tests](#running-tests)
- [Benchmarking the parser](#benchmarking-the-parser)
- [Writing a script that drives the enrichment layer](#writing-a-script-that-drives-the-enrichment-layer)
- [Repository layout](#repository-layout)
- [Figures and copyright](#figures-and-copyright)
- [Citation provenance](#citation-provenance)
- [Open questions and unbuilt features](#open-questions-and-unbuilt-features)

## Running tests

```bash
# Install pytest/pytest-cov into the same venv (run python-deps first)
bash scripts/install_full_pipeline.sh dev-deps

# Run the full suite with coverage
.venv-full/bin/python -m pytest --cov=src --cov=scripts --cov-report=term-missing
```

`tests/` covers both the corpus layer and `src/enrich/*` -- the enrich group's
dependencies (docling, chromadb, bertopic,
sentence-transformers) are mocked via `sys.modules` for fast,
deterministic unit tests, so the
`dev-deps` group alone is *not* enough on its own: the `enrich` group
(`python-deps`, step 1 of Quickstart) must already be installed too, since
`tests/test_bib_reader.py` needs `bibtexparser` and the `src/enrich/` test
modules need docling/chromadb/bertopic/sentence-transformers. A handful of tests
(`tests/test_feature_workflows.py`, the `TestRenderReal`/`TestExtractTextReal`
classes elsewhere) run the real `pdftotext`/`pandoc`/`pdflatex` binaries
end to end rather than mocking them, and skip automatically if those
aren't on `PATH`.

## Benchmarking the parser

`bench/` measures what a full `docling` parse of the bib corpus costs on
a given machine, and is deliberately kept out of `tests/`: it takes a couple of
hours, needs real PDFs and a GPU, and answers a "how long / what's the
bottleneck" question rather than a pass/fail one. It is excluded from the
release zip for the same reason `tests/` is.

- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) -- what each setting costs,
  organised by setting. Ships in the release archive, unlike `bench/`
- [docs/PARALLELISM.md](docs/PARALLELISM.md) -- parallel parse design:
  architecture, components, and the roadmap
- [bench/README.md](bench/README.md) -- how to run it, and what each
  switch measures
- [bench/RESULTS.md](bench/RESULTS.md) -- the 2026-08-02 baseline, with
  raw per-PDF timings in `bench/results/`
- [bench/PARALLELISM-PLAN.md](bench/PARALLELISM-PLAN.md) -- what is still
  unknown, and what to measure before changing it

The headline, in the order it was found:

1. Parsing all 501 bib PDFs with `docling` took ~1.6 hours (later
   measured at 1h 56m), with the A40 at ~7% utilization and three CPU
   cores of 48 busy. The GPU was worth only 1.79x over CPU-only -- the
   work was CPU-bound.
2. Turning OCR off (v0.12.0) was worth 2.46x on a serial sample, more
   than the GPU. Measured end to end later: 2.08x serially, but 3.91x at
   12 workers and 4.79x at 24, since OCR competes for the same CPU the
   parallelism needs.
3. Parallelising `sync` (v1.0.0) was worth 3.60x at four workers.
4. That moved the bottleneck onto a single GPU: `AcceleratorDevice.AUTO`
   resolves to `cuda:0` in every worker, so GPU 0 ran at 100% while
   GPUs 1-3 idled.
5. Giving each worker its own card (v1.1.0) was worth a further **1.62x**
   on the full corpus -- 528s to 326s at twelve workers. The whole
   501-PDF corpus now parses in **5m 10s**, against 1h 56m where this
   started.
6. Per-worker startup (v2.1.0) turned out to be 3.2s of importing torch
   and docling plus ~5s of loading Docling's models, and only the first
   is shareable between processes. A forkserver pool with those modules
   preloaded, started before the bibliography is read, takes a fixed
   ~1.5-2s off pool startup -- 9.6% of an 8-document run, 2.5% of a
   60-document one.
7. Measuring the **whole** corpus instead of extrapolating from a 16-PDF
   sample (2026-08-04) found the serial baseline was 55m 30s, not the
   ~39m every document had quoted -- **41% low**. Correcting it showed
   12-worker efficiency is 89%, not the 60% previously reported, and that
   `worker_ceiling()`'s `cpus // 4` clamp costs **1.41x**: 32 workers
   beat the 12 it allows.

The lesson worth carrying: every one of those steps was measured, and six
intermediate conclusions were wrong until the next measurement corrected
them -- including two that sat in the code as stated fact. `bench/` exists so that the next one is
checked too.

## Writing a script that drives the enrichment layer

`src.enrich.docling_parse.parse_corpus` and `python -m src.sync` both use
a worker pool when `[parser].workers` is above 1, and every start method
they can pick (`forkserver` or `spawn` -- see `[parser].start_method`)
re-imports the calling program's `__main__` in each worker. Any script of
your own that calls them must guard its top level:

```python
if __name__ == "__main__":
    main()
```

Without it, every worker re-runs the script on startup and the pool dies
with `BrokenProcessPool`. `scripts/enrich.py` and `src/sync.py`
are both guarded already; this only bites ad-hoc scripts, and it bites
immediately rather than subtly.

## Repository layout

```
README.md                 you are here
bench/                    parser wall-clock measurement (dev-only, not shipped) -- see "Benchmarking
                          the parser" above; corpus.json/sample*.json are generated and gitignored,
                          results/*.jsonl are committed evidence
AGENTS.md                 instructions for coding agents working in this repo -- hard invariants, install
                          notes, dev process, commit/PR/release conventions
DEVELOPER.md              this file -- test running, repo layout, open questions
DOCKER.md                 running this repo in a container (docker/Dockerfile)
docs/                     reference docs that ship in the release zip -- everything except the four
                          root-level ones above, which stay put because they're what a reader looks
                          for first
  PARALLELISM.md            parallel parse design: architecture, components, and the roadmap
  PERFORMANCE.md            what each config setting costs, measured -- the lookup-oriented companion
                            to PARALLELISM.md's design doc
  ZOTERO.md                 getting a bib file and its PDFs into the shape this pipeline expects
  CLI.md                    every command, and which interpreter each one needs
  CONFIG.md                 every setting, with config.toml.example reproduced in full
  PDF-PARSER.md             parser backend tradeoffs, and why grobid/markitdown were removed
  ARCHITECTURE.md           what runs, what each part writes, what is optional, and which interpreter
                            each command needs -- the user-facing companion to DESIGN.md
  RETRIEVAL.md              BM25 vs embeddings vs topic model: which answers what, and what to build
  DESIGN.md                 architecture and design decisions -- the rationale, not the map
  DIAGRAMS.md               the workflow drawn eleven ways; the fenced mermaid blocks are the source
  diagrams/                 the same eleven as standalone files, for use outside this repo
    *.mmd                     mermaid sources with a title line
    svg/*.svg                 rendered exports (mmdc -b white -w 1900). Exports only -- edit the
                              fenced block in DIAGRAMS.md, then re-render
  CITATION-PROVENANCE.md    what src/citation_provenance.py reports and how to read it
LICENSE                   MIT
assets/                   data files the pipeline reads at runtime, tracked and shipped
  csl/ieee.csl              the CSL style pandoc formats citations with ([render].csl default).
                          Vendored byte-identical to the CSL project's own release (CC BY-SA 3.0)
                          so it can be re-fetched and diffed -- do not edit it in place; the one
                          attribute this project needs is injected into a temp copy at render
                          time (see assets/csl/README.md and render_output._collapsed_csl)
  csl/README.md             the vendoring policy, upstream URL and sha256
.github/workflows/        ci.yml (test suite + coverage + poetry check, on push/PR) and release.yml
                          (on a v* tag: verifies tag matches pyproject.toml's version, builds
                          scripts/release.py's zip, publishes it to a GitHub Release)
config.toml.example       tracked template for the central config -- paths, parser backend, worker
                          count, embedding model. Copy to config.toml (gitignored, per-host) before
                          anything imports src.config; see docs/CONFIG.md
papers/                   gitignored, per-host data -- not shipped in the repo
  bibliography.bib          BibTeX export -- source of truth for citekeys/metadata (config.toml's [bib].path default)
  pdfs/                     [source_pdfs].dir default -- raw PDFs gathered outside the bib file, never citable;
                          manifest.json/reading-notes.md are hand-written and tracked, PDFs dropped in
                          alongside them are not (migrated here from this repo's original source-pdfs/,
                          now retired, on 2026-07-31)
pyproject.toml            Poetry config (dependency/lockfile manager only, package-mode = false --
                          no [build-system], nothing published) + pytest/coverage tool config
poetry.toml               project-local Poetry config: virtualenvs.create = false (installs into
                          whatever venv VIRTUAL_ENV points at, e.g. .venv-full/, instead of Poetry's own)
poetry.lock               resolved dependency versions -- regenerate with `poetry lock` after editing pyproject.toml
src/                      the corpus and drafting layers (sync needs bibtexparser;
                          citation_gate/references need nothing)
  config.py                 loads config.toml, env var overrides
  runlock.py                one-writer-at-a-time lock over content/, held by both entrypoints;
                          a dedicated sqlite file, so a killed holder releases it with no
                          staleness check and readers are never blocked
  bib_reader.py             parses bibliography.bib -- the only citekey source
  ledger.py                 per-citekey status tracking (content/ledger.sqlite); find_stale/prune_missing
                          detect/remove rows for citekeys no longer in the bib file. Also persists each
                          entry's formatting-relevant BibTeX fields (bib_fields, JSON), so references.py
                          can build a full bibliography entry without reading the bib file itself
  pdf_text.py               PDF text extraction, dispatched to pdftotext/docling by config.PARSER; also the parse-quality guard
  sync.py                   orchestrates the above -- the corpus-layer entrypoint; --remove-stale opts into
                          deleting stale ledger rows (default: report only, see README's "Removing a paper")
  dedup.py                  advisory near-duplicate citekey detection (shared DOI/title), called from sync
  retrieval.py              BM25 search over the corpus layer, backed by a cached term-frequency index
  passages.py               where a citekey's supporting text comes from (docling sidecar -> form-feed
                          pages -> pdftotext) and whether it may be quoted -- shared by the consumers
                          that need to point at part of a source rather than all of it
  citation_gate.py          hard citation-verification gate -- the drafting layer must pass this
  citation_coverage.py      ad-hoc review aid: retrieval-candidates-vs-actually-cited report, not a gate
  citation_provenance.py    ad-hoc review aid: what in each cited source supports the claim citing it, not a gate
                            (scores claims against passages.py's ladder; see docs/CITATION-PROVENANCE.md)
  references.py             auto-generates a draft's "## References" section from its own cited citekeys,
                          as numbered IEEE entries ordered by first appearance -- the same order (and
                          so the same numbers) pandoc's citeproc assigns when the draft is rendered
  render_output.py          Pandoc/TeX Live rendering + standalone CLI -- stdlib-only, no enrich group
                          needed, which is why it sits here and not in src/enrich/. `--format md` on a
                          Markdown draft skips pandoc entirely and emits references.numbered_markdown's
                          plain numbered copy instead
src/enrich/                the enrichment layer (pyproject.toml's "enrich" Poetry group), optional
  corpus.py                 unifies ledger items + [source_pdfs].dir's raw PDFs (doc: prefixed, non-citable),
                          skipping any that the ledger already covers
  docling_parse.py, embed_index.py, topic_model.py
scripts/
  install_full_pipeline.sh  single staged install path (os-deps/python-deps/dev-deps/all) for host + Docker
  enrich.py                 orchestrates src/enrich/* stages -- the enrichment layer's entry point
  verbatim_check.py          ad-hoc review aid: verbatim-overlap and page-locating checks against sources
  release.py                 bundles a distributable release/chitragupta-<version>.zip, dev files excluded
tests/                    pytest suite -- unit tests per module + end-to-end feature tests (see "Running tests")
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, topic_embed_cache.json, rendered/  (src/enrich/ outputs)
.claude/skills/           drafting layer: survey-writer, thesis-chapter-writer,
                          textbook-chapter-writer, tutorial-writer, deep-research
.claude/agents/           deep-research's subagents: deep-research-interviewer, deep-research-writer, peer-reviewer
.claude/hooks/            citation_gate_hook.py -- PostToolUse hook, mechanically enforces citation_gate on
                          every Write/Edit under content/drafts/*.md and *.tex (see AGENTS.md)
.claude/settings.json     wires the hook above into the PostToolUse event
docker/                   Dockerfile (TeX Live/Pandoc/Poetry) -- unverified end-to-end, see DOCKER.md
```

## Figures and copyright

With `[enrich].docling_images` on (off by default), the Docling stage writes
each paper's figure bitmaps to `content/docling/<doc>_artifacts/` and an
index of them to `content/docling/<doc>.figures.json`.

**Those images are a reading aid, not draft content.** Nothing in this
repo inserts them into `content/drafts/`, and nothing should start doing
so. A figure's copyright belongs to the publisher or the authors, and
citing a paper grants no right to reproduce its figures -- `citation_gate`
gates *citekeys*, and there is deliberately no equivalent gate for
images. The ledger also has no license column, so the pipeline genuinely
cannot tell a CC BY paper from an all-rights-reserved one; that judgment
stays with you, per figure.

The supported way to reference a figure is therefore **textually**, and
each record in `<doc>.figures.json` carries a ready-to-paste `cite`
string:

```json
{
  "page": 8,
  "caption": "Figure 3. Subdivision of the entry process of a Digital Twin",
  "cite": "Figure 3 of [@richstein_characterizing_2024], p.8",
  "image": "richstein_characterizing_2024_artifacts/image_000005_....png"
}
```

Two details worth knowing about that `cite` string:

- The number comes from the **caption's own text**, never from the
  picture's position. Publisher logos and licence badges are pictures
  too -- on a real 17-page MDPI paper, 6 of the 13 extracted pictures
  were furniture rather than figures -- so the Nth picture is routinely
  not the paper's Figure N.
- The number is captured *whole*, including chapter-scoped forms
  (`Fig. 1.1` ... `Fig. 1.4`, the convention in edited book chapters)
  and sub-figure letters (`Figure 2a`). Matching only the leading
  integer would collapse a chapter's four distinct figures onto one
  `Figure 1` -- a citation pointing at the wrong picture.
- A picture whose caption carries no number is cited by page instead
  (`"the figure on p.1 of [@key]"`), rather than being given a number
  this repo would have to invent. Two panels of one figure (captions
  beginning `(a)` / `(b)`) therefore share a page-based citation; that
  is the fallback behaving correctly, not a collision.

For a `[source_pdfs]` document the `cite` string is deliberately *not* a
`[@citekey]`, since those documents are outside the bib file and can
never be cited (AGENTS.md's citekey invariant).

## Citation provenance

`python -m src.citation_provenance content/drafts/<slug>.md` reports, for
every citation in a draft, what in the cited source supports it and where
-- ordered worst match first. It writes
`content/provenance/<slug>.provenance.md` plus `.tex`/`.pdf` renders, and
is also an enrichment stage (`--stages provenance --input <draft>`).

A **review aid, not a gate**, deliberately: matching is lexical, so it
cannot tell "the source doesn't say this" from "the source says it in
words I didn't recognise". `citation_gate` blocks because it checks
something exact (ledger membership); this reports because it doesn't.

Passage quality depends on what has been parsed. With the Docling stage
run, `content/docling/<citekey>.passages.json` supplies reading-ordered
paragraphs and the report quotes them. Without it, `pdftotext` output is
used and the report gives a page number **without quoting** -- on a
two-column paper that text splices two columns onto every line, so any
excerpt would be a collage of two arguments.

Full design rationale, including the measurements behind those choices:
[docs/CITATION-PROVENANCE.md](docs/CITATION-PROVENANCE.md).

## Open questions and unbuilt features

Run this pipeline as a cron job monitoring the bib file. To do so,
the following tasks need to be completed in priority order:

1. **Bib-file freshness is the blocker, not an afterthought.** With no
   continuous auto-export, `bibliography.bib` is a manual, point-in-time
   snapshot -- a cron job watching only its mtime does nothing until a
   human re-exports it.
2. **No scheduling mechanism exists yet** -- no crontab entry, no systemd
   timer. Given `sync` is already cheap and idempotent, a stateless cron
   entry polling every N minutes is the right shape (survives reboots
   without supervision) rather than a long-running watchdog daemon.
3. **No log file / failure surfacing.** `sync` prints to stdout/stderr;
   unattended it needs redirecting to a log (with rotation) and a way to
   notice repeated failures, since cron's default "mail root" often goes
   unread.
4. **Cron's minimal environment.** A crontab entry needs the venv's
   Python invoked by absolute path
   (`/workspace/git/chitragupta/.venv-full/bin/python`) -- cron
   doesn't source your shell profile or activate venvs.

### ~~The corpus layer throws away Docling's document model~~ **Done.**

With `[parser].backend = "docling"`, `pdf_text._extract_docling` used to
build a full `DoclingDocument` -- page numbers, bounding boxes, semantic
labels on every text item -- keep only `export_to_markdown()`, and let the
rest be garbage-collected. The passage ladder then fell past the
page-level rung (Markdown has no form feeds, so the split yielded one
page) to a fresh `pdftotext` run, which meant the best parser produced the
worst passages.

Both halves are now kept, and the fix was the two changes this note
proposed:

1. **`export_to_markdown(page_break_placeholder="\f")`** puts the page
   boundaries back into `content/parsed/<citekey>.txt`, so it has the same
   shape as `pdftotext`'s output and `verbatim_check.py locate` reports a
   real page. Checked against the installed `docling-core` 2.89.0 on a
   real 51-page paper: 51 pages in the model, 51 form-feed segments in the
   file. `\f` is whitespace, so BM25 tokenisation and
   `run_together_ratio` are unaffected.
2. **`passage_records()` moved into `src/passages.py`**, which is
   stdlib-only, is already the sidecar's reader, and describes a Docling
   document through `getattr` alone -- so it satisfies the dependency
   direction (`src/enrich/` imports the core, never the reverse) while
   letting both writers share one definition. The corpus layer writes
   `content/parsed/<citekey>.passages.json`; the enrichment layer keeps
   writing its own under `content/docling/`, keyed by doc_id. The corpus
   layer's is cleared *before* every parse, so a switch back to
   `pdftotext`, a failed parse and a re-parse all leave no stale sidecar.

The third item -- sharing the work with the enrichment stage -- shipped
too, in the only direction this repository permits. `docling_parse`
adopts the corpus layer's parse for a citekey (a file copy, against 6.65s
per document) rather than the corpus layer being reshaped to satisfy the
enrichment cache. It refuses for a `papers/pdfs/` document, for a run with
`DOCLING_IMAGES` on, and for artefacts older than their PDF. The one
remaining gap is stated in `_corpus_parse_available`'s docstring: it
cannot tell which `[parser].ocr` setting produced the corpus text, but
that staleness already exists in `content/parsed/` and has the same fix
(`python -m src.sync --reparse`).

### `content/topics.json` has no consumer

`src/enrich/topic_model.py` writes it and nothing reads it -- no module, no
genre skill. `survey-writer` groups themes by judgement and says so
explicitly ("With a small corpus there's no BERTopic step"). That is
defensible today: clustering is whole-corpus, so assignments are not
stable between runs, and on a small corpus every document legitimately
lands in the outlier topic. If it is ever wired in, `survey-writer`'s
"Cluster by judgment" step is the seam, gated on the file existing and on
there being non-`-1` assignments -- the same shape the existing skills use
to gate on `content/chroma/`.

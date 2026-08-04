# Developer guide

Material for working on this repository itself, as opposed to using it to
draft content -- test running, the full source layout, and known gaps.
See [README.md](README.md) for the user-facing Quickstart/Configuration/
Architecture docs and [DOCKER.md](DOCKER.md) for the container build.

## Table of contents

- [Running tests](#running-tests)
- [Benchmarking the parser](#benchmarking-the-parser)
- [Writing a script that drives the heavy pipeline](#writing-a-script-that-drives-the-heavy-pipeline)
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

`tests/` covers both the core pipeline and `src/heavy/*` -- heavy
dependencies (docling, chromadb, bertopic,
sentence-transformers) are mocked via `sys.modules` for fast,
deterministic unit tests, so the
`dev-deps` group alone is *not* enough on its own: the `heavy` group
(`python-deps`, step 1 of Quickstart) must already be installed too, since
`tests/test_bib_reader.py` needs `bibtexparser` and the `src/heavy/` test
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
- [docs/PARALLELISM.md](docs/PARALLELISM.md) -- the whole story across
  seven releases, including the six conclusions that turned out wrong
- [bench/README.md](bench/README.md) -- how to run it, and what each
  switch measures
- [bench/RESULTS.md](bench/RESULTS.md) -- the 2026-08-02 baseline, with
  raw per-PDF timings in `bench/results/`
- [bench/PARALLELISM-PLAN.md](bench/PARALLELISM-PLAN.md) -- the phased
  plan that measurement produced

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

## Writing a script that drives the heavy pipeline

`src.heavy.docling_parse.parse_corpus` and `python -m src.sync` both use
a worker pool when `[parser].workers` is above 1, and every start method
they can pick (`forkserver` or `spawn` -- see `[parser].start_method`)
re-imports the calling program's `__main__` in each worker. Any script of
your own that calls them must guard its top level:

```python
if __name__ == "__main__":
    main()
```

Without it, every worker re-runs the script on startup and the pool dies
with `BrokenProcessPool`. `scripts/full_pipeline.py` and `src/sync.py`
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
  PARALLELISM.md            how the parser got 22x faster across eight releases, and what it cost
  PERFORMANCE.md            what each config setting costs, measured -- the lookup-oriented companion
                            to PARALLELISM.md's narrative
  ZOTERO.md                 getting a bib file and its PDFs into the shape this pipeline expects
  CLI.md                    every command, and which interpreter each one needs
  CONFIG.md                 every setting, with config.toml.example reproduced in full
  PDF-PARSER.md             parser backend tradeoffs, and why grobid/markitdown were removed
  DESIGN.md                 architecture and design decisions
  CITATION-PROVENANCE.md    what src/citation_provenance.py reports and how to read it
LICENSE                   MIT
.github/workflows/        ci.yml (test suite + coverage + poetry check, on push/PR) and release.yml
                          (on a v* tag: verifies tag matches pyproject.toml's version, builds
                          scripts/release.py's zip, publishes it to a GitHub Release)
config.toml.example       tracked template for the central config -- paths, parser backend, worker
                          count, embedding model. Copy to config.toml (gitignored, per-host) before
                          anything imports src.config; see README's "Configuration"
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
src/                      core pipeline (needs bibtexparser; citation_gate/references need nothing)
  config.py                 loads config.toml, env var overrides
  runlock.py                one-writer-at-a-time lock over content/, held by both entrypoints;
                          a dedicated sqlite file, so a killed holder releases it with no
                          staleness check and readers are never blocked
  bib_reader.py             parses bibliography.bib -- the only citekey source
  ledger.py                 per-citekey status tracking (content/ledger.sqlite); find_stale/prune_missing
                          detect/remove rows for citekeys no longer in the bib file
  pdf_text.py               PDF text extraction, dispatched to pdftotext/docling by config.PARSER; also the parse-quality guard
  sync.py                   orchestrates the above -- the "job 1" entrypoint; --remove-stale opts into
                          deleting stale ledger rows (default: report only, see README's "Removing a paper")
  dedup.py                  advisory near-duplicate citekey detection (shared DOI/title), called from sync
  retrieval.py              BM25 search over the content layer, backed by a cached term-frequency index
  citation_gate.py          hard citation-verification gate -- "job 2" must pass this
  citation_coverage.py      ad-hoc review aid: retrieval-candidates-vs-actually-cited report, not a gate
  citation_provenance.py    ad-hoc review aid: what in each cited source supports the claim citing it, not a gate
                            (see docs/CITATION-PROVENANCE.md)
  references.py             auto-generates a draft's "## References" section from its own cited citekeys
src/heavy/                optional heavier pipeline (pyproject.toml's "heavy" Poetry group)
  corpus.py                 unifies ledger items + [source_pdfs].dir's raw PDFs (doc: prefixed, non-citable)
  docling_parse.py, embed_index.py, topic_model.py
  render_output.py          Pandoc/TeX Live rendering + standalone CLI -- stdlib-only, no heavy venv needed
scripts/
  install_full_pipeline.sh  single staged install path (os-deps/python-deps/dev-deps/all) for host + Docker
  full_pipeline.py           orchestrates src/heavy/* stages
  verbatim_check.py          ad-hoc review aid: verbatim-overlap and page-locating checks against sources
  release.py                 bundles a distributable release/automated-research-<version>.zip, dev files excluded
tests/                    pytest suite -- unit tests per module + end-to-end feature tests (see "Running tests")
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, topic_embed_cache.json, rendered/  (src/heavy/ outputs)
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer,
                          textbook-chapter-writer, tutorial-writer, deep-research
.claude/agents/           deep-research's subagents: deep-research-interviewer, deep-research-writer, peer-reviewer
.claude/hooks/            citation_gate_hook.py -- PostToolUse hook, mechanically enforces citation_gate on
                          every Write/Edit under content/drafts/*.md and *.tex (see AGENTS.md)
.claude/settings.json     wires the hook above into the PostToolUse event
docker/                   Dockerfile (TeX Live/Pandoc/Poetry) -- unverified end-to-end, see DOCKER.md
```

## Figures and copyright

With `[heavy].docling_images` on (off by default), the Docling stage writes
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
is also a heavy-pipeline stage (`--stages provenance --input <draft>`).

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
2. ~~The heavy stages have no incremental skip logic.~~ **Done.**
   `src/heavy/embed_index.py`'s `build_index()` skips `model.encode()`
   for docs whose text hash is unchanged since the last call;
   `src/heavy/topic_model.py` caches per-doc whole-text embeddings the
   same way, re-clustering the full corpus but only re-encoding changed
   docs. `src/heavy/docling_parse.py`'s `parse_doc()`/`parse_corpus()`
   now skip a PDF whose `(size, mtime_ns)` is unchanged since the last
   call (cached in `config.DOCLING_CACHE_PATH`) and whose output file
   still exists -- closing the one stage this item used to call out as
   still reprocessing every PDF on every call (373 seconds for 5 PDFs,
   documented above).
3. **No scheduling mechanism exists yet** -- no crontab entry, no systemd
   timer. Given `sync` is already cheap and idempotent, a stateless cron
   entry polling every N minutes is the right shape (survives reboots
   without supervision) rather than a long-running watchdog daemon.
4. ~~**No lock file.**~~ **Done.** `src/runlock.py` holds a
   one-writer-at-a-time lock over `content/` for the whole of `sync` and
   `full_pipeline`; a second run exits 2 immediately. It is a dedicated
   sqlite file rather than a PID file, so a killed holder releases it
   with no staleness heuristic, and readers are never blocked.
5. **No log file / failure surfacing.** `sync` prints to stdout/stderr;
   unattended it needs redirecting to a log (with rotation) and a way to
   notice repeated failures, since cron's default "mail root" often goes
   unread.
6. **Cron's minimal environment.** A crontab entry needs the venv's
   Python invoked by absolute path
   (`/workspace/git/automated-research/.venv-full/bin/python`) -- cron
   doesn't source your shell profile or activate venvs.

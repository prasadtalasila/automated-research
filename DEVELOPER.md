# Developer guide

Material for working on this repository itself, as opposed to using it to
draft content -- test running, the full source layout, and known gaps.
See [README.md](README.md) for the user-facing Quickstart/Configuration/
Architecture docs, [DOCKER.md](DOCKER.md) for the container build, and
[GROBID.md](GROBID.md) for building GROBID standalone.

## Table of contents

- [Running tests](#running-tests)
- [Repository layout](#repository-layout)
- [Open questions and unbuilt features](#open-questions-and-unbuilt-features)

## Running tests

```bash
# Install pytest/pytest-cov into the same venv (run python-deps first)
bash scripts/install_full_pipeline.sh dev-deps

# Run the full suite with coverage
.venv-full/bin/python -m pytest --cov=src --cov=scripts --cov-report=term-missing
```

`tests/` covers both the core pipeline and `src/heavy/*` -- heavy
dependencies (docling, chromadb, bertopic, sentence-transformers) are
mocked via `sys.modules` for fast, deterministic unit tests, so the
`dev-deps` group alone is *not* enough on its own: the `heavy` group
(`python-deps`, step 1 of Quickstart) must already be installed too, since
`tests/test_heavy_grobid_extract.py` needs `requests` and
`tests/test_bib_reader.py` needs `bibtexparser`. A handful of tests
(`tests/test_feature_workflows.py`, the `TestRenderReal`/`TestExtractTextReal`
classes elsewhere) run the real `pdftotext`/`pandoc`/`pdflatex` binaries
end to end rather than mocking them, and skip automatically if those
aren't on `PATH`.

## Repository layout

```
README.md                 you are here
AGENTS.md                 instructions for coding agents working in this repo -- hard invariants, install
                          notes, dev process, commit/PR/release conventions
DEVELOPER.md              this file -- test running, repo layout, open questions
DOCKER.md                 running this repo in a container (docker/Dockerfile + docker/setup.sh)
GROBID.md                 building/running GROBID standalone on a bare host, step by step
LICENSE                   MIT
.github/workflows/        ci.yml (test suite + coverage + poetry check, on push/PR) and release.yml
                          (on a v* tag: verifies tag matches pyproject.toml's version, builds
                          scripts/release.py's zip, publishes it to a GitHub Release)
config.toml               central config -- paths, GROBID URL/timeouts, embedding model (see README's "Configuration")
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
  bib_reader.py             parses bibliography.bib -- the only citekey source
  ledger.py                 per-citekey status tracking (content/ledger.sqlite)
  pdf_text.py               pdftotext wrapper
  sync.py                   orchestrates the above -- the "job 1" entrypoint
  dedup.py                  advisory near-duplicate citekey detection (shared DOI/title), called from sync
  retrieval.py              keyword search over the content layer
  citation_gate.py          hard citation-verification gate -- "job 2" must pass this
  citation_coverage.py      ad-hoc review aid: retrieval-candidates-vs-actually-cited report, not a gate
  references.py             auto-generates a draft's "## References" section from its own cited citekeys
src/heavy/                optional heavier pipeline (pyproject.toml's "heavy" Poetry group)
  corpus.py                 unifies ledger items + [source_pdfs].dir's raw PDFs (doc: prefixed, non-citable)
  docling_parse.py, embed_index.py, topic_model.py, grobid_extract.py
  render_output.py          Pandoc/TeX Live rendering + standalone CLI -- stdlib-only, no heavy venv needed
scripts/
  install_full_pipeline.sh  single staged install path (os-deps/python-deps/grobid/dev-deps/all) for host + Docker
  full_pipeline.py           orchestrates src/heavy/* stages
  verbatim_check.py          ad-hoc review aid: verbatim-overlap and page-locating checks against sources
  release.py                 bundles a distributable release/automated-research-<version>.zip, dev files excluded
tests/                    pytest suite -- unit tests per module + end-to-end feature tests (see "Running tests")
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, topic_embed_cache.json, rendered/  (src/heavy/ outputs)
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer, tutorial-writer, deep-research
.claude/agents/           deep-research's subagents: deep-research-interviewer, deep-research-writer, peer-reviewer
docker/                   Dockerfile + setup.sh (GROBID/TeX Live/Pandoc/Poetry) -- unverified end-to-end, see DOCKER.md
```

## Open questions and unbuilt features

Run this pipeline as a cron job monitoring the bib file. To do so,
the following tasks need to be completed in priority order:

1. **Bib-file freshness is the blocker, not an afterthought.** With no
   continuous auto-export, `bibliography.bib` is a manual, point-in-time
   snapshot -- a cron job watching only its mtime does nothing until a
   human re-exports it.
2. ~~The heavy stages have no incremental skip logic.~~ **Done for
   embed/bertopic** (`src/heavy/embed_index.py`'s `build_index()` skips
   `model.encode()` for docs whose text hash is unchanged since the last
   call; `src/heavy/topic_model.py` caches per-doc whole-text embeddings
   the same way, re-clustering the full corpus but only re-encoding
   changed docs). **Docling is still not incremental** -- `full_pipeline.py
   --stages docling` reprocesses every PDF on every call (373 seconds for
   5 PDFs, documented above), the same problem this item originally
   described, just not yet fixed for that one stage.
3. **No scheduling mechanism exists yet** -- no crontab entry, no systemd
   timer. Given `sync` is already cheap and idempotent, a stateless cron
   entry polling every N minutes is the right shape (survives reboots
   without supervision) rather than a long-running watchdog daemon.
4. **No lock file.** If a run takes longer than the cron interval, two
   overlapping `sync` invocations aren't currently prevented.
5. **No log file / failure surfacing.** `sync` prints to stdout/stderr;
   unattended it needs redirecting to a log (with rotation) and a way to
   notice repeated failures, since cron's default "mail root" often goes
   unread.
6. **Cron's minimal environment.** A crontab entry needs the venv's
   Python invoked by absolute path
   (`/workspace/git/automated-research/.venv-full/bin/python`) -- cron
   doesn't source your shell profile or activate venvs.

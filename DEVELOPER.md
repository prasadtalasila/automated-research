# Developer guide

Material for working on this repository itself, as opposed to using it to
draft content -- test running, the full source layout, and known gaps.
See [README.md](README.md) for the user-facing Quickstart/Configuration/
Architecture docs and [DOCKER.md](DOCKER.md) for the container build.

## Table of contents

- [Running tests](#running-tests)
- [Repository layout](#repository-layout)
- [Figures and copyright](#figures-and-copyright)
- [Open questions and unbuilt features](#open-questions-and-unbuilt-features)

## Running tests

```bash
# Install pytest/pytest-cov into the same venv (run python-deps first)
bash scripts/install_full_pipeline.sh dev-deps

# Run the full suite with coverage
.venv-full/bin/python -m pytest --cov=src --cov=scripts --cov-report=term-missing
```

`tests/` covers both the core pipeline and `src/heavy/*` -- heavy
dependencies (docling, markitdown, chromadb, bertopic,
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

## Repository layout

```
README.md                 you are here
AGENTS.md                 instructions for coding agents working in this repo -- hard invariants, install
                          notes, dev process, commit/PR/release conventions
DEVELOPER.md              this file -- test running, repo layout, open questions
DOCKER.md                 running this repo in a container (docker/Dockerfile)
LICENSE                   MIT
.github/workflows/        ci.yml (test suite + coverage + poetry check, on push/PR) and release.yml
                          (on a v* tag: verifies tag matches pyproject.toml's version, builds
                          scripts/release.py's zip, publishes it to a GitHub Release)
config.toml               central config -- paths, parser backend, embedding model (see README's "Configuration")
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
  ledger.py                 per-citekey status tracking (content/ledger.sqlite); find_stale/prune_missing
                          detect/remove rows for citekeys no longer in the bib file
  pdf_text.py               PDF text extraction, dispatched to pdftotext/markitdown/docling by config.PARSER
  sync.py                   orchestrates the above -- the "job 1" entrypoint; --remove-stale opts into
                          deleting stale ledger rows (default: report only, see README's "Removing a paper")
  dedup.py                  advisory near-duplicate citekey detection (shared DOI/title), called from sync
  retrieval.py              BM25 search over the content layer, backed by a cached term-frequency index
  citation_gate.py          hard citation-verification gate -- "job 2" must pass this
  citation_coverage.py      ad-hoc review aid: retrieval-candidates-vs-actually-cited report, not a gate
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
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer, tutorial-writer, deep-research
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

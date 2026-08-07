# DEVELOPER-AGENTS.md

Guidance for coding agents (and anyone else) **working on this repository
itself**, as opposed to using it to draft content. The user-facing half is
[AGENTS.md](AGENTS.md); the why behind both is [SOUL.md](SOUL.md).

[AGENTS.md](AGENTS.md)'s citekey invariant binds code here too: no module
may generate, guess or rewrite a citekey, and no new check may be promoted
into a gate beside `src/citation_gate.py`.

## Role

This assistant manages most of the day-to-day development here: implementing
features, writing tests first, running the full local check suite, opening
PRs, watching CI, merging, and cutting releases. Proceed autonomously through
that whole cycle for a routine code change rather than pausing to check in at
each step -- reserve pausing for decisions that are genuinely irreversible
(force-pushes, history rewrites, deleting something not obviously
regenerable) or genuinely ambiguous (a requirement with more than one
reasonable reading and no clear tie-breaker in this file or the code).

## Environment constraints on this host

`pip install` outside a venv is blocked (PEP 668) -- unconditionally, on
every host, regardless of root access. **This matters for the corpus
layer too**: `python -m src.sync` needs `bibtexparser` (parsing
`bibliography.bib` correctly -- nested braces, LaTeX escapes -- isn't
worth hand-rolling), so it must be run via the installed venv, not the
bare system interpreter. `python -m src.citation_gate` is the exception --
it only reads `content/ledger.sqlite` (stdlib `sqlite3`) and still runs
with bare `python3`.

**Probe for a toolchain; never assume one, in either direction.** An
earlier revision of this file asserted that root, TeX Live and Pandoc were
unavailable here, and a later one asserted the opposite. Both were one
machine's facts written as project rules, and both went stale. The
durable rule is the probe:

- **When the enrichment layer's dependencies are present:** stages that need them
  (Docling parsing; Pandoc/TeX Live rendering) work directly on the host,
  not only inside `docker/` -- there is nothing docker-exclusive about
  any of them.
- **When they're absent:** don't hang, stack-trace, or silently skip
  without saying so. Every `src/enrich/*` stage already self-probes its
  own prerequisites and reports honestly (`ok`/`skipped`/`missing-binary`)
  via `scripts/enrich.py` rather than assuming the target implies
  availability -- keep any new stage consistent with that pattern instead
  of inventing a new fallback policy.

Install everything with:
```
bash scripts/install_full_pipeline.sh              # Python deps only (default) -- what every host needs regardless of OS packages
bash scripts/install_full_pipeline.sh os-deps      # apt-get: TeX Live, Pandoc, poppler-utils, Poetry, zip/unzip -- needs root, opt-in
bash scripts/install_full_pipeline.sh dev-deps     # pytest/pytest-cov, to run the test suite -- opt-in
bash scripts/install_full_pipeline.sh all          # os-deps + python-deps
```
This is **the single install script for both the host and Docker and CI**
-- `docker/Dockerfile` calls it once per stage as separate `RUN` lines, and
`.github/workflows/ci.yml` calls it directly too, rather than any of them
having their own separate apt-get/pip/poetry install logic. Python
dependencies are managed by Poetry as a lockfile/venv manager only
(`package-mode = false` in `pyproject.toml` -- nothing here is published
or pip-installable). If you find a dependency-order issue, fix it once in
`pyproject.toml` (+ `poetry lock` to update `poetry.lock`) and every
target picks it up. Don't add a second install path.

`docker/` (Dockerfile) builds the same TeX Live/Pandoc stack inside a
container instead, for hosts where the
`os-deps` assumption above doesn't hold (no root, or root deliberately
withheld). **It has still not been built or run in this environment** (no
Docker daemon here) -- treat it as a draft to validate, not a tested
artifact.

## The enrichment layer (`src/enrich/`, `scripts/enrich.py`)

Implements Docling -> sentence-transformers/Chroma ->
BERTopic -> Pandoc/LaTeX, one script for both host and Docker. Each stage
self-probes its own prerequisites (pandoc/pdflatex on PATH) and
reports honestly (`skipped`/`missing-binary`) rather than assuming the
target implies availability -- don't "fix" a skip by hardcoding
target-specific behavior; fix the probe if it's wrong. `--target
host|docker` is **informational only** for exactly that reason: the
probes decide, not the flag, so nothing branches on it.

`src/enrich/embed_index.py`, `src/enrich/topic_model.py`, and
`src/enrich/docling_parse.py` are all incremental, mirroring
`src/ledger.py`'s own skip-what-hasn't-changed logic for the corpus
layer: a doc whose text hasn't changed since the last run isn't
re-embedded, and a PDF whose `(size, mtime_ns)` hasn't changed since the
last run (`config.DOCLING_CACHE_PATH`) isn't re-parsed by Docling.

No stage in this pipeline calls out to an LLM or needs an API key --
Docling, embeddings/Chroma, BERTopic, and the Pandoc/LaTeX render
step are all local/deterministic. Any LLM-backed synthesis happens only
via the `.claude/skills/` drafting layer, invoked through a Claude Code
session rather than a standalone API call.

`src/enrich/corpus.py` sources the enrichment corpus from the ledger and
nothing else, so every document it yields is citable and
keyed by its citekey alone. Keep it that way -- the enrichment layer must never
index a document a draft would not be allowed to cite. If a paper is
worth enriching, it belongs in the reference manager: catalogue it,
re-export, and re-run `python -m src.sync`.

A second, uncitable source was tried and removed in 3.3.0 because it made
every stage downstream carry a permanently non-citable case; the citekey
became the whole identity in 3.3.1. `src/enrich/corpus.py`'s module
docstring has the detail. Don't reintroduce a second id namespace.

## Conventions a new stage has to follow

Three, each learned from a bug rather than chosen:

- **Anything holding the write lock reports per document.** DESIGN.md's
  concurrency policy requires a serial section to be observably making
  progress, and an unreported one is indistinguishable from a hang -- a
  correct run was read as stuck and killed at 399 of 501 documents
  (#50). `sync`, `docling_parse` and `embed_index` all emit
  `[done/total] <citekey>`, opened *before* the slow call so the reader
  sees the document currently under way rather than the last one that
  finished. Where it goes differs, and the split is deliberate (3.4.0):
  `sync`'s stdout is a documented contract -- bibliography order,
  diffable between runs, pinned by tests -- so progress and warnings go
  through `logging` to `logs/sync.log` instead, while the enrichment
  stages still `print(..., flush=True)`. Flush anything printed: stdout
  is block-buffered when it isn't a terminal, and the tail of an
  interrupted run is the part worth keeping.
- **Classify a failure by cause on the exception, not by matching its
  message.** `pdf_text.py` sets `transient` and `timed_out` marks that
  survive the pool's pickling; `sync` reports each cause separately
  because they want opposite fixes (raise the timeout and `--reparse`
  versus fix or remove the PDF). Adding a cause means adding a mark, not
  a string match.
- **Report a partial result as a failure.** Docling's `PARTIAL_SUCCESS`
  returns a document that stops early; writing it would hand the citation
  gate a source that silently ends at page k of n. Check the status and
  raise *before* anything is written, so nothing enters the incremental
  cache.

## Development process: agile, test-driven

Work in small, independently-shippable increments -- prefer several small,
reviewable PRs over one large one, and prefer a working, tested slice of a
feature over a complete-but-untested one. Within each increment, follow
test-driven development:

1. Write a failing test that captures the behavior being added or the bug
   being fixed, and confirm it actually fails (a test that passes before
   the fix exists isn't testing anything).
2. Write the minimum implementation that makes it pass.
3. Refactor with the test suite green, if the result needs cleaning up.

This applies to bug fixes as much as features: "fix the bug" becomes
"write a test that reproduces it, then make it pass" -- don't fix
something you can't first demonstrate is broken. Exception: exploratory
spikes to understand a problem before committing to an approach don't
need up-front tests, but the resulting real change does.

## Before claiming a task complete: run all local checks

Never report a task as done on the strength of a plan or a code read alone.
Before saying so, actually run, in this repo:

- The full test suite with coverage: `.venv-full/bin/python -m pytest
  --cov=src --cov=scripts --cov-report=term-missing`. This repo maintains
  100% line and branch coverage -- a change that drops it needs a test
  added, not a lowered bar.
- `poetry check`.
- At least one real end-to-end smoke test that exercises the actual
  change against real dependencies, not only its mocked unit tests --
  e.g. if you touch a CLI script, run it for real; if you touch
  `src/enrich/*` and the `enrich` Poetry group is installed, run it against
  the real sentence-transformers/chromadb/bertopic stack, not just
  `sys.modules`-mocked fakes. Unit tests catch regressions in logic;
  smoke tests catch wrong assumptions about how the real library actually
  behaves (this project's test suite has caught real fake-vs-real
  behavior drift this way before -- see `tests/test_enrich_embed_index.py`
  and `tests/test_enrich_topic_model.py`'s own comments).

Only once all of the above are green does a task count as complete.

## Commit messages

Title line: imperative mood, concise, describes the change's effect (not
"updated files" or "misc fixes"). PRs are squash-merged (see "Pull
requests" below), and GitHub uses the PR's title as the resulting commit
title on `main`, appending the PR number automatically (e.g. `Fix reconcile
drift detection (#42)`) -- so write commits and PR titles as if either one
could become that commit title, and don't add the number by hand.

Body: a blank line, then a bulleted list of the specific, concrete changes,
each bullet starting with a present-tense verb (Fix, Add, Remove, Migrate,
Upgrade) and naming what actually changed, not vague summaries. No
preamble paragraph before the bullets. For example (style, not this repo's
literal content):

```
Fix reconcile drift detection, secret handling, and stale config warnings

- Fix reconcile to detect and reprovision users whose containers are gone,
  refuse `--fix` when Docker is unreachable or `--output-dir` differs
  from cwd.
- Restore secret-file exclusion in build.py, consolidate
  SECRET_FILENAMES, and chmod secret files 0600 unconditionally.
- Warn on stale root `.env` from install/update/generate paths; remove
  dead code no longer reachable after the above.
```

## Issues and pull requests

Templates live in `.github/`, where GitHub picks them up automatically --
don't restate their section list here:

- `.github/pull_request_template.md` -- auto-populates every new PR.
- `.github/ISSUE_TEMPLATE/bug_report.md` and
  `.github/ISSUE_TEMPLATE/feature_request.md`.

A PR title is held to the same bar as a commit's title line: concise,
describes the effect. The template's **Test plan** section is not a
formality -- fill it from what you actually ran (see "Before claiming a
task complete" above), not from what you intended to run.

Merge method: squash. Each PR becomes exactly one commit on `main`, titled
from the PR title (see "Commit messages" above) -- keep the PR title
accurate even when the branch itself carries several intermediate
commits.

## Versioning and releases

Semantic versioning (`pyproject.toml`'s `[tool.poetry].version`), bumped
according to the most significant change in the release, not the number
of commits:

- **PATCH** (x.y.Z): bug fixes, documentation-only changes, CI/workflow-only
  changes, test-only additions -- nothing that changes what the pipeline
  does or how it's invoked.
- **MINOR** (x.Y.0): new backward-compatible functionality -- a new
  script, a new `src/` module, a new optional config key, a performance
  improvement that doesn't change output shape.
- **MAJOR** (X.0.0): breaking changes -- anything that changes an
  existing citekey/output format, removes or renames a `config.toml` key
  without a fallback, changes a CLI's argument shape, or otherwise
  requires an existing user to change how they invoke or configure the
  pipeline.

Release notes go in the GitHub Release body, not the git tag message.
`.github/RELEASE_TEMPLATE.md` has the shape to follow -- GitHub does *not*
pick that file up automatically, so copy from it by hand when drafting a
release.

## Shipping a code change: the full cycle

Any change that touches code (not a docs-only change) goes through the
complete cycle, and isn't done until every step below has actually
succeeded -- not merely started:

1. Branch off `main`, commit (see "Commit messages" above), push.
2. Decide the version bump (see "Versioning and releases") and update
   `pyproject.toml` as part of the same branch -- `release.yml` verifies
   the pushed tag against `pyproject.toml`'s version on `main`, so the
   bump has to land *before* the tag exists, i.e. in this PR, not after.
3. Open a PR against `main` (see "Issues and pull requests" above).
4. Wait for `.github/workflows/ci.yml` to complete on the PR and confirm
   it's green -- if it fails, fix the actual cause (see "Before claiming a
   task complete") and push again; don't merge past a red check.
5. Request review from Copilot, resolve every issue it identifies, and
   mark each as resolved; iterate (push a fix, let it re-review) until
   none remain. Use judgement on a genuinely trivial finding rather than
   treating every comment as mandatory -- but "trivial" means actually
   inconsequential (a wording nit), not "inconvenient to fix."
6. Squash-merge the PR.
7. Tag `v<version>` (matching what's now in `main`'s `pyproject.toml`) and
   push the tag.
8. Confirm `.github/workflows/release.yml` completed and the resulting
   GitHub Release has its `chitragupta-<version>.zip` asset
   attached -- this is the actual deliverable, not the tag or the merge
   by itself.

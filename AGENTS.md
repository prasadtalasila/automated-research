# AGENTS.md

Guidance for coding agents (and anyone else) working in this repository.

## Role

This assistant manages most of the day-to-day development here: implementing
features, writing tests first, running the full local check suite, opening
PRs, watching CI, merging, and cutting releases. Proceed autonomously through
that whole cycle for a routine code change rather than pausing to check in at
each step -- reserve pausing for decisions that are genuinely irreversible
(force-pushes, history rewrites, deleting something not obviously
regenerable) or genuinely ambiguous (a requirement with more than one
reasonable reading and no clear tie-breaker in this file or the code).

## The hard invariant: never fabricate a citekey

Fabricated placeholder references have made it into real papers before --
that is the failure mode this pipeline is built to prevent.

Rule: a citekey may only be used if it appears in `papers/bibliography.bib`
(source of truth -- see below) and was picked up into `content/ledger.sqlite`
by `python -m src.sync`. If a citation would help an argument but isn't in
the bib file, say so in prose -- do not invent a key for it, and do not
"fix" a gate failure by making up a plausible-looking key instead of
removing the claim or sourcing a real one.

Every genre skill (`survey-writer`, `thesis-chapter-writer`, `tutorial-writer`
in `.claude/skills/`) must run `python -m src.citation_gate <file>` on its own
output and only present the draft once it exits 0. This is a gate, not a
lint suggestion -- treat a `FAIL` the same way you'd treat a failing test.

## The bib file is the source of truth (not this pipeline)

`papers/bibliography.bib` (path configurable via `config.toml`'s
`[bib].path` or the `BIB_FILE` env var; gitignored, per-host data -- see
"Configuration" in README.md) is a manual export from your reference
manager's BibTeX export feature -- no auto-sync plugin is installed, so it
is not continuously auto-synced. Whatever citekey BibTeX assigns there
(e.g. `talasila_composable_2025`, or `noauthor_digital_nodate` for an item
with no discoverable author) is the citekey everywhere downstream.
`src/bib_reader.py` parses it and is the only place that reads it; nothing
else should ever generate or guess a citekey.

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
  file's `file` field) -> advisory duplicate-citekey check (`src/dedup.py`).
  No LLM calls, no judgment calls, idempotent. Safe to run unattended or on
  a schedule.
- **Job 2 -- generative drafting** (the three `.claude/skills/`, or the
  heavier `scripts/full_pipeline.py` stages): invoked on demand, reviewed by
  the user. These read the content layer; they never write to
  `content/ledger.sqlite` directly (only `sync` does).
- **Ad-hoc review aids** (`scripts/verbatim_check.py`,
  `src/citation_coverage.py`): neither job -- run by hand when reviewing a
  draft, never invoked automatically, never gate anything.

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
  GROBID.md for the exact recipe and failure mode.
- **When they're absent:** don't hang, stack-trace, or silently skip
  without saying so. Every `src/heavy/*` stage already self-probes its
  own prerequisites and reports honestly (`ok`/`skipped`/`missing-binary`)
  via `scripts/full_pipeline.py` rather than assuming the target implies
  availability -- keep any new stage consistent with that pattern instead
  of inventing a new fallback policy.

Install everything with:
```
bash scripts/install_full_pipeline.sh              # Python deps only (default) -- what every host needs regardless of OS packages
bash scripts/install_full_pipeline.sh os-deps      # apt-get: JDK 21, TeX Live, Pandoc, poppler-utils, Poetry, zip/unzip -- needs root, opt-in
bash scripts/install_full_pipeline.sh grobid       # fetch + build GROBID standalone -- multi-GB, opt-in
bash scripts/install_full_pipeline.sh dev-deps     # pytest/pytest-cov, to run the test suite -- opt-in
bash scripts/install_full_pipeline.sh all          # os-deps + python-deps (not grobid -- too heavy to bundle by default)
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

`docker/` (Dockerfile + `docker/setup.sh`) builds the same GROBID/TeX
Live/Pandoc stack inside a container instead, for hosts where the
`os-deps` assumption above doesn't hold (no root, or root deliberately
withheld). **It has still not been built or run in this environment** (no
Docker daemon here) -- treat it as a draft to validate, not a tested
artifact.

## The heavy pipeline (`src/heavy/`, `scripts/full_pipeline.py`)

Implements Docling -> GROBID -> sentence-transformers/Chroma ->
BERTopic -> Pandoc/LaTeX, one script for both host and Docker
(`scripts/full_pipeline.py --target host|docker`). Each stage self-probes
its own prerequisites (reachable GROBID, pandoc/pdflatex on PATH) and
reports honestly (`skipped`/`missing-binary`) rather than assuming the
target implies availability -- don't "fix" a skip by hardcoding
target-specific behavior; fix the probe if it's wrong.

`src/heavy/embed_index.py` and `src/heavy/topic_model.py` are
incremental, mirroring `src/ledger.py`'s own content-hash skip logic for
the core pipeline: a doc whose text hasn't changed since the last run
isn't re-embedded. Docling itself (`src/heavy/docling_parse.py`) is not
yet incremental -- that's a known, open gap (DEVELOPER.md), not an
oversight to silently work around.

No stage in this pipeline calls out to an LLM or needs an API key --
Docling, GROBID, embeddings/Chroma, BERTopic, and the Pandoc/LaTeX render
step are all local/deterministic. Any LLM-backed synthesis happens only
via the `.claude/skills/` genre layer, invoked through a Claude Code
session rather than a standalone API call.

`src/heavy/corpus.py` unifies two identifier namespaces: ledger items get
`doc_id == citekey` (real, citable); raw PDFs gathered outside the bib file
(e.g. an open metadata-API search, under `config.toml`'s `[source_pdfs].dir`
default `papers/pdfs/*.pdf`) get `doc:<filename stem>`, which can never
collide with a bib citekey (those never contain a colon) and which
`citation_gate.py` will always reject. Keep it that way -- don't give a
`source-pdfs`-sourced doc anything citekey-shaped. (`source-pdfs` here is
`CorpusDoc.source`'s internal tag value, not the name of a directory you
should expect to find on disk.)

## Retrieval

`src/retrieval.py` (keyword overlap, stdlib-only) is what the genre skills
use by default -- the corpus is still small enough that embeddings are
overhead without payoff. `src/heavy/embed_index.py` (sentence-transformers +
Chroma) is a verified, working upgrade path once that stops being true;
its `search(query, k)` shape matches `src/retrieval.py`'s so callers don't
need to change when you swap one for the other.

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
  `src/heavy/*` and the heavy Poetry group is installed, run it against
  the real sentence-transformers/chromadb/bertopic stack, not just
  `sys.modules`-mocked fakes. Unit tests catch regressions in logic;
  smoke tests catch wrong assumptions about how the real library actually
  behaves (this project's test suite has caught real fake-vs-real
  behavior drift this way before -- see `tests/test_heavy_embed_index.py`
  and `tests/test_heavy_topic_model.py`'s own comments).

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

## Pull requests

Title: same bar as a commit's title line -- concise, describes the effect.

Body:

```markdown
# <same as title>

## Type of Change
- [ ] New feature
- [ ] Bug fix
- [ ] Documentation update
- [ ] Refactoring
- [ ] Security patch
- [ ] Test coverage

## Description
Why this change, not just what it is -- the motivating problem or gap,
and the reasoning behind the approach taken over alternatives, referencing
an issue number if one exists.

## What changed, from the user's point of view
Bulleted, concrete, from the reader's perspective -- command examples
where relevant, not a restatement of the diff.

## Test plan
- [x] / [ ] items for what was actually verified (see "Before claiming a
  task complete" above) -- full suite + coverage, `poetry check`, and
  which real end-to-end smoke test(s) were run.
```

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

Release notes (the GitHub Release body, not the git tag message): a
version + date heading, then a `## Summary` of bulleted highlights written
for a reader who wasn't following along commit-by-commit -- what changed
and why it matters, not a raw commit log. For a small release, a `##
What's Changed` list of the PRs included (title + link) plus a `Full
Changelog` compare link is enough; for a larger one, add an `## In Detail`
section elaborating the most significant items with their own
subheadings.

## Shipping a code change: the full cycle

Any change that touches code (not a docs-only change) goes through the
complete cycle, and isn't done until every step below has actually
succeeded -- not merely started:

1. Branch off `main`, commit (see "Commit messages" above), push.
2. Decide the version bump (see "Versioning and releases") and update
   `pyproject.toml` as part of the same branch -- `release.yml` verifies
   the pushed tag against `pyproject.toml`'s version on `main`, so the
   bump has to land *before* the tag exists, i.e. in this PR, not after.
3. Open a PR against `main` (see "Pull requests" above).
4. Wait for `.github/workflows/ci.yml` to complete on the PR and confirm
   it's green -- if it fails, fix the actual cause (see "Before claiming a
   task complete") and push again; don't merge past a red check.
5. Squash-merge the PR.
6. Tag `v<version>` (matching what's now in `main`'s `pyproject.toml`) and
   push the tag.
7. Confirm `.github/workflows/release.yml` completed and the resulting
   GitHub Release has its `automated-research-<version>.zip` asset
   attached -- this is the actual deliverable, not the tag or the merge
   by itself.

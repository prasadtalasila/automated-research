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

All five genre skills (`survey-writer`, `thesis-chapter-writer`,
`textbook-chapter-writer`, `tutorial-writer`, `deep-research` in
`.claude/skills/`) must run
`python -m src.citation_gate <file>` on its own output and only present the
draft once it exits 0. This is a gate, not a
lint suggestion -- treat a `FAIL` the same way you'd treat a failing test.
It binds the two teaching genres too, where citations are optional: a
draft that cites nothing passes trivially, but a draft that cites
anything must pass on merit.
A PostToolUse hook (`.claude/hooks/citation_gate_hook.py`, wired up in
`.claude/settings.json`) now also enforces this mechanically: any Write/Edit
under `content/drafts/*.md` or `*.tex` runs the gate automatically and blocks
the write with a `FAIL` reason on the offending citekey(s) if it doesn't
pass. Treat the instruction above as belt-and-suspenders, not the only line
of defense -- but still run the gate by hand before calling a draft done,
since the hook only fires on the tool call that wrote the file, not on
demand.

## The bib file is the source of truth (not this pipeline)

`papers/bibliography.bib` (path configurable via `config.toml`'s
`[bib].path` or the `BIB_FILE` env var; gitignored, per-host data -- see
docs/CONFIG.md) is a manual export from your reference
manager's BibTeX export feature -- no auto-sync plugin is installed, so it
is not continuously auto-synced. Whatever citekey BibTeX assigns there
(e.g. `talasila_composable_2025`, or `noauthor_digital_nodate` for an item
with no discoverable author) is the citekey everywhere downstream.
`src/bib_reader.py` parses it and is the only place that reads it; nothing
else should ever generate or guess a citekey.

One constraint follows from that, enforced in `bib_reader.citekey_problem()`:
a citekey is also a **filename stem** (`content/parsed/<citekey>.txt`, its
`.passages.json` sidecar, the enrichment layer's `content/docling/<citekey>.md`),
so it has to be usable as one. A citekey containing a path separator, a
character Windows forbids, or a reserved device name is **skipped with a
warning naming it**, rather than sanitised -- this project never rewrites a
citekey, so the only fix is to rename it in the reference manager and
re-export. Skipping loses one paper and says so; letting it through would
write outside `content/`.

That rule is why a module needing bibliographic detail reads it back out
of the ledger rather than re-opening the bib file. `src/references.py`
formats an IEEE bibliography entry (authors, venue, volume, pages) from
the `bib_fields` column, which `sync` populates via `bib_reader` -- it
does not, and must not, parse `bibliography.bib` itself. The one thing
that legitimately reads the bib file directly is pandoc's `--citeproc`,
which is not this codebase.

**Content written before 2026-07-28 may cite keys in an older,
now-invalid format** (e.g. `talasila2025composable` instead of
`talasila_composable_2025`). Those citations are stale and will fail the
gate -- that's expected, not a regression; re-cite using whatever is
actually in `bibliography.bib`.

To add papers: add them in your reference manager, re-export
`bibliography.bib`, re-run `python -m src.sync`. There is no
watch/auto-export step here.

Removing a paper works the same way (delete it, re-export, re-run `sync`),
but deletion of the corresponding `content/ledger.sqlite` row is opt-in:
`sync` by default only reports a citekey that's dropped out of the bib
file (`stale   <citekey> (no longer in bibliography.bib)`, one line per
citekey, then a single summary note -- "Review the N stale item(s)
above, then re-run with --remove-stale..."); pass `--remove-stale` to
actually delete it. A bib export that comes back short a citekey is far
more often a botched re-export or `BIB_FILE` pointing at the wrong path
than an intentional removal, so the default leaves the ledger untouched
until a human confirms with the flag. Even with `--remove-stale`, `sync`
refuses (raises) rather than pruning if the bib file comes back
*completely* empty against a
non-empty ledger, for the same reason at the extreme -- see
`src/ledger.py`'s `prune_missing`.

## The three layers

- **The corpus layer -- deterministic** (`python -m src.sync`): bib file read
  -> ledger update -> PDF text extraction (paths come straight from the bib
  file's `file` field; `src/pdf_text.py` dispatches to pdftotext (default),
  or docling per `config.PARSER` -- see docs/CONFIG.md's "backend:
  pdftotext or docling") -> advisory duplicate-citekey check (`src/dedup.py`)
  -> stale-citekey report, or removal with `--remove-stale` (see "The bib
  file is the source of truth" above). No LLM calls, no judgment calls,
  idempotent. Safe to run unattended or on a schedule.
- **The drafting layer -- generative** (the `.claude/skills/`): invoked on
  demand, reviewed by the user. **Read-only over the corpus layer**: they
  never write to `content/ledger.sqlite`, and they never run `python -m
  src.sync` or the enrichment layer on the user's behalf. Both take the
  write lock and can run for tens of minutes; starting one is the user's
  call. On an empty ledger a skill says so and stops rather than
  regenerating anything.
- **The enrichment layer -- optional** (`scripts/enrich.py`):
  Docling, embeddings and topic modelling over the same corpus. It extends
  the *corpus* layer rather than the drafting one -- nothing in it is
  generative, everything it writes is a corpus artefact, and it takes the
  same write lock as `sync` for that reason. Run by a human, never by a
  skill.
- **Ad-hoc review aids** (`src/citation_provenance.py`,
  `scripts/verbatim_check.py`, `src/citation_coverage.py`): in no layer --
  run by hand when reviewing a draft, never invoked automatically, never
  gate anything. That they are not gates is deliberate: the gate answers a
  question with one correct answer (is this citekey in the ledger?), while
  these three answer questions of judgement, where a machine verdict would
  be either wrong often enough to ignore or trusted more than it deserves.
  Don't promote one to a gate.

These three were called "job 1", "job 2" and "the heavy pipeline" until
2026-08-06, and the code followed in 3.0.0: the word *heavy* now names
nothing here, since the Poetry group, the package and the `config.toml`
table are all `enrich`. What a part *does* and what it *costs to install*
remain separate axes, though -- `src/render_output.py` is drafting-layer
code that needs no package from that group, which is why it sits in
`src/` rather than `src/enrich/`.

## Config lives in `config.toml`

`src/config.py` loads `config.toml` (repo root) via stdlib `tomllib`, with
every setting overridable by an env var of the same name (e.g.
`BIB_FILE=/other/path.bib python -m src.sync`). Add new settings there, not
as hardcoded values in `config.py`.

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

## Retrieval

`src/retrieval.py` (BM25 ranking over a cached term-frequency index,
stdlib-only, no venv or model download needed) is what the genre skills
use by default. Term-frequency stats per document are cached to disk
(`config.RETRIEVAL_INDEX_PATH`), keyed by a cheap per-item fingerprint
(parsed-file stat, not content) so a call only re-tokenizes documents
whose text actually changed since the last run -- the same incremental
principle as `src/ledger.py`'s stat-before-hash skip logic and
`src/enrich/embed_index.py`'s embedding cache, just scoped to
`src/retrieval.py` itself (this doesn't touch `sync`). `src/enrich/embed_index.py`
(sentence-transformers + Chroma) is a verified, working upgrade path with
a matching `search(query, k)` shape, ready to swap in without changing
callers once BM25 stops being enough -- that's a deliberate call to make
when it comes up (source text quality/volume, query patterns), not a
corpus-size threshold to assert a number for here.

Retrieval finds a *document*; `src/passages.py` decides which part of it
may be shown. Anything that needs to point at a span of a source rather
than the whole of it -- `citation_provenance`, `verbatim_check`, the
enrichment layer -- goes through that one ladder rather than re-deriving
passages, so a caller cannot accidentally quote from a rung that isn't
quotable. See docs/LADDERS.md.

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

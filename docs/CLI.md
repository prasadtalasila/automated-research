# Command reference

Status: **reference.** Written 2026-08-03.

Every command this repository provides, every flag it accepts, and which
interpreter each one needs. [README.md](../README.md)'s Quickstart is the
short path; this is the full set.

## Table of contents

- [Upgrading a corpus parsed by an earlier version](#upgrading-a-corpus-parsed-by-an-earlier-version)
- [Upgrading from 2.x](#upgrading-from-2x)
- [Which interpreter](#which-interpreter)
- [The full first run, step by step](#the-full-first-run-step-by-step)
- [Every command and flag](#every-command-and-flag)
  - [`src.sync`](#python--m-srcsync)
  - [`src.ledger`](#python3--m-srcledger)
  - [`src.citation_gate`](#python3--m-srccitation_gate)
  - [`src.references`](#python3--m-srcreferences)
  - [`src.dossier`](#python3--m-srcdossier)
  - [`src.retrieval`](#python3--m-srcretrieval)
  - [`src.citation_coverage`](#python3--m-srccitation_coverage)
  - [`src.citation_provenance`](#python3--m-srccitation_provenance)
  - [`src.render_output`](#python3--m-srcrender_output)
  - [`scripts/enrich.py`](#scriptsenrichpy)
  - [`scripts/verbatim_check.py`](#scriptsverbatim_checkpy)
  - [`scripts/install_full_pipeline.sh`](#scriptsinstall_full_pipelinesh)
  - [`scripts/release.py`](#scriptsreleasepy)
- [Running sync on a schedule](#running-sync-on-a-schedule)
- [Environment variables](#environment-variables)

## Upgrading a corpus parsed by an earlier version

If you already ran `sync` with `[parser].backend = "docling"`, those
citekeys were parsed before this project kept Docling's page breaks and
passage records, and their PDFs haven't changed -- so the ledger would
normally skip them forever.

It doesn't. `sync` now treats a document it calls `parsed` whose passage
sidecar is missing as one that needs parsing again, so the next run
upgrades exactly those documents and nothing else. It costs one re-parse
each, once (6.65s per PDF serial, 0.62s at twelve workers -- see
[PERFORMANCE.md](PERFORMANCE.md)), and the run reports them the way it
reports any other parse. The same check restores a `.txt` or a sidecar
you delete by hand.

Nothing to do, in other words -- but if you would rather force it all at
once, `python -m src.sync --reparse` still re-extracts everything.

## Upgrading from 2.x

3.0.0 renamed the enrichment layer's identifiers to match the vocabulary
the documentation uses. Nothing else about how any command behaves
changed. Old spellings do not work -- there are no compatibility shims:

| 2.x | 3.0.0 |
|---|---|
| `python3 -m src.heavy.render_output` | `python3 -m src.render_output` |
| `python scripts/full_pipeline.py` | `python scripts/enrich.py` |
| `poetry install --with heavy` | `poetry install --with enrich` |
| `config.toml`'s `[heavy]` table | `[enrich]` |
| `src/heavy/` | `src/enrich/`, and `render_output.py` moved up to `src/` |

Two things deliberately did **not** change, because renaming them would
invalidate work you already have on disk for no conceptual gain:
`content/docling/`, `content/chroma/` and `content/topics.json` keep their
names, and so does every `DOCLING_*` environment variable.

`render_output` moving out of the package is the one rename that fixes a
mistake rather than a label: it is the drafting layer's publish step, runs
on bare `python3`, and never needed a package from that dependency group.
Living under `src/heavy/` said the opposite.

To upgrade: rename the `[heavy]` header in your `config.toml` to
`[enrich]`, re-run `bash scripts/install_full_pipeline.sh python-deps`,
and update any script of your own that calls the two commands above.

## Which interpreter

Three tiers. Commands below are written with the interpreter they need.

| Tier | Interpreter | Commands |
|---|---|---|
| 1 | **`python3`** -- stdlib only, no venv | `src.citation_gate`, `src.references`, `src.render_output`, `src.ledger`, `src.citation_provenance`, `src.citation_coverage`, `src.dossier`, `src.retrieval`, `scripts/verbatim_check.py` |
| 2 | **`.venv-full/bin/python`** -- venv, for `bibtexparser` | `src.sync` |
| 3 | **`.venv-full/bin/python`** -- venv with the `enrich` group | `scripts/enrich.py` |

Tier 1 is deliberate, not incidental. The chain that enforces the one rule
-- `citation_gate` -> `references` -> `render_output` -- imports nothing
outside the standard library, so it cannot be blocked by a virtual
environment that is broken, missing, or built for a different Python.
`docs/ARCHITECTURE.md` has the [full
reasoning](ARCHITECTURE.md#which-interpreter-and-why).

Two commands look like they belong in a higher tier and don't:

- `src.render_output` needs only stdlib plus
  `src.config`/`src.citation_gate`/`src.references`. It shells out to the
  `pandoc`/`pdflatex` binaries, which are OS packages rather than Python
  dependencies. (It was `src.heavy.render_output` until 3.0.0, which made
  it look like part of the enrichment layer; it never was.)
- `src.citation_coverage` and `scripts/verbatim_check.py` are review aids
  built on `src.retrieval` and `src.config`, both stdlib. `verbatim_check`
  calls the `pdftotext` binary, again an OS package.

Using the wrong interpreter is the most likely first error you will hit:
`ModuleNotFoundError: No module named 'bibtexparser'` means you ran
`python3 -m src.sync` instead of `.venv-full/bin/python -m src.sync`.

## The full first run, step by step

```bash
# 1. Export your reference manager's library to BibTeX at
#    papers/bibliography.bib (create papers/ if needed -- it's gitignored,
#    so a fresh clone never has it). Skipping this makes step 3 fail
#    immediately with a FileNotFoundError telling you to do exactly this.
mkdir -p papers && cp /path/to/your/exported-library.bib papers/bibliography.bib

# 1b. Create your config from the tracked example. config.toml is
#     gitignored per-host data, so a fresh clone has none, and
#     src/config.py refuses to import without it (naming this exact
#     command). Every key in it is optional -- see docs/CONFIG.md.
cp config.toml.example config.toml

# 2. Install. scripts/install_full_pipeline.sh is the only install path;
#    it takes stage names as positional arguments (see its own section
#    below for the full table). Poetry must exist before python-deps
#    runs -- install it yourself, or let the os-deps stage do it.
pipx install poetry
bash scripts/install_full_pipeline.sh os-deps      # root; pdftotext, Pandoc, TeX Live
bash scripts/install_full_pipeline.sh python-deps  # .venv-full/ + the enrich group
bash scripts/install_full_pipeline.sh dev-deps     # only to run the test suite

# `all` is os-deps + python-deps in one call, and deliberately excludes
# dev-deps:
# bash scripts/install_full_pipeline.sh all

# 3. Sync the corpus layer from papers/bibliography.bib.
.venv-full/bin/python -m src.sync

# 4. Inspect what it found. Read-only, takes no lock (so it works while a
#    sync is running), and needs no venv.
python3 -m src.ledger

# 5. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a textbook chapter introducing digital twin asset reuse"
#    "write a tutorial that builds a minimal digital twin asset from scratch"
# The matching skill in .claude/skills/ picks this up automatically,
# including its own citation_gate -> references -> render_output chain.

# 6. Re-run any step of that chain by hand (no venv needed for these).
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md
python3 -m src.render_output path/to/draft.md --format pdf
```

## Every command and flag

Defaults shown are the value used when the flag is omitted.

### `python -m src.sync`

Bibliography -> ledger -> parsed text. **Needs the venv.** Takes the
write lock, so only one run at a time; a second run exits **2** rather
than waiting.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `--reparse` | off | Re-extract every PDF, ignoring the ledger's record of what is already parsed. For when output is recorded as fine but you have reason to doubt it |
| `--remove-stale` | off (report only) | Delete ledger rows for citekeys no longer in the bib file. Without it they are only *reported* |

```bash
.venv-full/bin/python -m src.sync
# .venv-full/bin/python -m src.sync --reparse
# .venv-full/bin/python -m src.sync --remove-stale
# .venv-full/bin/python -m src.sync --reparse --remove-stale

# Exit codes: 0 = clean, 1 = at least one parse failed,
#             2 = another run holds the lock.
```

### `python3 -m src.ledger`

Read-only view of the corpus layer. **Takes no lock**, so it works while
a sync is running. With no flags it prints a summary.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `--list` | off | List every item |
| `--status STATUS` | -- | List only items with this status: `parsed`, `no_pdf`, `discovered`, `parse_failed` |
| `--citekey CITEKEY` | -- | Show one item in full |

```bash
python3 -m src.ledger
# python3 -m src.ledger --list
# python3 -m src.ledger --status parse_failed
# python3 -m src.ledger --status no_pdf
# python3 -m src.ledger --citekey talasila_composable_2025
```

### `python3 -m src.citation_gate`

The hard gate: fails if a draft cites a citekey the ledger doesn't hold.
**Takes no options** -- every argument is a file to check.

| Argument | What it does |
|---|---|
| `-h`, `--help` | Show usage and exit 0 |
| `<file> [<file> ...]` | One or more drafts to check |

```bash
python3 -m src.citation_gate content/drafts/survey.md
# python3 -m src.citation_gate content/drafts/*.md      # several at once

# Exit codes: 0 = every citation verified,
#             1 = at least one unresolved citekey,
#             2 = no files given.
```

### `python3 -m src.references`

Append or replace a `References` section built from a draft's own cited
citekeys.

Entries are IEEE-style and numbered by first appearance in the draft --
the order pandoc's citeproc numbers citations in, so this list and the
rendered PDF's bibliography agree on which source is `[1]`. Each entry
ends with its citekey in a code span, because the draft's own inline
markers are still `[@citekey]`:

```
[1] J. Doe and R. Roe, "A Paper," *IEEE Trans. Testing*, vol. 3, pp. 1–9, 2024. `doe_paper_2024`
```

Authors, venue, volume and pages come from the ledger's `bib_fields`
column, which `sync` populates from the bib file. A row synced before
that column existed has no fields to format, so its entry degrades to
title and year until the next `python -m src.sync`.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<input>` | required | The draft file (Markdown) |
| `--heading HEADING` | `References` | Heading text, e.g. `"6. References"` to match a draft's own numbered headings |

```bash
python3 -m src.references content/drafts/survey.md
# python3 -m src.references content/drafts/thesis.md --heading "6. References"
```

### `python3 -m src.dossier`

The working state behind a draft: create it, inspect it, back it up,
restore it. A dossier lives at `content/dossiers/` plus the draft's path
relative to `content/drafts/`, minus the suffix -- so
`content/drafts/dt/survey.md` gets `content/dossiers/dt/survey/`. Six
Markdown files hold the reader, the scope, the glossary, the kept
evidence, the rejected candidates and why, the user's steering, and a
revision log. [DRAFT-ITERATION.md](DRAFT-ITERATION.md) is the design.

Stdlib only, and never a gate: it takes no lock and only ever opens the
ledger read-only.

Two "missing" cases are deliberately different, because one is actionable
and the other isn't:

| Situation | `status` does |
|---|---|
| No ledger, or an unreadable one | Reports the dossier as usual, says the drift check is unavailable, **exits 0** |
| No dossier for this draft | Prints the `init` command to create one, **exits 1** |

So `python3 -m src.dossier status <draft> >/dev/null` is a usable test for
"does this draft have a dossier yet", while a machine with no corpus built
still gets a full report of what it has.

`status --all` is the other direction: one drift report over *every*
dossier, for after a `sync` that added or removed papers. It **always
exits 0** -- some drafts having drifted is the normal state of a live
corpus, not a failure -- so a caller branches on the contents, not the
status code. It reports two different things per dossier:

- **missing** -- a citekey the draft cites (`evidence.md` / `sections.md`)
  that has left the ledger, listed with the sections citing it. A defect.
- **candidates** -- papers now in the ledger that one of the dossier's own
  `retrieval.md` queries would surface in its top 15, minus everything
  already kept *or rejected*. A decision, not a defect.
- **reconsider** -- papers the draft already declined that those queries
  still reach, carried with the recorded reason. Not drift (it is true on
  every sweep), so it never marks a dossier stale and prints only
  alongside a real finding; `--json` always carries it.

Like every other read here it takes no lock and writes nothing: the
ledger is opened read-only and the BM25 index used for matching is built
in memory and discarded, leaving `content/retrieval_index.json`
untouched. A sweep costs about 2s cold and 0.2-0.4s warm on this
project's own corpus, and 50 dossiers cost only 0.19s more than one --
see [PERFORMANCE.md](PERFORMANCE.md#what-a-drift-sweep-costs) and
[DRAFT-ITERATION.md](DRAFT-ITERATION.md#drift-across-every-dossier).

`brief` is the one subcommand written for a *subagent* rather than a
person. A skill that dispatches parallel section writers has to give each
one its evidence, and pasting that evidence into the dispatch prompt
spends it as output -- the 5x direction, once per writer. `brief` is what
the prompt points at instead: the writer runs it in its own context, and
that context is discarded when it exits. It selects by citekey, or by a
section named in `sections.md`, and refuses to dump the whole of
`evidence.md` -- a caller reaching for it is trying not to read that.
See [DRAFT-ITERATION.md](DRAFT-ITERATION.md#dispatching-from-the-dossier)
and [TOKENS.md](TOKENS.md).

Its exit code is the contract, because a dispatch prompt cannot read a
paragraph: **0 when it printed at least one block, 1 when it could not
print any** -- no dossier, an unknown section, a section whose row
assigns no citekeys, or not one of the asked-for citekeys transcribed.
The last three are different gaps and the message says which. Everything
except the evidence itself goes to stderr, so stdout is only ever the
blocks. A citekey with no block is
named in a warning rather than dropped: it means the run that found it
never transcribed it, and that material is gone rather than mislaid.

| Subcommand | What it does |
|---|---|
| `init <draft> --genre G` | Create the skeleton. Only ever adds missing files -- safe to re-run |
| `status <draft>` | What each file holds, the draft's section count, and whether the corpus moved since |
| `status --all` | Corpus drift over every dossier: broken citations and new candidates. Always exits 0 |
| `sections <draft>` | Heading -> line range, for reading and editing one section instead of the file |
| `brief <draft> [citekey ...]` | The kept-evidence blocks for a section or a citekey list, for a subagent to read. **Exits 1 if nothing resolves** |
| `list` | Every dossier on this machine |
| `export [<name> ...]` | Bundle drafts + dossiers to a `.tar.gz` |
| `restore <archive>` | Unpack a bundle. **Dry run unless `--force`** |

| Flag | Applies to | What it does |
|---|---|---|
| `--genre GENRE` | `init` | Required: `survey`, `thesis-chapter`, `textbook-chapter`, `tutorial`, `deep-research` |
| `--all` | `status` | Report every dossier instead of one draft. Mutually exclusive with a draft path |
| `--json` | `status` | Emit the drift report as JSON, for `draft-reviser` rather than a terminal |
| `--section NAME` | `brief` | Take the citekeys from that `sections.md` row. Matches without the section's numbering; an ambiguous name matches nothing rather than guessing |
| `--check` | `brief` | Report what resolves, and what doesn't, without printing the blocks -- what an orchestrator runs before dispatching |
| `--out FILE` | `export` | Archive path (default `drafts-<name>-<date>.tar.gz`) |
| `--with-rendered` | `export` | Include `content/rendered/` too -- large, it holds the PDFs |
| `--force` | `restore` | Actually write, overwriting what is already there |

```bash
python3 -m src.dossier init content/drafts/survey.md --genre survey
python3 -m src.dossier status content/drafts/survey.md
python3 -m src.dossier sections content/drafts/survey.md

# Before dispatching a section writer: do this section's rows resolve?
python3 -m src.dossier brief content/drafts/survey.md --section "2. Failure modes" --check
# What the writer itself runs
python3 -m src.dossier brief content/drafts/survey.md --section "2. Failure modes"
python3 -m src.dossier brief content/drafts/survey.md talasila_composable_2025

# After a sync: which drafts went stale, and what specifically
python3 -m src.dossier status --all
python3 -m src.dossier status --all --json

# Back up everything, then one topic with its PDFs
python3 -m src.dossier export
python3 -m src.dossier export digital-twins-for-software-engineers --with-rendered

# Restore: look first, then commit to it
python3 -m src.dossier restore drafts-all-2026-08-06.tar.gz
python3 -m src.dossier restore drafts-all-2026-08-06.tar.gz --force
```

A bundle carries `drafts/`, `dossiers/` and optionally `rendered/`, with
paths relative to `content/` so it restores into a checkout whose
`[content].dir` points elsewhere. It does **not** carry
`content/ledger.sqlite` (regenerate with `python -m src.sync`) or
`papers/bibliography.bib` (your reference manager's export, which
AGENTS.md keeps as the source of truth rather than something this
pipeline copies). Restore refuses the whole archive -- rather than
skipping a member -- if any entry is a link or device node, escapes the
extraction directory, or sits outside those three directories.

### `python3 -m src.retrieval`

BM25 retrieval over the synced corpus. Read-only, takes no lock, needs no
venv. [RETRIEVAL.md](RETRIEVAL.md) has the ranking details.

| Subcommand | What it does |
|---|---|
| `search "<query>"` | Rank the corpus and return a snippet per candidate |
| `evidence "<query>" --citekey KEY` | The passages of that one document that bear on the query (`--windows`, 2 by default) |

`evidence` is a lookup, not a stage: use it when a `search` snippet is not
enough to judge a source you are minded to cite. Nothing is obliged to
call it. [REJECTION.md](REJECTION.md) explains why an earlier arrangement,
which made a cheap first pass mandatory and used it to *reject*, was
withdrawn.

| Flag | Applies to | Default | What it does |
|---|---|---|---|
| `--k N` | `search` | 5 | How many candidates to rank |
| `--chars N` | all | 600 / 500 | Window size (evidence / search) |
| `--citekey KEY` | `evidence` | required | Which document to read |
| `--windows N` | `evidence` | 2 | How many passages to return |
| `--log DRAFT` | all | -- | Record the call and its payload size in DRAFT's dossier |

```bash
python3 -m src.retrieval search "digital twin architecture" --k 15 \
    --log content/drafts/survey.md
python3 -m src.retrieval evidence "digital twin architecture" \
    --citekey ferko_architecting_2022 --log content/drafts/survey.md
```

`--log` appends to `retrieval.md` in that draft's dossier, which is what
turns "retrieval is where the tokens go" into a number for a particular
draft (`python3 -m src.dossier status` totals it). A `--log` path that
isn't under `content/drafts/`, or a filesystem error while writing, is
reported on stderr and skipped -- the measurement never fails the search
it was measuring.

Exits 1 with the fix if there is no ledger; an empty result set is not an
error.

### `python3 -m src.citation_coverage`

How much of what retrieval surfaced actually made it into a draft's
citations. **Informational, not a gate**, and unlike the gate it never
runs automatically. Stdlib-only, like `citation_gate` and `references` --
it reuses `src.retrieval`, which is itself stdlib.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<draft>` | required | The draft to check |
| `--query QUERY` | required, repeatable | A retrieval query to check coverage against. Give it more than once |
| `--k K` | `5` | Top-k results per query |

```bash
python3 -m src.citation_coverage content/drafts/survey.md \
    --query "digital twin composability" \
    --query "runtime verification"
# ... --k 10
```

### `python3 -m src.citation_provenance`

Reports what in each cited source actually supports the claim citing it,
quoting a real passage. A review aid, not a gate.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<draft>` | required | The Markdown draft to check |
| `--formats FORMATS` | `md,tex,pdf` | Comma-separated output formats. `tex`/`pdf` need `pandoc`/`pdflatex` on `PATH` |

```bash
python3 -m src.citation_provenance content/drafts/survey.md
# python3 -m src.citation_provenance content/drafts/survey.md --formats md
# python3 -m src.citation_provenance content/drafts/survey.md --formats md,tex,pdf
```

### `python3 -m src.render_output`

Render a Pandoc-Markdown or LaTeX draft. Needs `pandoc` (and `pdflatex`
for PDF) on `PATH`, but no Python package from the enrich group.

Citations render IEEE-style -- `[1]`, and `[3]–[6]` for a consecutive run
-- over a numbered bibliography of complete entries, via the CSL style
vendored at `assets/csl/ieee.csl`. In the copy handed to pandoc, the
draft's own References section (if `python -m src.references` added one)
keeps its heading but has its entries replaced by citeproc's placement
anchor, so the output carries exactly one bibliography -- citeproc's,
the one that can be numbered consistently with the inline markers --
under the draft's own heading, including a numbered one like
`## 6. References`. The draft file itself is never modified.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<input>` | required | The draft file (Markdown or LaTeX) |
| `--format FORMAT` | `pdf` | Output format -- e.g. `pdf`, `tex`, `docx`, `md` |
| `--documentclass CLASS` | `article` | LaTeX documentclass |
| `--fontsize SIZE` | `12pt` | LaTeX font size |
| `--papersize SIZE` | `a4` | LaTeX paper size, **without** the `paper` suffix pandoc appends itself -- so `a4`, `letter` |
| `--margin MARGIN` | `1in` | Page margin, passed to the `geometry` package |
| `--csl PATH` | `assets/csl/ieee.csl` | CSL style for citations and the bibliography. A relative path is looked for under the current directory first (like `<input>`), then the repo root -- so the repo-relative form `[render] csl` uses works from anywhere |
| `--no-collapse-citations` | off | Render a run as `[3], [4], [5], [6]` instead of `[3]–[6]`, i.e. leave the style exactly as it is on disk |

`--format md` on a **Markdown** draft is a special case, and the one
output you can read without a PDF viewer: it writes
`content/rendered/<slug>.md` with the citekeys replaced by the same IEEE
numbers the PDF uses (`[1]`, `[3]–[6]`) over a reference list built from
the ledger. It needs no `pandoc`, because pandoc's Markdown writer is the
wrong tool for it -- that writer escapes every marker (`\[1\]`, since
`[1]` could be a link reference) and emits the bibliography as `:::`
fenced divs full of `[...]{.csl-left-margin}` spans, none of which render
anywhere except pandoc.

The draft itself is never modified: it keeps its `[@citekey]` markers,
which are what `citation_gate` verifies and what `--citeproc` resolves
when rendering. A `.tex` input still goes through pandoc for `md`, since
converting a thesis fragment's `\citep{...}` genuinely is a format
conversion.

```bash
python3 -m src.render_output content/drafts/survey.md --format pdf
# python3 -m src.render_output content/drafts/survey.md --format tex
# python3 -m src.render_output content/drafts/survey.md --format docx
# python3 -m src.render_output content/drafts/survey.md --format md   # numbered Markdown, no pandoc needed
# python3 -m src.render_output content/drafts/thesis.md \
#     --documentclass report --fontsize 11pt --papersize letter --margin 1.5in
```

### `scripts/enrich.py`

Orchestrates the enrichment layer: docling -> embeddings/Chroma -> BERTopic
-> provenance -> render. **Needs the venv.** Each stage probes its own
prerequisites and reports a real per-stage status, so a
`skipped/missing-binary` result on a machine without TeX Live is a
correct answer rather than a bug.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `--target {host,docker}` | `host` | **Informational only** -- stages self-probe regardless |
| `--stages STAGES` | all five | Comma-separated subset of `docling,embed,bertopic,provenance,render` |
| `--input INPUT` | -- | Input file for the `render` stage |
| `--output-format FORMAT` | `pdf` | Output format for the `render` stage |
| `--documentclass CLASS` | `article` | LaTeX documentclass for the `render` stage |

```bash
.venv-full/bin/python scripts/enrich.py
# .venv-full/bin/python scripts/enrich.py --stages docling
# .venv-full/bin/python scripts/enrich.py --stages embed,bertopic
# .venv-full/bin/python scripts/enrich.py --stages render \
#     --input content/drafts/survey.md --output-format pdf --documentclass report
```

The `embed` stage names each document as it reaches it, so a run over a
real corpus is legible rather than silent for its whole duration:

```
=== embed ===
  [1/646] abbiati_modelling_2024 -- embedded, 92 chunk(s)
  [2/646] abduvakhobov_scalable_2024 -- unchanged, 65 chunk(s)
  [3/646] adhikari_digital_2023 -- no text to embed
  ...
  646 document(s): 102 embedded, 399 unchanged, 145 with no text -- 32033 chunk(s) in the index
```

`unchanged` is the incremental skip (same text as last run, not
re-encoded); `no text to embed` is a bib entry with no parsed text behind
it, which stays searchable by title through `src/retrieval.py` and not by
meaning. Ctrl+C is safe: every chunk upserted before the interrupt is
already in `content/chroma/`, the stage says how far it got, and re-running
picks up from there.

### `scripts/verbatim_check.py`

Review aid with two subcommands: verbatim overlap between a draft and a
source, and page location for a phrase. Stdlib-only -- but `locate` shells
out to the `pdftotext` binary, so that subcommand needs poppler-utils on
`PATH`. Run with no arguments to print its usage.

| Subcommand | Arguments | What it does |
|---|---|---|
| `overlap` | `<draft> <citekey> [--n N]` | Longest verbatim word-n-gram runs shared between the draft's sentences citing `<citekey>` and that source's parsed text. `--n` defaults to `8` |
| `locate` | `<citekey> "<phrase>" [more...]` | Which PDF page each phrase (or its distinctive words) appears on |

```bash
python3 scripts/verbatim_check.py overlap content/drafts/survey.md talasila_composable_2025
# python3 scripts/verbatim_check.py overlap content/drafts/survey.md talasila_composable_2025 --n 12
# python3 scripts/verbatim_check.py locate talasila_composable_2025 "a digital twin is"
```

`locate` reports page numbers by splitting on the form-feed characters
between pages. Both backends emit them, so a page number here is a page
you can turn to whichever one parsed the citekey -- see
[CONFIG.md](CONFIG.md#backend-pdftotext-or-docling). One limit: `docling`
writes a break between consecutive pages that carry text, so a page with
no extracted items at all shifts the numbering after it. The passage
sidecar records each item's own page and is not affected; where the two
disagree, believe `python3 -m src.citation_provenance`.

### `scripts/install_full_pipeline.sh`

One install path for both a bare machine and the Docker image. Takes
**stage names as positional arguments**, not flags.

| Stage | What it does |
|---|---|
| `python-deps` | **Default when no stage is given.** Creates the venv and runs `poetry install --with enrich` |
| `os-deps` | `apt-get` the system packages (TeX Live, Pandoc, poppler-utils, Poetry, git/curl/unzip). Needs root; auto-sudo's. Opt-in -- not everyone wants a script touching apt |
| `dev-deps` | `poetry install --with dev` (pytest, pytest-cov) into the same venv. Needed only to run the test suite. Run `python-deps` first |
| `all` | `os-deps` + `python-deps`. **Does not include `dev-deps`** |

```bash
bash scripts/install_full_pipeline.sh              # = python-deps
# bash scripts/install_full_pipeline.sh all
# bash scripts/install_full_pipeline.sh os-deps
# bash scripts/install_full_pipeline.sh dev-deps
# bash scripts/install_full_pipeline.sh os-deps python-deps dev-deps

# SKIP_VENV=1 installs into the active environment instead of creating
# .venv-full/ -- what docker/Dockerfile uses for its own /opt/venv.
# SKIP_VENV=1 bash scripts/install_full_pipeline.sh python-deps
```

`python-deps` and `dev-deps` also run `ensure_gpu_torch`, which detects
the NVIDIA driver's supported CUDA ceiling and reinstalls torch from a
matching wheel index if the default one would silently run CPU-only. It
is idempotent and safe to re-run, and prints what it decided -- `torch
already sees the GPU (driver supports its bundled CUDA build)` when no
reinstall was needed.

**Poetry is a prerequisite, not something `python-deps` installs.** It is
in the `os-deps` package list, so `all` covers it; if you run
`python-deps` on its own, install Poetry first (`pipx install poetry`).
Each stage ends by printing the exact interpreter path to use afterwards,
which is `.venv-full/bin/python` on a normal host.

### `scripts/release.py`

Builds the release archive under `release/`. A maintainer tool.

**Takes no arguments and parses none** -- including `-h`/`--help`, which
it ignores while building the archive anyway. Run it bare:

```bash
.venv-full/bin/python scripts/release.py
```

`tests/`, `bench/`, `.github/` and `.gitignore` are excluded from the
archive. Every prose document ships: `docs/`, `README.md`, `SOUL.md`,
`AGENTS.md`, `DEVELOPER-AGENTS.md` and `DEVELOPER.md`, plus `.claude/`.

## Running sync on a schedule

`python -m src.sync` is deterministic, idempotent, and takes its own
write lock (`src.runlock`) -- it was already safe to run unattended.
What makes it worth *actually* putting on a schedule is the other two
things: exit codes an unattended caller can branch on without parsing
any text, and `logs/sync.log` (rotated; see `[logging]` in
`config.toml.example`) as a persistent transcript to check afterwards.

**Don't hand-roll a log redirect for most of this.** `logs/sync.log`
carries almost every warning, per-document progress line, and the run
summary, at the level `[logging].level` sets -- a cron or systemd
wrapper around this command doesn't need its own `>> some.log 2>&1` to
get a durable record of those. Two messages are the exception and stay
terminal-only by design: a docling worker's GPU-OOM fallback (runs in a
child process with no route back to the file) and the Ctrl+C interrupt
notice (runs in a signal handler, deliberately kept to a bare `print`).
Both are rare and neither is the kind of thing a schedule needs to
recover from unattended.

**Exit codes are the API**, not the printed text:

| Exit code | Meaning | What an unattended caller should do |
|---|---|---|
| `0` | Clean -- everything that needed parsing, parsed | Nothing |
| `1` | At least one document failed, or a prior deterministic failure is still unresolved | Alert; `logs/sync.log`'s FAILED/WARNING lines name which citekey and why |
| `2` | Another run already holds the write lock | Nothing -- expected under any schedule tight enough to overlap a slow run. The skipped cycle costs nothing; the next one picks up whatever this one would have |

### cron

```bash
# crontab -e -- runs hourly, on the hour. cd into the repo first: sync
# resolves config.toml and papers/bibliography.bib relative to it.
0 * * * * cd /path/to/chitragupta && .venv-full/bin/python -m src.sync
```

cron's own default, with no `MAILTO` set, is to mail stdout/stderr to
the crontab's owner -- which needs a working local MTA to go anywhere,
and most hosts don't have one configured. `logs/sync.log` doesn't depend
on any of that: it's a plain file, written every run regardless of mail
setup.

### systemd (service + timer)

Two unit files, not one -- systemd's usual split between "what" and
"when":

```ini
# /etc/systemd/system/chitragupta-sync.service
[Unit]
Description=Chitragupta corpus sync

[Service]
Type=oneshot
WorkingDirectory=/path/to/chitragupta
ExecStart=/path/to/chitragupta/.venv-full/bin/python -m src.sync
# Exit 2 (another run still holds the lock) is an expected, harmless
# outcome under this schedule, not a service failure -- don't let
# systemd treat it as one.
SuccessExitStatus=2
```

```ini
# /etc/systemd/system/chitragupta-sync.timer
[Unit]
Description=Run chitragupta-sync.service hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chitragupta-sync.timer
journalctl -u chitragupta-sync.service   # systemd's own transcript,
                                          # alongside logs/sync.log
```

Both assume a host where `.venv-full/` is already built (see
[`scripts/install_full_pipeline.sh`](#scriptsinstall_full_pipelinesh)
above) -- scheduling only runs what's already installed, it doesn't
install anything itself.

## Environment variables

Every `config.toml` setting has a matching environment variable that
overrides it for one run. The full list, with accepted values, is in
[CONFIG.md](CONFIG.md#every-setting). The ones that most often appear on
a command line:

```bash
# Point at a different bibliography or output directory for one run
# BIB_FILE=/path/to/other.bib .venv-full/bin/python -m src.sync
# CONTENT_DIR=/tmp/scratch-content .venv-full/bin/python -m src.sync

# Keep config.toml somewhere else entirely
# CONFIG_PATH=/etc/research/config.toml .venv-full/bin/python -m src.sync

# Try the higher-fidelity parser with some parallelism, without editing the file
# PARSER=docling PARSER_WORKERS=auto .venv-full/bin/python -m src.sync

# Confine a docling run to one GPU (no config setting for this, by design)
# CUDA_VISIBLE_DEVICES=0 .venv-full/bin/python -m src.sync
```

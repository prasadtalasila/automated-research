# Command reference

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
| 1 | **`python3`** -- stdlib only, no venv | `src.citation_gate`, `src.references`, `src.render_output`, `src.ledger`, `src.citation_provenance`, `src.citation_coverage`, `scripts/verbatim_check.py` |
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

`tests/`, `bench/`, `DEVELOPER.md`, `AGENTS.md`, `.github/` and
`.gitignore` are excluded from the archive; `docs/` ships.

## Running sync on a schedule

`python -m src.sync` is deterministic, idempotent, and takes its own
write lock (`src.runlock`) -- it was already safe to run unattended.
What makes it worth *actually* putting on a schedule is the other two
things: exit codes an unattended caller can branch on without parsing
any text, and `logs/sync.log` (rotated; see `[logging]` in
`config.toml.example`) as a persistent transcript to check afterwards.

**Don't hand-roll a log redirect.** `logs/sync.log` already carries
every warning, per-document progress line, and the run summary, at the
level `[logging].level` sets. A cron or systemd wrapper around this
command doesn't need its own `>> some.log 2>&1` to get a durable
record -- that file already is one.

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

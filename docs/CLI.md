# Command reference

Every command this repository provides, every flag it accepts, and which
interpreter each one needs. [README.md](../README.md)'s Quickstart is the
short path; this is the full set.

## Table of contents

- [Which interpreter](#which-interpreter)
- [The full first run, step by step](#the-full-first-run-step-by-step)
- [Every command and flag](#every-command-and-flag)
  - [`src.sync`](#python--m-srcsync)
  - [`src.ledger`](#python3--m-srcledger)
  - [`src.citation_gate`](#python3--m-srccitation_gate)
  - [`src.references`](#python3--m-srcreferences)
  - [`src.citation_coverage`](#python3--m-srccitation_coverage)
  - [`src.citation_provenance`](#python3--m-srccitation_provenance)
  - [`src.heavy.render_output`](#python3--m-srcheavyrender_output)
  - [`scripts/full_pipeline.py`](#scriptsfull_pipelinepy)
  - [`scripts/verbatim_check.py`](#scriptsverbatim_checkpy)
  - [`scripts/install_full_pipeline.sh`](#scriptsinstall_full_pipelinesh)
  - [`scripts/release.py`](#scriptsreleasepy)
- [Environment variables](#environment-variables)

## Which interpreter

Commands below are written with the interpreter they need.

**`.venv-full/bin/python`** -- needs the venv (`bibtexparser`, or the
heavy stack):

- `src.sync`, `src.citation_coverage`, `scripts/full_pipeline.py`,
  `scripts/verbatim_check.py`

**`python3`** -- stdlib-only, no venv:

- `src.citation_gate`, `src.references`, `src.ledger`,
  `src.citation_provenance`, `src.heavy.render_output`

`render_output` lives under `src/heavy/` but needs only stdlib plus
`src.config`/`src.citation_gate`/`src.references`; it shells out to the
`pandoc`/`pdflatex` binaries, which are OS packages rather than Python
dependencies.

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

# Optional: raw, not-yet-cataloged PDFs (no reference-manager entry, no
# citekey) for the heavy pipeline's topic modelling/embeddings. NEVER
# citable this way -- add a PDF to your reference manager, re-export, and
# re-run sync before citing it.
mkdir -p papers/pdfs && cp /path/to/some-paper.pdf papers/pdfs/

# 2. Install Python dependencies -- creates .venv-full/ and runs
#    `poetry install --with heavy` into it. OS-level packages (TeX Live,
#    Pandoc, poppler-utils) are a separate, opt-in stage; see
#    scripts/install_full_pipeline.sh below.
bash scripts/install_full_pipeline.sh

# 3. Sync the content layer from papers/bibliography.bib.
.venv-full/bin/python -m src.sync

# 4. Inspect what it found. Read-only, takes no lock (so it works while a
#    sync is running), and needs no venv.
python3 -m src.ledger

# 5. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a tutorial chapter introducing digital twin asset reuse"
# The matching skill in .claude/skills/ picks this up automatically,
# including its own citation_gate -> references -> render_output chain.

# 6. Re-run any step of that chain by hand (no venv needed for these).
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md
python3 -m src.heavy.render_output path/to/draft.md --format pdf
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

Read-only view of the content layer. **Takes no lock**, so it works while
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
runs automatically.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<draft>` | required | The draft to check |
| `--query QUERY` | required, repeatable | A retrieval query to check coverage against. Give it more than once |
| `--k K` | `5` | Top-k results per query |

```bash
.venv-full/bin/python -m src.citation_coverage content/drafts/survey.md \
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

### `python3 -m src.heavy.render_output`

Render a Pandoc-Markdown or LaTeX draft. Needs `pandoc` (and `pdflatex`
for PDF) on `PATH`, but no Python package from the heavy group.

| Flag | Default | What it does |
|---|---|---|
| `-h`, `--help` | -- | Show help and exit |
| `<input>` | required | The draft file (Markdown or LaTeX) |
| `--format FORMAT` | `pdf` | Output format -- e.g. `pdf`, `tex`, `docx` |
| `--documentclass CLASS` | `article` | LaTeX documentclass |
| `--fontsize SIZE` | `12pt` | LaTeX font size |
| `--papersize SIZE` | `a4` | LaTeX paper size, **without** the `paper` suffix pandoc appends itself -- so `a4`, `letter` |
| `--margin MARGIN` | `1in` | Page margin, passed to the `geometry` package |

```bash
python3 -m src.heavy.render_output content/drafts/survey.md --format pdf
# python3 -m src.heavy.render_output content/drafts/survey.md --format tex
# python3 -m src.heavy.render_output content/drafts/survey.md --format docx
# python3 -m src.heavy.render_output content/drafts/thesis.md \
#     --documentclass report --fontsize 11pt --papersize letter --margin 1.5in
```

### `scripts/full_pipeline.py`

Orchestrates the heavy pipeline: docling -> embeddings/Chroma -> BERTopic
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
.venv-full/bin/python scripts/full_pipeline.py
# .venv-full/bin/python scripts/full_pipeline.py --stages docling
# .venv-full/bin/python scripts/full_pipeline.py --stages embed,bertopic
# .venv-full/bin/python scripts/full_pipeline.py --stages render \
#     --input content/drafts/survey.md --output-format pdf --documentclass report
```

### `scripts/verbatim_check.py`

Review aid with two subcommands: verbatim overlap between a draft and a
source, and page location for a phrase. **Needs the venv.** Run with no
arguments to print its usage.

| Subcommand | Arguments | What it does |
|---|---|---|
| `overlap` | `<draft> <citekey> [--n N]` | Longest verbatim word-n-gram runs shared between the draft's sentences citing `<citekey>` and that source's parsed text. `--n` defaults to `8` |
| `locate` | `<citekey> "<phrase>" [more...]` | Which PDF page each phrase (or its distinctive words) appears on |

```bash
.venv-full/bin/python scripts/verbatim_check.py overlap content/drafts/survey.md talasila_composable_2025
# .venv-full/bin/python scripts/verbatim_check.py overlap content/drafts/survey.md talasila_composable_2025 --n 12
# .venv-full/bin/python scripts/verbatim_check.py locate talasila_composable_2025 "a digital twin is"
```

`locate` reports page numbers by splitting on the form-feed characters
`pdftotext` emits between pages. A citekey parsed with the `docling`
backend has none, so every hit reports `pdf p.1` -- see
[CONFIG.md](CONFIG.md#backend-pdftotext-or-docling).

### `scripts/install_full_pipeline.sh`

One install path for both a bare machine and the Docker image. Takes
**stage names as positional arguments**, not flags.

| Stage | What it does |
|---|---|
| `python-deps` | **Default when no stage is given.** Creates the venv and runs `poetry install --with heavy` |
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
is idempotent and safe to re-run.

### `scripts/release.py`

Builds the release archive under `release/`. A maintainer tool.

**Takes no arguments and parses none** -- including `-h`/`--help`, which
it ignores while building the archive anyway. Run it bare:

```bash
.venv-full/bin/python scripts/release.py
```

`tests/`, `bench/`, `DEVELOPER.md`, `AGENTS.md`, `.github/` and
`.gitignore` are excluded from the archive; `docs/` ships.

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

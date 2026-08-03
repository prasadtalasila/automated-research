# Command reference

Every command this repository provides, and which interpreter each
one needs. [../README.md](../README.md)'s Quickstart is the short
path; this is the full set.

## The full first run, step by step

```bash
# 1. Export your reference manager's library to BibTeX at
#    papers/bibliography.bib (create the papers/ dir if needed -- it's
#    gitignored, so a fresh clone never has this file yet; see
#    "Configuration" below). Skipping this makes step 3 fail immediately
#    with a clear FileNotFoundError telling you to do exactly this.
mkdir -p papers && cp /path/to/your/exported-library.bib papers/bibliography.bib

# 1b. Create your config from the tracked example. config.toml is
#     gitignored per-host data, so a fresh clone has none, and
#     src/config.py refuses to import without it (naming this exact
#     command). Edit it afterwards -- parser backend, paths, worker count.
cp config.toml.example config.toml

# Optional: also add any raw, not-yet-cataloged PDFs (no reference-manager
# entry, no citekey) for the heavy pipeline's topic modeling/embeddings to
# consider -- see "papers/pdfs/" below. NEVER citable this way; add a PDF
# to your reference manager, re-export, and re-run sync before citing it.
mkdir -p papers/pdfs && cp /path/to/some-paper.pdf papers/pdfs/

# 2. Install Python dependencies -- creates .venv-full/ and runs `poetry
#    install --with heavy` into it: bibtexparser (core pipeline) plus the
#    full src/heavy/ stack. Dependencies/versions live in pyproject.toml +
#    poetry.lock; Poetry here is a lockfile/venv manager only, nothing is
#    published (see DEVELOPER.md's "Repository layout"). OS-level packages
#    (TeX Live, Pandoc, Poetry itself) are a separate, opt-in
#    stage -- see "What works on this host" below.
bash scripts/install_full_pipeline.sh

# 3. Sync the content layer from papers/bibliography.bib. A citekey that
#    later drops out of the bib file (a paper removed from your reference
#    manager) is only *reported* by default; re-run with --remove-stale
#    to actually delete its ledger row once you've reviewed the reported
#    list (see "Removing a paper" below) -- not needed on a first run.
.venv-full/bin/python -m src.sync

# 4. Inspect what it found. Read-only, takes no lock (so it works while a
#    sync is running), and needs no venv.
python3 -m src.ledger

# 5. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a tutorial chapter introducing digital twin asset reuse"
# The matching skill in .claude/skills/ picks this up automatically,
# including its own citation_gate -> references -> render_output chain
# (see "Architecture" below).

# 6. Manually re-run any step of that chain yourself (no venv needed for any of these)
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md --heading "References"    # --heading default: "References"
python3 -m src.heavy.render_output path/to/draft.md --format pdf     # also: --documentclass, --fontsize, --margin (--help for all)
```

# Venv requirement

Every `python -m src.*` / `python scripts/*.py` command below needs the
venv from Quickstart step 2 -- **except** three stdlib-only tools, which
run fine with the bare system `python3`:

- `python -m src.citation_gate <file>` -- only reads `content/ledger.sqlite`
  (stdlib `sqlite3`).
- `python -m src.references <file>` -- same, plus its own regex extraction
  (shared with `citation_gate`).
- `python3 -m src.ledger` -- read-only status for the content layer; also
  takes no lock, so it works while a sync is running.
- `python -m src.heavy.render_output <file> --format pdf` -- despite living
  under `src/heavy/`, this one only needs `stdlib` + `src.config` +
  `src.citation_gate` + `src.references`; it shells out to the `pandoc`/
  `pdflatex` binaries (apt packages, not Python deps), not anything from
  the heavy venv.

Using the wrong interpreter is the most likely first error you'll hit:
`ModuleNotFoundError: No module named 'bibtexparser'` means you ran
`python3 -m src.sync` instead of `.venv-full/bin/python -m src.sync`.

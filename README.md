# Automated Research Pipeline

Turns a BibTeX bibliography into grounded survey papers, thesis chapters,
and undergraduate tutorial chapters, with every citation traceable back to
a paper the bibliography actually holds.

## Table of contents

- [Quickstart](#quickstart)
- [Venv requirement](#venv-requirement)
- [Hardware requirements](#hardware-requirements)
- [Configuration](#configuration)
  - [Choosing an embedding model](#choosing-an-embedding-model)
- [Architecture](#architecture)
- [The heavy pipeline](#the-heavy-pipeline)
  - [Calling the heavy pipeline from a skill or agent](#calling-the-heavy-pipeline-from-a-skill-or-agent)
- [Running tests](#running-tests)
- [Repository layout](#repository-layout)
- [Open questions and unbuilt features](#open-questions-and-unbuilt-features)
- [Acknowledgements](#acknowledgements)

See also: [DOCKER.md](DOCKER.md) (running this repo in a container) and
[GROBID.md](GROBID.md) (building/running GROBID standalone on a bare host).

## Quickstart

```bash
# 1. Export your reference manager's library to BibTeX at
#    papers/bibliography.bib (create the papers/ dir if needed -- it's
#    gitignored, so a fresh clone never has this file yet; see
#    "Configuration" below). Skipping this makes step 3 fail immediately
#    with a clear FileNotFoundError telling you to do exactly this.
mkdir -p papers && cp /path/to/your/exported-library.bib papers/bibliography.bib

# 2. Install Python dependencies -- creates .venv-full/ and runs `poetry
#    install --with heavy` into it: bibtexparser (core pipeline) plus the
#    full src/heavy/ stack. Dependencies/versions live in pyproject.toml +
#    poetry.lock; Poetry here is a lockfile/venv manager only, nothing is
#    published (see "Repository layout" below). OS-level packages (JDK,
#    TeX Live, Pandoc, Poetry itself) and GROBID are separate, opt-in
#    stages -- see "What works on this host" below.
bash scripts/install_full_pipeline.sh

# 3. Sync the content layer from papers/bibliography.bib
.venv-full/bin/python -m src.sync

# 4. Inspect what it found
.venv-full/bin/python -c "
from src import ledger
con = ledger.connect()
for row in ledger.all_items(con): print(dict(row))
"

# 5. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a tutorial chapter introducing digital twin asset reuse"
# The matching skill in .claude/skills/ picks this up automatically,
# including its own citation_gate -> references -> render_output chain
# (see "Architecture" above).

# 6. Manually re-run any step of that chain yourself (no venv needed for any of these)
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md
python3 -m src.heavy.render_output path/to/draft.md --format pdf
```

To add papers: add the entry to your BibTeX bibliography, re-export
`papers/bibliography.bib` (a manual step unless your reference manager
auto-syncs it), then re-run `python -m src.sync`.

All paths are configurable in `config.toml` (repo root), overridable
per-run with an env var of the same name, e.g. `BIB_FILE=/path/to/other.bib
python -m src.sync`. See ["Configuration"](#configuration) below for the
full settings reference.


## Venv requirement

Every `python -m src.*` / `python scripts/*.py` command below needs the
venv from Quickstart step 1 -- **except** three stdlib-only tools, which
run fine with the bare system `python3`:

- `python -m src.citation_gate <file>` -- only reads `content/ledger.sqlite`
  (stdlib `sqlite3`).
- `python -m src.references <file>` -- same, plus its own regex extraction
  (shared with `citation_gate`).
- `python -m src.heavy.render_output <file> --format pdf` -- despite living
  under `src/heavy/`, this one only needs `stdlib` + `src.config` +
  `src.citation_gate` + `src.references`; it shells out to the `pandoc`/
  `pdflatex` binaries (apt packages, not Python deps), not anything from
  the heavy venv.

Using the wrong interpreter is the most likely first error you'll hit:
`ModuleNotFoundError: No module named 'bibtexparser'` means you ran
`python3 -m src.sync` instead of `.venv-full/bin/python -m src.sync`.

## Hardware requirements

Originally observed on a small CPU-only host (4 cores, 9.7GB RAM, ~3GB
actually free once other processes were accounted for); GPU behavior
below was separately verified on an NVIDIA A40 host (96 cores, 251GB RAM,
driver 555.42.02) on 2026-07-30:

| Resource | Minimum (core pipeline only) | Recommended (`src/heavy/` in regular use) |
|---|---|---|
| Disk | ~1GB (bibtexparser + content/) | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in twice over via sentence-transformers/docling, plus docling's own layout/OCR models); a GROBID build and TeX Live add several GB more on top |
| RAM | ~1-2GB (sync, citation_gate, keyword retrieval are all lightweight) | **8GB minimum, 16GB+ better**. At ~3GB free, Docling parsing a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it still finished, just slowly. Bigger PDFs or a bigger corpus will make this worse |
| CPU | 1-2 cores | **4+ cores** without a GPU -- Docling's layout inference and BERTopic's UMAP/HDBSCAN are CPU-bound if there's no GPU to offload to; more cores directly reduces wall-clock time |
| GPU | none needed | **none required**, but if present, `scripts/install_full_pipeline.sh`'s `ensure_gpu_torch` detects the NVIDIA driver's supported CUDA ceiling (`nvidia-smi`) and automatically reinstalls torch from a matching CUDA-tagged wheel index -- verified end-to-end on an A40 (driver capped at CUDA 12.5; the default pip/Poetry-resolved torch wheel needed CUDA 13 and silently ran CPU-only until this ran). sentence-transformers/Docling/BERTopic all then use the GPU automatically |
| Network | needed once, for `poetry install` | also needed for first-run model downloads (the embedding model, Docling's layout/OCR models, GROBID's Maven dependencies during its Gradle build) |

Tips:
- **No GPU, disk tight**: `pip`/Poetry's default torch wheel pulls a full
  set of `nvidia-*` CUDA packages even on a CPU-only machine (several GB,
  unused). Install torch from the CPU-only wheel index first (`pip
  install torch --index-url https://download.pytorch.org/whl/cpu`, inside
  the venv) before running `scripts/install_full_pipeline.sh` if disk is
  tight and there's no GPU to use anyway.
- **GPU present but `torch.cuda.is_available()` is `False`**: this is
  exactly the failure mode `ensure_gpu_torch` (in
  `scripts/install_full_pipeline.sh`) exists to catch and fix
  automatically on every `python-deps`/`dev-deps` run -- it's idempotent
  and safe to re-run by hand:
  ```bash
  .venv-full/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
  ```
  If it still reports `False` after `bash scripts/install_full_pipeline.sh python-deps`,
  your driver may predate every CUDA wheel tag the script knows about --
  see that function's own comments for the manual fallback.

## Configuration

`config.toml` (repo root) is the single place every path/URL/timeout/model
setting lives. `src/config.py` loads it once at import time via stdlib
`tomllib` into plain module-level constants (not functions, so they're
fixed for the life of the process); every setting can also be overridden
per-run with an environment variable of the same name, without editing the
file, e.g. `BIB_FILE=/path/to/other.bib python -m src.sync`.

| Section | Key | Env var | Default | What it controls |
|---|---|---|---|---|
| `[bib]` | `path` | `BIB_FILE` | `papers/bibliography.bib` | The BibTeX export `src/bib_reader.py` parses -- the only source of citekeys (CLAUDE.md's hard invariant). Gitignored, per-host -- see "Repository layout" |
| `[content]` | `dir` | `CONTENT_DIR` | `content` | Where `sync`/heavy-pipeline outputs live: `ledger.sqlite`, `parsed/`, `docling/`, `chroma/`, `topics.json`, `rendered/` |
| `[source_pdfs]` | `dir` | `SOURCE_PDFS_DIR` | `papers/pdfs` | Raw PDFs gathered outside the bib file, no citekey -- see `src/heavy/corpus.py` |
| `[heavy]` | `grobid_url` | `GROBID_URL` | `http://localhost:8070` | Where `src/heavy/grobid_extract.py` looks for a running GROBID instance |
| `[heavy]` | `grobid_health_timeout` | `GROBID_HEALTH_TIMEOUT` | `3.0` (seconds) | Kept low -- `is_available()` runs before every extraction and shouldn't itself become the slow part of a fast-fail path |
| `[heavy]` | `grobid_extract_timeout` | `GROBID_EXTRACT_TIMEOUT` | `60.0` (seconds) | Must outlast a real header extraction on a real PDF, not just a health check |
| `[heavy]` | `embedding_model` | `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Which sentence-transformers model `src/heavy/embed_index.py` loads for semantic search -- see below |

### Choosing an embedding model

`src/heavy/embed_index.py`'s `get_client_and_model()` calls
`SentenceTransformer(config.EMBEDDING_MODEL).encode(...)` symmetrically --
the exact same call embeds both a 200-word document chunk (`chunk_text`,
40-word overlap) and a search query, with no special prefix or instruction
text added either side. That rules out models that need one to perform as
designed: the BAAI `bge-*` and `intfloat e5-*` families expect literal
`"query: "` / `"passage: "` prefixes baked into the input string, which
this code doesn't add -- swapping one in would run without error but
silently underperform, not fail loudly.

Sentence-transformers hosts many general-purpose and domain-specific
models; of the ones that actually work correctly with this file's
prefix-free, symmetric usage as it stands today, three are best suited to
a research-paper corpus:

| Model | Dimensions | Relative cost | Best for | Tradeoff |
|---|---|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` (current default) | 384 | Lowest -- ~22M params, fast even on CPU | Small corpora, quick iteration, CPU-only hosts | Least semantic nuance of the three; general-purpose training data, nothing scientific-text-specific |
| `sentence-transformers/all-mpnet-base-v2` | 768 | ~4-5x MiniLM -- comfortable on the A40 GPU host, noticeably slower on CPU-only hardware | Meaningfully better general-purpose semantic quality; drop-in, same symmetric `encode()` call | More RAM/VRAM and slower indexing/search, for a quality gain that may not matter yet at this corpus's current size |
| `sentence-transformers/multi-qa-mpnet-base-dot-v1` | 768 | Same class as `all-mpnet-base-v2` | Trained specifically on short-query-vs-long-passage retrieval pairs -- the closest match to what `embed_index.search()` actually does | Slightly weaker than `all-mpnet-base-v2` on generic sentence-similarity outside retrieval; still no prefix needed, so still a genuine drop-in |

Not recommended without a code change first: `allenai/specter`/`specter2`
(trained on paper title+abstract pairs specifically -- a poor fit for the
arbitrary 200-word body-text chunks `chunk_text` actually produces) and
the BGE/E5 families (need the prefix handling `get_client_and_model()`
doesn't do). Either could be worth adopting later, paired with the
matching code change -- not as a config-only swap.

To switch models: edit `config.toml`'s `[heavy].embedding_model`, or set
`EMBEDDING_MODEL=...` for a single run, then re-run
`python scripts/full_pipeline.py --stages embed`. `sentence-transformers`
downloads a new model on first use (needs network), and Chroma's existing
collection isn't automatically re-embedded -- switch models only when
you're prepared to rebuild the index.

## Architecture

Two layers -- job 1 (deterministic) and job 2 (generative) in
[CLAUDE.md](CLAUDE.md)'s terms -- with an optional heavy-pipeline
extension of job 2:

- **Content layer** (job 1: shared, deterministic, safe to run
  unattended). Run via `python -m src.sync`. Idempotent and incremental --
  a paper is only re-parsed if its PDF content actually changed.

```
+--------------------+
|  bibliography.bib  |   BibTeX export -- source of truth for citekeys/metadata
+--------------------+
          |
          v
+--------------------+
| src/bib_reader.py  |   parses citekeys + metadata
+--------------------+
          |
          v
+---------------------------------------+
| src/ledger.py                         |
|   -> content/ledger.sqlite            |   per-citekey status tracking
+---------------------------------------+
          |
          v
+---------------------------------------+
| src/pdf_text.py                       |
|   -> content/parsed/<citekey>.txt     |   pdftotext extraction
+---------------------------------------+
          |
          v
+---------------------------------------+
| src/retrieval.py                      |
|   search(query, k) -> SearchResult    |   keyword-overlap ranking
+---------------------------------------+
```

- **Genre layer** (job 2: generative, on-demand, reviewed by you) --
  Claude Code skills in `.claude/skills/`: `survey-writer`,
  `thesis-chapter-writer`, `tutorial-writer`, and `deep-research` (a
  heavier, multi-perspective alternative -- see Acknowledgements). Each
  reads the content layer above; none of them regenerate it. Optionally
  extended by **the heavy pipeline** (`src/heavy/`,
  `scripts/full_pipeline.py`) -- see ["The heavy pipeline"](#the-heavy-pipeline)
  below.

```
JOB 1 -- deterministic, unattended-safe     JOB 2 -- generative, on demand, reviewed by you
+-------------------------------+     +---------------------------------------------+
| bibliography.bib -> ledger -> | --> | Genre skills (.claude/skills/) draft, then:  |
| pdf_text -> retrieval         |     |   1. src.citation_gate  -- hard gate, must   |
+-------------------------------+     |      pass before anything below runs         |
                                      |   2. src.references     -- auto "References" |
                                      |      section from exactly the cited citekeys |
                                      |   3. src.heavy.render_output -- tex/pdf,     |
                                      |      bare python3, no heavy venv needed      |
                                      +---------------------------------------------+
                                                             |
                                                             |  optional, opt-in
                                                             v
                                     +-----------------------------------------------+
                                     | THE HEAVY PIPELINE (src/heavy/)               |
                                     | Docling -> GROBID -> embeddings/Chroma ->     |
                                     | BERTopic -> (same render_output.py above)     |
                                     | each stage reports: ok / skipped /            |
                                     | missing-binary                                |
                                     +-----------------------------------------------+
```

`papers/bibliography.bib` is the **source of truth** for citekeys and
metadata -- this pipeline parses it, it does not generate its own citekeys
or its own copy of the bibliography. It's a per-host, gitignored file (see
"Configuration" below), not shipped in the repo, since the PDFs it points
to aren't either. See [CLAUDE.md](CLAUDE.md) for why, and what changed if
you're looking at content written before 2026-07-28.

Every genre skill runs `python -m src.citation_gate` on its own output
before presenting a draft, and refuses to invent a citekey. See
[CLAUDE.md](CLAUDE.md) for why this is a hard gate rather than a style
suggestion. Once a draft passes, `python -m src.references` appends (or
idempotently replaces) a "## References" section built only from citekeys
the draft already cites, and `python -m src.heavy.render_output
<file> --format pdf` (or `tex`/`docx`) renders it via Pandoc/TeX Live,
resolving `[@citekey]` markers against `bibliography.bib` -- both stdlib
only, no venv required, same as `citation_gate`. `scripts/verbatim_check.py`
is a separate, ad-hoc review aid (not part of this automatic chain) for
spot-checking a draft against its sources for verbatim overlap or
page-locating a quoted phrase.


| Capability | What it needs |
|---|---|
| Parse bib file, track citekeys + PDF paths | `bibtexparser` (venv, main Poetry group) |
| Extract PDF text | `pdftotext` (poppler-utils, `os-deps` stage) |
| Track parse status incrementally | stdlib `sqlite3` |
| Keyword-based retrieval | stdlib only |
| Citation verification gate, auto References section, standalone tex/pdf render | stdlib only, no venv needed (see "Venv requirement" above) |
| Docling layout-aware parsing, embeddings/Chroma, BERTopic | venv, `heavy` Poetry group (`src/heavy/`) |
| Bibliographic-quality parsing (GROBID: references, sections) | JDK 21 + a standalone GROBID build -- see [GROBID.md](GROBID.md) |
| Compiling generated `.tex` chapters to PDF (Pandoc/TeX Live) | `pandoc`, `pdflatex`, `latexmk` (`os-deps` stage) |

See [GROBID.md](GROBID.md) for the full GROBID build/run/troubleshooting
walkthrough (previously an inline subsection here).

Retrieval by default is a keyword-overlap ranker (`src/retrieval.py`,
stdlib only) -- deliberately: the corpus is still small enough that
embeddings are overhead without payoff for the genre skills' day-to-day
use. `src/heavy/embed_index.py` (sentence-transformers + Chroma) is a
verified, working upgrade with a matching `search(query, k)` signature,
for when that stops being true.

## The heavy pipeline

`scripts/full_pipeline.py` runs Docling -> GROBID -> sentence-
transformers/Chroma -> BERTopic -> Pandoc/LaTeX as one script for both the
host and Docker targets:

```bash
.venv-full/bin/python scripts/full_pipeline.py --stages embed,bertopic
.venv-full/bin/python scripts/full_pipeline.py --stages render --input draft.md
```

Each stage self-probes its prerequisites and reports honestly
(`ok`/`skipped`/`missing-binary`) instead of assuming the target implies
availability -- see `src/heavy/*.py` docstrings for what's been verified
and how. No stage needs an LLM API key -- this repo intentionally has none.
(An earlier revision had PaperQA2 and STORM stages here, both of which
needed `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`; they were removed to keep
this repo API-key-free. Any LLM-backed synthesis now happens only via the
`.claude/skills/` genre layer below, invoked through a Claude Code session
rather than a standalone API call.)

The `render` stage above is just `src/heavy/render_output.py` wired
through `full_pipeline.py`'s corpus-building machinery; if all you want
is to render one draft, `python -m src.heavy.render_output <file>
--format pdf` (bare `python3`, see "Venv requirement" above) does the
same thing without loading the rest of the heavy pipeline.

### Calling the heavy pipeline from a skill or agent

**Yes, directly -- this is already possible today, and already exercised,
no separate agent layer required.** A Claude Code skill runs inline with
the same Bash tool access as the session invoking it, so any skill can
shell out to `python -m src.heavy.<stage>` or
`python scripts/full_pipeline.py` exactly as a human would. `deep-research`
already does this conditionally: it calls `src.retrieval.search()` by
default but falls back to `src.heavy.embed_index.search()` -- same
`search(query, k, snippet_chars)` signature -- "if that stack has been
built" (checking `content/chroma/` first). `survey-writer` documents the
same conditional upgrade path.

The pattern to follow for any new use: **check the stack exists before
calling into it** (e.g. `content/chroma/` for embeddings, `.venv-full/`
for anything needing the heavy venv at all), and degrade to the
lightweight default rather than erroring if it doesn't -- consistent with
every `src/heavy/*` stage's own self-probing design.

`deep-research`'s three dispatched subagents
(`deep-research-interviewer`, `deep-research-writer`, `peer-reviewer` in
`.claude/agents/`) each also carry Bash tool access, so the same direct
call works from inside them too -- but nothing about calling the heavy
pipeline *requires* going through a dedicated agent. A skill invoked
inline is sufficient by itself.

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
CLAUDE.md                 project instructions for Claude Code sessions -- hard invariants, install notes
DOCKER.md                 running this repo in a container (docker/Dockerfile + docker/setup.sh)
GROBID.md                 building/running GROBID standalone on a bare host, step by step
config.toml               central config -- paths, GROBID URL/timeouts, embedding model (see "Configuration" below)
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
  retrieval.py              keyword search over the content layer
  citation_gate.py          hard citation-verification gate -- "job 2" must pass this
  references.py             auto-generates a draft's "## References" section from its own cited citekeys
src/heavy/                optional heavier pipeline (pyproject.toml's "heavy" Poetry group)
  corpus.py                 unifies ledger items + [source_pdfs].dir's raw PDFs (doc: prefixed, non-citable)
  docling_parse.py, embed_index.py, topic_model.py, grobid_extract.py
  render_output.py          Pandoc/TeX Live rendering + standalone CLI -- stdlib-only, no heavy venv needed
scripts/
  install_full_pipeline.sh  single staged install path (os-deps/python-deps/grobid/dev-deps/all) for host + Docker
  full_pipeline.py           orchestrates src/heavy/* stages
  verbatim_check.py          ad-hoc review aid: verbatim-overlap and page-locating checks against sources
tests/                    pytest suite -- unit tests per module + end-to-end feature tests (see "Running tests")
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, rendered/  (src/heavy/ outputs)
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
2. **The heavy stages have no incremental skip logic.** `python -m
   src.sync` already is incremental (a paper is only re-parsed if its PDF
   hash changed) -- safe to run every few minutes. `src/heavy/embed_index.py`
   and `src/heavy/topic_model.py` are **not**: they rebuild/reprocess
   every document on every call. A cron job that also re-runs
   `full_pipeline.py --stages docling,embed,bertopic` on a schedule would
   re-run Docling over all 5 PDFs every tick -- 373 seconds and the same
   swap pressure documented above, for zero new information. This needs
   the same content-hash-based skip logic `ledger.py` already has for the
   core pipeline, extended to the heavy stages.
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

## Acknowledgements

- **[hadufer/claude-storm](https://github.com/hadufer/claude-storm)** (MIT
  License) -- the `.claude/skills/deep-research/` skill and its
  `deep-research-interviewer`/`deep-research-writer` subagents adapt its
  7-phase pipeline (perspective discovery, parallel grounded interviews,
  contradiction mapping, outline, cited writing, synthesis, self peer-review).
  Retooled here for a closed, citekey-grounded local corpus instead of live
  web sources -- see `reference.md` in that skill's directory for exactly
  what changed and why.
- **[stanford-oval/storm](https://github.com/stanford-oval/storm)** -- the
  original STORM method claude-storm implements: "Assisting in Writing
  Wikipedia-like Articles From Scratch with Large Language Models" (Shao,
  Jiang, Kanell, Xu, Khattab, Lam; NAACL 2024; arXiv:2402.14207).
- Nav Toor's (@heynavtoor) 4-prompt adaptation, fused into claude-storm's
  pipeline and carried through into `deep-research`'s synthesis-briefing
  and single-reviewer (`quick` depth) peer-review phases.
- **[Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills)**
  -- the *idea* behind `deep-research`'s `standard`/`deep`-depth peer review
  (an independent multi-reviewer panel including a dedicated adversarial
  reviewer, reconciled against a concession threshold) is credited to that
  project's Stage-3 peer-review design. That project is licensed CC-BY-NC
  4.0; **no text from it was copied** -- `.claude/agents/peer-reviewer.md`
  and `.claude/skills/deep-research/reference.md` §7 are written from
  scratch, adapting only the concept of an independent panel plus a
  Devil's Advocate role, not its implementation.

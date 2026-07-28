# Automated Research Pipeline

Turns a BibTeX bibliography into grounded survey papers, thesis chapters,
and undergraduate tutorial chapters, with every citation traceable back to
a paper the bibliography actually holds.

## Table of contents

- [Architecture](#architecture)
- [Venv requirement](#venv-requirement)
- [Hardware requirements](#hardware-requirements)
- [Quickstart](#quickstart)
- [Host vs. Docker: what runs where](#host-vs-docker-what-runs-where)
  - [Building GROBID standalone on a bare host](#building-grobid-standalone-on-a-bare-host)
- [Running with Docker](#running-with-docker)
- [The heavy pipeline](#the-heavy-pipeline)
  - [Calling the heavy pipeline from a skill or agent](#calling-the-heavy-pipeline-from-a-skill-or-agent)
- [Open questions and unbuilt features](#open-questions-and-unbuilt-features)
- [Repository layout](#repository-layout)
- [Acknowledgements](#acknowledgements)

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
| bibliography.bib -> ledger -> | --> | Genre skills (.claude/skills/)              |
| pdf_text -> retrieval         |     | each runs `python -m src.citation_gate`     |
+-------------------------------+     | on its own output before presenting a draft |
                                      +---------------------------------------------+
                                                             |
                                                             |  optional, opt-in
                                                             v
                                     +-----------------------------------------------+
                                     | THE HEAVY PIPELINE (src/heavy/)               |
                                     | Docling -> GROBID -> embeddings/Chroma ->     |
                                     | BERTopic -> Pandoc/LaTeX                      |
                                     | each stage reports: ok / skipped /            |
                                     | missing-binary                                |
                                     +-----------------------------------------------+
```

`bibliography.bib` (repo root) is the **source of truth** for citekeys and
metadata -- this pipeline parses it, it does not generate its own citekeys
or its own copy of the bibliography. See [CLAUDE.md](CLAUDE.md) for why,
and what changed if you're looking at content written before 2026-07-28.

Every genre skill runs `python -m src.citation_gate` on its own output
before presenting a draft, and refuses to invent a citekey. See
[CLAUDE.md](CLAUDE.md) for why this is a hard gate rather than a style
suggestion.

## Venv requirement

Every `python -m src.*` / `python scripts/*.py` command below needs the
venv from Quickstart step 1 -- **except** `python -m src.citation_gate`,
which only reads `content/ledger.sqlite` (stdlib `sqlite3`) and runs fine
with the bare system `python3`. Using the wrong interpreter is the most
likely first error you'll hit: `ModuleNotFoundError: No module named
'bibtexparser'` means you ran `python3 -m src.sync` instead of
`.venv-full/bin/python -m src.sync`.

## Hardware requirements

Observed on the machine this was built and verified on (4 cores, 9.7GB
RAM, ~3GB actually free once other processes were accounted for):

| Resource | Minimum (core pipeline only) | Recommended (`src/heavy/` in regular use) |
|---|---|---|
| Disk | ~1GB (bibtexparser + content/) | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in twice over via sentence-transformers/docling, plus docling's own layout/OCR models); a GROBID build and TeX Live add several GB more on top |
| RAM | ~1-2GB (sync, citation_gate, keyword retrieval are all lightweight) | **8GB minimum, 16GB+ better**. At ~3GB free, Docling parsing a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it still finished, just slowly. Bigger PDFs or a bigger corpus will make this worse |
| CPU | 1-2 cores | **4+ cores** -- Docling's layout inference and BERTopic's UMAP/HDBSCAN are both CPU-bound with no GPU here; more cores directly reduces wall-clock time |
| GPU | none needed | none required, but sentence-transformers/Docling/BERTopic will use one automatically via torch if present, and it would help once the corpus is large enough for that to matter |
| Network | needed once, for `pip install` | also needed for first-run model downloads (the embedding model, Docling's layout/OCR models, GROBID's Maven dependencies during its Gradle build) |

Tip: `pip`'s default torch wheel pulls a full set of `nvidia-*` CUDA
packages even on a CPU-only machine like this one (several GB, unused). If
disk is tight and there's no GPU, install torch from the CPU-only wheel
index first (`pip install torch --index-url
https://download.pytorch.org/whl/cpu`) before running
`scripts/install_full_pipeline.sh` -- not done here since it wasn't a
blocker at 170GB free, but it's a real, easy saving.

## Quickstart

```bash
# 1. Install Python dependencies -- creates .venv-full/ with bibtexparser
#    (core pipeline) plus the full src/heavy/ stack. OS-level packages
#    (JDK, TeX Live, Pandoc) and GROBID are separate, opt-in stages --
#    see "Host vs. Docker: what runs where" below.
bash scripts/install_full_pipeline.sh

# 2. Sync the content layer from bibliography.bib
.venv-full/bin/python -m src.sync

# 3. Inspect what it found
.venv-full/bin/python -c "
from src import ledger
con = ledger.connect()
for row in ledger.all_items(con): print(dict(row))
"

# 4. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a tutorial chapter introducing digital twin asset reuse"
# The matching skill in .claude/skills/ picks this up automatically.

# 5. Manually re-check citations in any draft yourself (no venv needed)
python3 -m src.citation_gate path/to/draft.md
```

To add papers: add the entry to your BibTeX bibliography, re-export
`bibliography.bib` (a manual step unless your reference manager
auto-syncs it), then re-run `python -m src.sync`.

All paths are configurable in `config.toml` (repo root), overridable
per-run with an env var of the same name, e.g. `BIB_FILE=/path/to/other.bib
python -m src.sync`.

## Host vs. Docker: what runs where

This section originally described a host with no root, no Java, no TeX
Live, and no Pandoc -- the constraint `docker/` exists to work around.
That's since changed: root (`sudo apt-get`), a JDK, TeX Live, and Pandoc
are now all installed and verified directly on this host. `pip install`
outside a venv is still blocked (PEP 668 / externally-managed-environment)
-- that's a Python packaging restriction, independent of root access.
Don't assume this generalizes to every host running this repo, though --
`docker/` (below) exists precisely for hosts where it doesn't.

| Capability | Here | Needs `docker/` |
|---|---|---|
| Parse bib file, track citekeys + PDF paths | `bibtexparser` (venv) | -- |
| Extract PDF text | `pdftotext` (already present) | -- |
| Track parse status incrementally | stdlib `sqlite3` | -- |
| Keyword-based retrieval | stdlib only | -- |
| Citation verification gate | stdlib `re`, no venv needed | -- |
| Docling layout-aware parsing, embeddings/Chroma, BERTopic | venv (`src/heavy/`) | also works, verified |
| Bibliographic-quality parsing (GROBID: references, sections) | JDK 21 + a standalone GROBID build (a hand-built one is verified reachable at `:8070`; see caveat below) | also works -- `docker/setup.sh` builds+runs the same way |
| Compiling generated `.tex` chapters to PDF (Pandoc/TeX Live) | `pandoc`, `pdflatex`, `latexmk` all installed and verified working | also works |

### Building GROBID standalone on a bare host

```bash
bash scripts/install_full_pipeline.sh os-deps   # JDK 21 + the rest -- needs root; skip if you already have a JDK 21
bash scripts/install_full_pipeline.sh grobid    # fetch + build GROBID standalone -- multi-GB, slow, run once
```

The `grobid` stage fetches
**[grobidOrg/grobid](https://github.com/grobidOrg/grobid)** (the
authoritative GROBID repository) at the pinned `GROBID_VERSION` (default
`0.9.0`), builds it (`./gradlew clean build -x test`, which also produces
the standalone distribution zips), and prints how to start it
(`./gradlew run`, or the standalone distribution it just built). Override
where it's fetched to with `GROBID_DIR` (defaults to
`$HOME/grobid-<version>` on a bare host; `docker/Dockerfile` uses
`/opt/grobid`) and the version with `GROBID_VERSION`.

Must be a **JDK**, not a JRE -- GROBID compiles Kotlin/Java from source,
and that needs `javac`. And it must be **version 21 specifically, not
whatever's newest**: GROBID's `build.gradle` pins a Java 21 toolchain, and
its bundled Kotlin compiler (2.0.21) throws `IllegalArgumentException:
25.0.3` trying to parse a JDK 25 version string -- it predates JDK 25's
existence. `install_full_pipeline.sh grobid` checks the *default* `java`
for this up front and exits with a clear message rather than failing deep
inside the Kotlin compiler -- but on a host with multiple JDKs installed
where 21 isn't the default (this host's own case), that check will refuse
even though a working build is still reachable: either
`sudo update-alternatives --config java` to make 21 the default, or add
`org.gradle.java.home=/usr/lib/jvm/java-21-openjdk-amd64` (adjust the path)
to GROBID's own `gradle.properties`, which is what actually worked here
without changing the system default. If a Gradle daemon or the separate
long-lived Kotlin compiler daemon (`ps aux | grep -i kotlin`) already
started under the wrong JDK before you fix this, `./gradlew --stop` and
killing that Kotlin daemon are both necessary -- neither one picks up a
JDK change on its own.

**What's actually verified vs. not:** a GROBID 0.9.0 build done by hand
(fetch, `./gradlew clean build -x test`, unzip+run the standalone
distribution) answers `/api/isalive` and `/api/health` at
`http://localhost:8070`, matching `config.toml`'s `grobid_url` default.
**Not verified**: `install_full_pipeline.sh grobid` above is a
reimplementation of that same recipe, written afterward -- it has not
itself been executed end to end. Nor has the Python-side probe
(`src.heavy.grobid_extract.is_available()`) been re-checked against a live
`.venv-full/` -- that venv doesn't currently exist on this host (rerun
Quickstart step 1 to recreate it).

Retrieval by default is a keyword-overlap ranker (`src/retrieval.py`,
stdlib only) -- deliberately: the corpus is still small enough that
embeddings are overhead without payoff for the genre skills' day-to-day
use. `src/heavy/embed_index.py` (sentence-transformers + Chroma) is a
verified, working upgrade with a matching `search(query, k)` signature,
for when that stops being true.

## Running with Docker

**Untested end-to-end**: no Docker daemon is available in the environment
this was written in, so nothing below has actually been built or run --
it's what `docker/Dockerfile` and `docker/setup.sh` document, not
something exercised. Validate before relying on it.

Build:

```bash
docker build -t research-pipeline -f docker/Dockerfile .
```

This runs `scripts/install_full_pipeline.sh` three times as separate,
independently cached layers -- `os-deps`, then `grobid`, then
`python-deps` with `SKIP_VENV=1` into a venv at `/opt/venv` -- so editing
later Dockerfile lines or unrelated repo files doesn't force earlier
layers to rebuild. **Exception**: the script itself is `COPY`'d once,
before any of the three stages run, so editing
`scripts/install_full_pipeline.sh` invalidates all three layers, including
the multi-GB `grobid` one -- Docker's cache keys each layer on the exact
command *and* any files that command's `COPY` depends on, and this file
feeds all of them. The `grobid` layer alone is multi-GB and multi-minute;
expect a long first build.

Run (mount your repo and a volume for `content/` so it survives container
restarts):

```bash
docker run -it --rm \
    -v "$(pwd)":/workspace/automated-research \
    -v research-pipeline-content:/workspace/automated-research/content \
    research-pipeline
```

GROBID is built into the image but not started automatically. Inside the
running container, start it and sanity-check the rest of the toolchain:

```bash
docker exec -it <container> /usr/local/bin/setup-grobid.sh
```

From there, the same commands as the Quickstart above work directly with
no venv prefix, since `/opt/venv` is already on `PATH` inside the
container:

```bash
python -m src.sync
python scripts/full_pipeline.py --stages embed,bertopic
```

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

## Open questions and unbuilt features

Everything in this section is **proposed, not implemented** -- analysis
and a recommendation, not shipped behavior. Distinct from the
verified-vs-unverified distinction elsewhere in this doc: nothing here has
code behind it yet.

### What's missing to run this as a cron job monitoring the bib file?

In priority order:

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

## Repository layout

```
config.toml              central config -- paths, GROBID URL/timeouts, embedding model
bibliography.bib          BibTeX export -- source of truth for citekeys/metadata
src/                      core pipeline (needs bibtexparser; citation_gate needs nothing)
  config.py                 loads config.toml, env var overrides
  bib_reader.py             parses bibliography.bib -- the only citekey source
  ledger.py                 per-citekey status tracking (content/ledger.sqlite)
  pdf_text.py               pdftotext wrapper
  sync.py                   orchestrates the above -- the "job 1" entrypoint
  retrieval.py              keyword search over the content layer
  citation_gate.py          hard citation-verification gate -- "job 2" must pass this
src/heavy/                optional heavier pipeline (docker/requirements-full.txt)
  corpus.py                 unifies ledger items + source-pdfs/ (doc: prefixed, non-citable)
  docling_parse.py, embed_index.py, topic_model.py, grobid_extract.py,
  render_output.py
scripts/
  install_full_pipeline.sh  single staged install path (os-deps/python-deps/grobid/all) for host + Docker
  full_pipeline.py           orchestrates src/heavy/* stages
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, rendered/  (src/heavy/ outputs)
source-pdfs/              raw PDFs not sourced via the bib file -- see src/heavy/corpus.py; never citable
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer, tutorial-writer, deep-research
.claude/agents/           deep-research's subagents: deep-research-interviewer, deep-research-writer, peer-reviewer
docker/                   Dockerfile + setup.sh (GROBID/TeX Live/Pandoc) -- unverified end-to-end
```

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

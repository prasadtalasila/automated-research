# Automated Research Pipeline

Turns a Zotero library into grounded survey papers, thesis chapters, and
undergraduate tutorial chapters, with every citation traceable back to a
paper Zotero actually holds.

## Architecture

Two layers:

- **Content layer** (shared, deterministic, safe to run unattended):
  `bibliography.bib -> src/bib_reader.py -> src/ledger.py (content/ledger.sqlite)
  -> src/pdf_text.py (content/parsed/*.txt) -> src/retrieval.py`
  Run via `python -m src.sync`. Idempotent and incremental -- a paper is only
  re-parsed if its PDF content actually changed.
- **Genre layer** (generative, on-demand, reviewed by you): Claude Code
  skills in `.claude/skills/` -- `survey-writer`, `thesis-chapter-writer`,
  `tutorial-writer`, and `deep-research` (a heavier, multi-perspective
  alternative to `survey-writer` -- see Acknowledgements) -- each reading
  the same content layer.

`bibliography.bib` (a manual export from Zotero, File > Export Library >
BibTeX) is the **source of truth** for citekeys and metadata -- this pipeline
parses it, it does not generate its own citekeys or its own copy of the
bibliography. See [CLAUDE.md](CLAUDE.md) for why, and what changed if you're
looking at content written before 2026-07-28.

Every genre skill runs `python -m src.citation_gate` on its own output before
presenting a draft, and refuses to invent a citekey. See [CLAUDE.md](CLAUDE.md)
for why this is a hard gate rather than a style suggestion.

## Venv requirement

Every `python -m src.*` / `python scripts/*.py` command below needs the venv
from step 1 -- **except** `python -m src.citation_gate`, which only reads
`content/ledger.sqlite` (stdlib `sqlite3`) and runs fine with the bare
system `python3`. Using the wrong interpreter is the most likely first
error you'll hit: `ModuleNotFoundError: No module named 'bibtexparser'`
means you ran `python3 -m src.sync` instead of
`.venv-full/bin/python -m src.sync`.

## Hardware requirements

Observed on the machine this was built and verified on (4 cores, 9.7GB RAM,
~3GB actually free once other processes were accounted for):

| Resource | Minimum (core pipeline only) | Recommended (`src/heavy/` in regular use) |
|---|---|---|
| Disk | ~1GB (bibtexparser + content/) | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in three times over via sentence-transformers/docling/knowledge-storm, plus docling's own layout/OCR models); the Docker image adds TeX Live and a GROBID build on top of that |
| RAM | ~1-2GB (sync, citation_gate, keyword retrieval are all lightweight) | **8GB minimum, 16GB+ better**. At ~3GB free, Docling parsing a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it still finished, just slowly. Bigger PDFs or a bigger corpus will make this worse |
| CPU | 1-2 cores | **4+ cores** -- Docling's layout inference and BERTopic's UMAP/HDBSCAN are both CPU-bound with no GPU here; more cores directly reduces wall-clock time |
| GPU | none needed | none required, but sentence-transformers/Docling/BERTopic will use one automatically via torch if present, and it would help once the corpus is large enough for that to matter |
| Network | needed once, for `pip install` | also needed for first-run model downloads (the embedding model, Docling's layout/OCR models, GROBID's Maven dependencies during its Gradle build) |

Tip: `pip`'s default torch wheel pulls a full set of `nvidia-*` CUDA packages
even on a CPU-only machine like this one (several GB, unused). If disk is
tight and there's no GPU, install torch from the CPU-only wheel index first
(`pip install torch --index-url https://download.pytorch.org/whl/cpu`)
before running `scripts/install_full_pipeline.sh` -- not done here since
it wasn't a blocker at 170GB free, but it's a real, easy saving.

## Quickstart

```bash
# 1. Install (one script, works on a bare host or inside docker/Dockerfile)
bash scripts/install_full_pipeline.sh
# creates .venv-full/ with bibtexparser (core) + the full src/heavy/ stack

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

To add papers: add them in Zotero, re-export `bibliography.bib` (no Better
BibTeX plugin is installed, so this is a manual step, not continuous
auto-sync), then re-run `python -m src.sync`.

All paths are configurable in `config.toml` (repo root), overridable per-run
with an env var of the same name, e.g. `BIB_FILE=/path/to/other.bib python -m
src.sync`.

## What runs here vs. what needs the Docker path

This section originally described a host with no root, no Java, no TeX
Live, and no Pandoc -- the constraint `docker/` exists to work around.
That's since changed: root (`sudo apt-get`), a JDK, TeX Live, and Pandoc
are now all installed and verified directly on this host. `pip install`
outside a venv is still blocked (PEP 668 / externally-managed-environment)
-- that's a Python packaging restriction, independent of root access.

| Capability | Here | Needs `docker/` |
|---|---|---|
| Parse bib file, track citekeys + PDF paths | `bibtexparser` (venv) | -- |
| Extract PDF text | `pdftotext` (already present) | -- |
| Track parse status incrementally | stdlib `sqlite3` | -- |
| Keyword-based retrieval | stdlib only | -- |
| Citation verification gate | stdlib `re`, no venv needed | -- |
| Docling layout-aware parsing, embeddings/Chroma, BERTopic | venv (`src/heavy/`) | also works, verified |
| PaperQA2 / STORM (needs an LLM key) | installs, no key configured here | also works if given a key |
| Bibliographic-quality parsing (GROBID: references, sections) | JDK 21 + a standalone GROBID build; service verified reachable at `:8070` (recipe below) | also works -- `docker/setup.sh` builds+runs the same way |
| Compiling generated `.tex` chapters to PDF (Pandoc/TeX Live) | `pandoc`, `pdflatex`, `latexmk` all installed and verified working | also works |

### Building GROBID standalone on a bare host (not Docker)

```bash
sudo apt-get install -y openjdk-21-jdk   # must be 21, not the newest available -- see caveat below
wget https://github.com/kermitt2/grobid/archive/refs/tags/0.9.0.zip
unzip 0.9.0.zip && cd grobid-0.9.0
./gradlew clean assemble          # builds grobid-service's + grobid-home's distribution zips
cd ..
mkdir grobid-installation && cd grobid-installation
unzip ../grobid-0.9.0/grobid-service/build/distributions/grobid-service-0.9.0.zip
mv grobid-service-0.9.0 grobid-service
unzip ../grobid-0.9.0/grobid-home/build/distributions/grobid-home-0.9.0.zip
./grobid-service/bin/grobid-service   # foreground; wrap in nohup ... & to background it
```

Must be a **JDK**, not a JRE -- GROBID compiles Kotlin/Java from source, and
that needs `javac`. And it must be **version 21 specifically, not whatever's
newest**: GROBID's `build.gradle` pins a Java 21 toolchain, and its bundled
Kotlin compiler (2.0.21) throws `IllegalArgumentException: 25.0.3` trying to
parse a JDK 25 version string -- it predates JDK 25's existence. If a Gradle
daemon or the separate long-lived Kotlin compiler daemon (`ps aux | grep -i
kotlin`) already started under the wrong JDK before you fix this, `./gradlew
--stop` and killing that Kotlin daemon are both necessary -- neither one
picks up a JDK change on its own.

Verified: the service above answers `/api/isalive` and `/api/health` at
`http://localhost:8070`, matching `config.toml`'s `grobid_url` default.
**Not re-verified**: the Python-side probe
(`src.heavy.grobid_extract.is_available()`) against a live `.venv-full/` --
that venv doesn't currently exist on this host (rerun Quickstart step 1 to
recreate it).

`docker/Dockerfile` + `docker/setup.sh` scaffold the same environment inside
Ubuntu 24.04 (per the original container design) using the exact same
`scripts/install_full_pipeline.sh` as the host, and are now also pinned to
`openjdk-21-jdk-headless` for the reason above. **The Dockerfile itself has
still not been built or run in this session** -- no Docker daemon is
available on this host -- but the packages it installs
(`docker/requirements-full.txt`) were verified in a host venv; see that
file's header for exactly what was and wasn't (the honest answer is
per-stage, not one yes/no -- see `scripts/full_pipeline.py`'s docstring and
`src/heavy/*.py`).

Retrieval by default is a keyword-overlap ranker (`src/retrieval.py`, stdlib
only) -- deliberately: the corpus is still small enough that embeddings are
overhead without payoff for the genre skills' day-to-day use.
`src/heavy/embed_index.py` (sentence-transformers + Chroma) is a verified,
working upgrade with a matching `search(query, k)` signature, for when
that stops being true.

## The heavy pipeline

`scripts/full_pipeline.py` runs Docling -> GROBID/Zotero ->
sentence-transformers/Chroma -> BERTopic -> PaperQA2 -> STORM -> Pandoc/LaTeX
as one script for both the host and Docker targets:

```bash
.venv-full/bin/python scripts/full_pipeline.py --stages embed,bertopic
.venv-full/bin/python scripts/full_pipeline.py --stages paperqa --question "..."
.venv-full/bin/python scripts/full_pipeline.py --stages storm --topic "..."
```

Each stage self-probes its prerequisites and reports honestly
(`ok`/`skipped`/`no-api-key`/`missing-binary`) instead of assuming the
target implies availability -- see `src/heavy/*.py` docstrings for what's
been verified and how.

## Open questions / not yet built

Everything in this section is **proposed, not implemented** -- analysis and
a recommendation, not shipped behavior. Distinct from the verified-vs-unverified
distinction elsewhere in this doc: nothing here has code behind it yet.

### What's missing to run this as a cron job monitoring Zotero/the bib file?

In priority order:

1. **Bib-file freshness is the blocker, not an afterthought.** With no
   Better BibTeX plugin installed, `bibliography.bib` is a manual,
   point-in-time export -- a cron job watching only its mtime does nothing
   until a human clicks Export in Zotero, even though `zotero.sqlite` itself
   is always live.
2. **The heavy stages have no incremental skip logic.** `python -m src.sync`
   already is incremental (a paper is only re-parsed if its PDF hash
   changed) -- safe to run every few minutes. `src/heavy/embed_index.py` and
   `src/heavy/topic_model.py` are **not**: they rebuild/reprocess every
   document on every call. A cron job that also re-runs
   `full_pipeline.py --stages docling,embed,bertopic` on a schedule would
   re-run Docling over all 5 PDFs every tick -- 373 seconds and the same
   swap pressure documented above, for zero new information. This needs the
   same content-hash-based skip logic `ledger.py` already has for the core
   pipeline, extended to the heavy stages.
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
6. **Cron's minimal environment.** A crontab entry needs the venv's Python
   invoked by absolute path (`/workspace/git/automated-research/.venv-full/bin/python`)
   -- cron doesn't source your shell profile or activate venvs.

## Repository layout

```
config.toml              central config -- paths, GROBID URL, embedding model
bibliography.bib          Zotero export -- source of truth for citekeys/metadata
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
  paperqa_answer.py, storm_synthesize.py, render_output.py
scripts/
  install_full_pipeline.sh  single install path for host + Docker
  full_pipeline.py           orchestrates src/heavy/* stages
content/                  generated, gitignored (regenerate with sync)
  ledger.sqlite, parsed/<citekey>.txt, provenance/,
  docling/, chroma/, topics.json, paperqa/, storm/, rendered/  (src/heavy/ outputs)
source-pdfs/              raw PDFs outside Zotero -- see src/heavy/corpus.py; never citable
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer, tutorial-writer, deep-research
.claude/agents/           deep-research's subagents: deep-research-interviewer, deep-research-writer
docker/                   Dockerfile + setup.sh (GROBID/TeX Live/Pandoc) -- Dockerfile unverified
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

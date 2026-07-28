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
- **Genre layer** (generative, on-demand, reviewed by you): three Claude Code
  skills in `.claude/skills/` -- `survey-writer`, `thesis-chapter-writer`,
  `tutorial-writer` -- each a thin template over the same content layer.

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

This host has no root, no Java, no TeX Live, no Pandoc, and `pip install`
outside a venv is blocked (PEP 668 / externally-managed-environment).

| Capability | Here | Needs `docker/` |
|---|---|---|
| Parse bib file, track citekeys + PDF paths | `bibtexparser` (venv) | -- |
| Extract PDF text | `pdftotext` (already present) | -- |
| Track parse status incrementally | stdlib `sqlite3` | -- |
| Keyword-based retrieval | stdlib only | -- |
| Citation verification gate | stdlib `re`, no venv needed | -- |
| Docling layout-aware parsing, embeddings/Chroma, BERTopic | venv (`src/heavy/`) | also works, verified |
| PaperQA2 / STORM (needs an LLM key) | installs, no key configured here | also works if given a key |
| Bibliographic-quality parsing (GROBID: references, sections) | -- | needs Java |
| Compiling generated `.tex` chapters to PDF (Pandoc/TeX Live) | -- | needs root (apt) |

`docker/Dockerfile` + `docker/setup.sh` scaffold the full environment (Ubuntu
24.04, per the original container design) using the exact same
`scripts/install_full_pipeline.sh` as the host. **The Dockerfile itself has
not been built or run in this session** -- no Docker daemon is available on
this host -- but the packages it installs (`docker/requirements-full.txt`)
were verified in a host venv; see that file's header for exactly what was
and wasn't (the honest answer is per-stage, not one yes/no -- see
`scripts/full_pipeline.py`'s docstring and `src/heavy/*.py`).

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

### Should `sync` also read `zotero.sqlite` directly, alongside the bib file?

Advantage: `zotero.sqlite` is always live; `bibliography.bib` is a manual,
point-in-time export (no Better BibTeX) that goes stale the moment you add
a paper in Zotero without re-exporting. Reading the sqlite file too would
let `sync` notice that drift instead of silently working from an outdated
bibliography.

**Recommendation: yes, but only as a read-only staleness check, never as a
second citekey source.** Concretely: compare the count (and ideally the
identity, matched DOI -> URL -> title in that priority order) of live
Zotero items against `bibliography.bib` entries, and warn -- "Zotero has N
items; bibliography.bib has M; re-export if this looks wrong" -- without
ever assigning a citekey to something that isn't in the bib file.

The reason for that limit, not just a style preference: Zotero's native
BibTeX export doesn't include the internal item key, so there is no clean
foreign key between a sqlite row and a bib entry -- matching is inherently
heuristic. This repo's own two entries demonstrate both failure modes:
`talasila_composable_2025` has a DOI and matches cleanly; `noauthor_digital_nodate`
has neither DOI nor author, only a scraped title, and would only match
reliably on URL. Letting sqlite-derived items mint their own citekeys again
(even "provisionally") reopens exactly the fabrication risk this pipeline
exists to prevent -- that's the tradeoff, and it's a hard "no," not a soft one.

Feasible via `config.toml`: yes -- an optional `[zotero] data_dir` setting,
absent by default, enabling a small read-only diagnostic module. Not built.

### Why can't the pipeline just download PDFs for bib entries automatically?

This isn't hypothetical -- `source-pdfs/manifest.json` and `reading-notes.md`
(from an earlier research pass over 21 candidate papers) already document
every failure mode encountered:

1. **Genuinely closed-access papers** -- no legal open copy exists anywhere.
   No automated fix exists or should exist; the real path is institutional
   access (your university library proxy) via an authenticated human session
   -- exactly what Zotero's browser connector already does when you save a
   paper yourself.
2. **Anti-bot walls on nominally open-access papers** (IEEE Xplore, MDPI,
   Taylor & Francis blocked automated fetches in the existing manifest even
   though the content is legitimately OA). **Not recommended, full stop**:
   headless-browser automation or proxies to get past these would be evading
   an access control the publisher put there on purpose, and that doesn't
   become fine because the underlying content happens to be open-access.
3. **DOI resolves to an HTML landing page, not a direct PDF link**
   (ScienceDirect, PNAS, Frontiers in the existing manifest). **This one has
   a legitimate fix, worth building**: many publishers (the Highwire Press /
   Google Scholar convention) embed a `<meta name="citation_pdf_url" ...>`
   tag on the landing page specifically so it can be found by automated
   tools -- reading that tag isn't evasion, it's using metadata published
   for exactly this purpose, and would likely recover several of the
   landing-page-only cases already logged in the manifest.

Not built: a landing-page-fetch step (case 3) gated on Unpaywall/OpenAlex
already reporting the DOI as open-access.

### What's missing to run this as a cron job monitoring Zotero/the bib file?

In priority order:

1. **The bib-file freshness gap above is the blocker, not an afterthought.**
   With no Better BibTeX, a cron job watching only `bibliography.bib`'s mtime
   does nothing until a human clicks Export in Zotero. The sqlite
   staleness-check proposed above is the piece that would make "monitors
   Zotero" mean something continuous rather than "monitors whether you
   remembered to export."
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

Already solved, not a gap: reading `zotero.sqlite` safely while Zotero is
open (`mode=ro&immutable=1`, proven in this session).

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
.claude/skills/           genre layer: survey-writer, thesis-chapter-writer, tutorial-writer
docker/                   Dockerfile + setup.sh (GROBID/TeX Live/Pandoc) -- Dockerfile unverified
```

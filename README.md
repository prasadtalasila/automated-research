<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="docs/logo-dark.svg">
    <img src="docs/logo.svg" alt="chitragupta" height="72">
  </picture>
</p>

<p align="center">
Turns a BibTeX bibliography into grounded survey papers, thesis chapters,
undergraduate textbook chapters and hands-on tutorials, with every citation
traceable back to a paper the bibliography actually holds.
</p>

<p align="center">
Named for the Hindu god who keeps the ledger of every deed and audits souls
against it -- which is what this does to citations. <a href="docs/NAME.md">See more</a>.
</p>

---

## The one rule

Fabricated placeholder references have made it into real papers before.
This pipeline is built to make that impossible rather than unlikely:

> **A citekey may only be used if it appears in your own `.bib` export
> *and* was picked up into the ledger by a real parse of a real PDF.**

- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [The enrichment layer](#the-enrichment-layer)
- [Hardware requirements](#hardware-requirements)
- [Documentation](#documentation)
- [Acknowledgements](#acknowledgements)

## How it works

Five phases. You own the first, the machine owns the fourth, and nothing
reaches the fifth without passing it.

```mermaid
flowchart LR

  P0["<b>0 · CURATE</b><br/><i>you, in Zotero</i><br/><br/>Add papers, export<br/>BibTeX + Export Files<br/><br/><b>papers/bibliography.bib</b><br/><small>nothing else may invent a citekey</small>"]

  P1["<b>1 · SYNC</b><br/><i>the corpus layer — deterministic, no LLM</i><br/><br/><code>python -m src.sync</code><br/>read bib → update ledger<br/>→ extract PDF text<br/><br/><b>content/ledger.sqlite</b><br/><b>content/parsed/*.txt</b><br/><small>idempotent · re-runs cost almost nothing</small>"]

  P2["<b>2 · DRAFT</b><br/><i>the drafting layer — generative, you review</i><br/><br/>Ask a genre skill:<br/><i>“write a survey section on …”</i><br/>it retrieves only from<br/>the parsed corpus<br/><br/><b>content/drafts/&lt;slug&gt;.md</b>"]

  P3{{"<b>3 · VERIFY</b><br/><i>machine-enforced</i><br/><br/><code>src.citation_gate</code><br/>Is every citekey<br/>in the ledger?"}}

  P4["<b>4 · PUBLISH</b><br/><i>stdlib + Pandoc / TeX Live</i><br/><br/><code>src.references</code><br/>IEEE list from exactly<br/>the citekeys cited<br/><br/><code>render_output --format pdf</code><br/><b>content/rendered/&lt;slug&gt;.pdf</b>"]

  FIX["<b>refused — exit 1</b><br/><br/>Use a citekey that exists,<br/>or add the paper in Zotero,<br/>re-export and re-sync.<br/><br/><small>A FAIL is treated like a<br/>failing test, not a lint warning.</small>"]

  P0 ==> P1 ==> P2 ==> P3
  P3 == "PASS · exit 0" ==> P4
  P3 -- "FAIL" --> FIX
  FIX -. "back to the bibliography" .-> P0

  classDef you fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef det fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:3px,color:#450a0a
  classDef out fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a

  class P0 you
  class P1 det
  class P2 gen
  class P3 gate
  class P4 out
  class FIX bad
```

Two properties of that picture do all the work:

- **Phase 0 is the only entrance.** Citekeys come from your reference
  manager's BibTeX export. The pipeline never fetches a paper, never
  invents a citekey, and never renames one.
- **Phase 3 is the only exit.** `src.citation_gate` sits on the single
  path between a draft and a rendered document. There is no arrow around
  it, and a `FAIL` is treated like a failing test rather than a lint
  warning.

Five genre skills sit behind phase 2 -- survey, thesis chapter,
undergraduate textbook chapter, tutorial, and a heavier multi-perspective
deep-research mode -- and all five obey the same grounding rules.

## Quickstart

```bash
# 1. Export Zotero's library: format BibTeX, tick "Export Files", and save
#    it as `bibliography` inside papers/. Zotero writes the .bib plus a
#    companion attachment folder beside it:
#      papers/bibliography.bib
#      papers/bibliography/files/<id>/<name>.pdf
#    Each entry's file field is a path relative to the .bib, so don't
#    rename or move that folder afterwards -- see docs/ZOTERO.md.
#      Ex: file = {Full Text PDF:bibliography/files/16/paper-name.pdf:application/pdf}
mkdir -p papers && cp -r /path/to/your/export/. papers/

# Optional, and a different mechanism: papers/pdfs/ is where you drop a
# raw PDF you have not cataloged in Zotero yet. The enrichment layer will
# read it, but it has no citekey and can never be cited -- catalog it and
# re-export to do that. Zotero's own exported attachments don't go here.

cp config.toml.example config.toml

# 2. Install dependencies. `all` = OS packages (pdftotext, TeX Live,
#    Pandoc) plus the Python ones; with no stage it installs the Python
#    ones only.
pipx install poetry
bash scripts/install_full_pipeline.sh all

# 3. Sync the corpus layer from papers/bibliography.bib. A citekey that
#    later drops out of the bib file (a paper removed from your reference
#    manager) is only *reported* by default; re-run with --remove-stale
#    to actually delete its ledger row once you've reviewed the reported
#    list (see "Removing a paper" below) -- not needed on a first run.
source .venv-full/bin/activate
python -m src.sync

# ...and only once you've read the stale list it prints, and agree with it:
# python -m src.sync --remove-stale

# 4. Inspect what it found. Read-only, takes no lock (so it works while a
#    sync is running), and needs no venv.
python3 -m src.ledger

# 5. In Claude Code, ask for a draft, e.g.:
#    "write a survey section on digital twin composability"
#    "draft a thesis chapter on runtime verification for autonomous robots"
#    "write a textbook chapter introducing digital twin asset reuse"
#    "write a tutorial that builds a minimal digital twin asset from scratch"
# The matching skill in .claude/skills/ picks this up automatically,
# including its own citation_gate -> references -> render_output chain

# 6. Manually re-run any step of that chain yourself (no venv needed for any of these)
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md --heading "References"    # --heading default: "References"
python3 -m src.heavy.render_output path/to/draft.md --format pdf     # also: --csl, --no-collapse-citations, --documentclass, --fontsize, --margin (--help for all)
python3 -m src.heavy.render_output path/to/draft.md --format md      # numbered Markdown copy in content/rendered/ (no pandoc needed)

# 7. Check the draft against its sources. Review aids, not gates: none of
#    these runs automatically, and none of them can block a draft.
python3 -m src.citation_provenance path/to/draft.md                  # what in each source supports the claim citing it
python3 scripts/verbatim_check.py overlap path/to/draft.md <citekey> # wording shared with that source
python3 -m src.citation_coverage path/to/draft.md --query "..."      # retrieval found it -- did the draft cite it?

# 8. Optional, and only when you want it: the enrichment layer. Layout-aware
#    parsing, semantic search and topic clustering over the whole corpus.
#    Nothing above needs it, and no skill builds it for you -- see
#    docs/RETRIEVAL.md for which stage is worth your time.
.venv-full/bin/python scripts/full_pipeline.py --stages docling,embed
```

Exporting from Zotero in detail, including the attachment-path trap that
silently leaves every entry without a PDF, is in
[docs/ZOTERO.md](docs/ZOTERO.md). Every command and which interpreter it
needs is in [docs/CLI.md](docs/CLI.md). Every setting -- including
`[parser].backend`, which decides how faithfully your PDFs are read -- is
in [docs/CONFIG.md](docs/CONFIG.md). What each of these commands is part
of, and why some need `.venv-full/bin/python` while others run on bare
`python3`, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Two of those commands rewrite the shared corpus layer -- `sync` and the
enrichment layer -- and they share one lock, so the second to start exits
`2` rather than interleaving. Everything else only reads, which is why
`python3 -m src.ledger` and the citation gate work fine while a sync is in
progress.

Removing a paper: delete the entry in Zotero, re-export, re-run `sync`.
By default `sync` only *reports* citekeys that dropped out of the bib
file -- it doesn't delete their `content/ledger.sqlite` row until you
re-run with `--remove-stale`. This is deliberate: a bib export that comes
back short a citekey is far more often a botched re-export or `BIB_FILE`
pointing at the wrong path than an intentional deletion, so the default
keeps the ledger untouched until a human confirms. `--remove-stale` still
refuses if the bib file comes back completely empty against a non-empty
ledger, for the same reason -- fix the export or path rather than
deleting everything in one run.

## The enrichment layer

Everything above works without it. The enrichment layer is a second,
optional pass over the same corpus that buys three things: layout-aware
parsing that yields quotable passages, semantic search that finds a paper
arguing your point in different words, and topic clustering over the whole
corpus.

```bash
.venv-full/bin/python scripts/full_pipeline.py --stages docling,embed
.venv-full/bin/python scripts/full_pipeline.py --stages render --input draft.md
```

It costs real time and disk -- a first full-corpus parse is measured in
tens of minutes, and the heavy dependency group is several gigabytes -- so
you build it deliberately. **No genre skill builds it for you.** The
skills read what is already there and fall back to the lightweight default
when it isn't.

Which stage is worth that cost, and what each one actually answers, is in
[docs/RETRIEVAL.md](docs/RETRIEVAL.md). How the stages fit into the rest
of the system, including how to call them from your own script or skill,
is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#the-enrichment-layer).

No stage needs an LLM API key -- this repository intentionally has none.
Every stage probes its own prerequisites and reports `ok`, `skipped` or
`missing-binary` rather than assuming they are present.

## Hardware requirements

The table below is what the pipeline needs, not what it was developed
on. The specific observations behind it come from two reference
machines, named here because [docs/PERFORMANCE.md](docs/PERFORMANCE.md)
refers back to them -- **treat every measured figure as that machine's,
and expect yours to differ**:

- **the small machine** -- 4 cores, 9.7GB RAM (~3GB actually free), no GPU.
- **the multi-GPU machine** -- 4x NVIDIA A40 46GB, 96 cores (48 available
  to the process), 251GB RAM, driver 555.42.02, verified 2026-07-30.


| Resource | Minimum (corpus layer only) | Recommended (`src/heavy/` in regular use) |
|---|---|---|
| Disk | ~1GB (bibtexparser + content/) | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in twice over via sentence-transformers/docling, plus docling's own layout/OCR models); TeX Live adds several GB more on top |
| RAM | ~1-2GB (sync, citation_gate, keyword retrieval are all lightweight) | **8GB minimum, 16GB+ better**. At ~3GB free, Docling parsing a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it still finished, just slowly. Bigger PDFs or a bigger corpus will make this worse |
| CPU | 1-2 cores | **4+ cores** without a GPU -- Docling's layout inference and BERTopic's UMAP/HDBSCAN are CPU-bound if there's no GPU to offload to; more cores directly reduces wall-clock time |
| GPU | none needed | **none required**, but if present, `scripts/install_full_pipeline.sh`'s `ensure_gpu_torch` detects the NVIDIA driver's supported CUDA ceiling (`nvidia-smi`) and automatically reinstalls torch from a matching CUDA-tagged wheel index -- verified end-to-end on the multi-GPU machine (driver capped at CUDA 12.5; the default pip/Poetry-resolved torch wheel needed CUDA 13 and silently ran CPU-only until this ran). sentence-transformers/Docling/BERTopic all then use the GPU automatically |
| Network | needed once, for `poetry install` | also needed for first-run model downloads (the embedding model, Docling's layout/OCR models) |

Tips:
- **No GPU, disk tight**: `pip`/Poetry's default torch wheel pulls a full
  set of `nvidia-*` CUDA packages even with no GPU present (several GB,
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

## Documentation

This file is the overview: what the pipeline is, how to get it running,
and what it needs. Everything else lives in one document per question,
split by who is asking.

### Using it

**Getting started**

| Document | Answers |
|---|---|
| [docs/ZOTERO.md](docs/ZOTERO.md) | How do I get my library and its PDFs into the shape this expects? Includes the attachment-path trap that silently leaves every entry without a PDF |
| [docs/CLI.md](docs/CLI.md) | What commands are there, what flags does each take, and which interpreter does it need? |
| [docs/CONFIG.md](docs/CONFIG.md) | What settings exist, what values does each accept, and what is the default? Starts with a minimal `config.toml` |

**Understanding the system**

| Document | Answers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | What actually runs, what does each part write, which parts are optional, and why do some commands need the venv? |
| [docs/DIAGRAMS.md](docs/DIAGRAMS.md) | The workflow drawn eleven ways -- six by depth, three by genre, two in an appendix. Pick the one that matches what you already know |
| [docs/RETRIEVAL.md](docs/RETRIEVAL.md) | BM25, embeddings, topic models -- which one answers my question, and which is worth building? |

**Choosing settings**

| Document | Answers |
|---|---|
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | What does each setting *cost*? Every measured figure in one place, organised by setting |
| [docs/PDF-PARSER.md](docs/PDF-PARSER.md) | Which PDF backend should I use, and why were two others dropped? |

**Reading the output**

| Document | Answers |
|---|---|
| [docs/CITATION-PROVENANCE.md](docs/CITATION-PROVENANCE.md) | What does the provenance report say, and how do I read it? |
| [docs/WRITING-STANDARDS.md](docs/WRITING-STANDARDS.md) | What prose standards do the genre skills follow, and where in the technical-communication literature do they come from? |

### Working on it

| Document | Answers |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | How is this put together, what patterns does it lean on, and what happens when two runs collide? |
| [docs/PARALLELISM.md](docs/PARALLELISM.md) | How does the parallel parse actually work, what is each component for, and what is planned next? |
| [DEVELOPER.md](DEVELOPER.md) | How do I run the tests, where does everything live, and what is unbuilt? |
| [DOCKER.md](DOCKER.md) | How do I run this in a container? |
| [AGENTS.md](AGENTS.md) | The rules a coding agent working here must follow -- above all, never fabricate a citekey |

Everything under `docs/` ships in the release archive. `DEVELOPER.md`,
`AGENTS.md`, `tests/` and `bench/` (the measurement harness and its raw
timings) are in the repository but excluded from it.

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

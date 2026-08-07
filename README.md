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

Five phases across three layers. You own phase 0, the **corpus layer**
owns phase 1, the **drafting layer** owns 2 through 4, and nothing reaches
phase 4 without passing phase 3.

<p align="center">
  <img src="docs/diagrams/svg/v1-overview.svg"
       alt="Five phases: curate in Zotero, sync the corpus, draft with a genre skill, verify with the citation gate, publish. A failing gate sends the draft back to be rewritten."
       width="100%">
</p>

Two properties of that picture do all the work:

- **Phase 0 is the only entrance.** Citekeys come from your reference
  manager's BibTeX export. The pipeline never fetches a paper, never
  invents a citekey, and never renames one.
- **Phase 3 is the only exit.** `src.citation_gate` sits on the single
  path between a draft and a rendered document. There is no arrow around
  it, and a `FAIL` is treated like a failing test rather than a lint
  warning.

The loop back from a failed gate goes to *drafting*, not to you: the skill
discards the unsupported claim and writes again, so a gate failure is
normally something you never see. You only get involved in the rarer case
where the paper genuinely isn't in the corpus yet -- the dotted arrow back
to phase 0.

Five genre skills sit behind phase 2 -- survey, thesis chapter,
undergraduate textbook chapter, tutorial, and a heavier multi-perspective
deep-research mode -- and all five obey the same grounding rules. An
optional third layer, **enrichment**, deepens the same corpus with
layout-aware parsing, semantic search and topic clustering; nothing above
needs it.

The figure is `docs/diagrams/svg/v1-overview.svg`, rendered from
[docs/DIAGRAMS.md](docs/DIAGRAMS.md), which draws this workflow eleven
ways -- by depth, by genre, and in time order.

### One thing the corpus layer does not promise

The corpus layer is deterministic in the sense that matters most -- no
LLM, no judgement calls, same bibliography in, same citekeys out -- but
it is **not** bit-reproducible with every parser. Re-running it over
**unchanged inputs** with the default `pdftotext` backend, parsed text
comes back byte-identical and every ledger column is stable except the
`last_synced` timestamp.

"Unchanged inputs" is doing real work in that sentence. Re-exporting
`bibliography.bib` gives its PDFs fresh modification times, and that is a
different input: `pdf_mtime_ns` legitimately changes, even for a
byte-identical file.

With the opt-in `docling` backend and a worker pool, it is not. Docling
groups dense reference blocks slightly differently under load, so around
1.4% of documents come back with different text and about 1% with a
different *quotable passage* -- which means the exact span quoted from a
source can change between runs. Two runs of the *same* configuration are
not exempt, at roughly a third of that rate. Serial parsing
(`[parser].workers = 1`, the default) has not been observed to vary.

That is Docling's behaviour, not something this pipeline adds, and it
cannot be switched off. If it matters to you, keep `workers = 1` or use
`pdftotext`. The full artifact-by-artifact contract is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#what-is-reproducible-and-what-is-not).

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

cp config.toml.example config.toml

# 2. Install dependencies. scripts/install_full_pipeline.sh is the only
#    install path -- one script for a bare host and for the Docker image,
#    taking stage names as positional arguments:
#      python-deps  creates .venv-full/ and runs `poetry install --with
#                   enrich` into it. The default when no stage is given.
#      os-deps      apt-gets pdftotext, Pandoc, TeX Live, git/curl/unzip.
#                   Needs root; auto-sudo's. Opt-in.
#      dev-deps     pytest + pytest-cov, to run the test suite.
#      all          os-deps + python-deps (NOT dev-deps).
#    Poetry has to exist first -- either install it yourself, as here, or
#    let the os-deps stage do it.
pipx install poetry
bash scripts/install_full_pipeline.sh all

# ...and, only if you want to run the test suite:
# bash scripts/install_full_pipeline.sh dev-deps
# .venv-full/bin/python -m pytest

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
python3 -m src.render_output path/to/draft.md --format pdf     # also: --csl, --no-collapse-citations, --documentclass, --fontsize, --margin (--help for all)
python3 -m src.render_output path/to/draft.md --format md      # numbered Markdown copy in content/rendered/ (no pandoc needed)

# 7. Check the draft against its sources. Review aids, not gates: none of
#    these runs automatically, and none of them can block a draft.
python3 -m src.citation_provenance path/to/draft.md                  # what in each source supports the claim citing it
python3 scripts/verbatim_check.py overlap path/to/draft.md <citekey> # wording shared with that source
python3 -m src.citation_coverage path/to/draft.md --query "..."      # retrieval found it -- did the draft cite it?

# 8. Optional, and only when you want it: the enrichment layer. Layout-aware
#    parsing, semantic search and topic clustering over the whole corpus.
#    Nothing above needs it, and no skill builds it for you -- see
#    docs/RETRIEVAL.md for which stage is worth your time.
.venv-full/bin/python scripts/enrich.py --stages docling,embed
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
.venv-full/bin/python scripts/enrich.py --stages docling,embed
.venv-full/bin/python scripts/enrich.py --stages render --input draft.md
```

It costs real time and disk -- a first full-corpus parse is measured in
tens of minutes, and the enrich dependency group is several gigabytes -- so
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

What the pipeline needs, not what it was developed on. The split below is
the one that matters: the **corpus layer** -- `sync`, the citation gate,
keyword retrieval -- is light enough for any laptop, and the optional
**enrichment layer** is what costs real disk and real time.

| Resource | Minimum (corpus layer only) | Recommended (enrichment layer in regular use) |
|---|---|---|
| Disk | ~1GB | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in twice over via sentence-transformers/docling, plus docling's own layout/OCR models); TeX Live adds several GB more |
| RAM | ~1-2GB | **8GB minimum, 16GB+ better**. At ~3GB free, Docling on a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it finished, just slowly |
| CPU | 1-2 cores | **4+ cores** -- without a GPU, Docling's layout inference and BERTopic's UMAP/HDBSCAN are CPU-bound, and more cores directly cut wall-clock time |
| GPU | none needed | **none required.** If one is present the installer detects it and torch is set up to use it automatically -- worth ~4.7x on the parse |
| Network | once, for `poetry install` | also for first-run model downloads (the embedding model, Docling's layout/OCR models) |

**For a sense of scale at the top end:** this project's own bibliography
-- 501 PDFs, 13,400 pages, 1.54GB -- parses in **about 4 minutes** on a
96-core machine with four A40s, against **1h 56m** serially on that same
host. On ordinary hardware a first full Docling parse is measured in tens
of minutes. A *second* run over an unchanged corpus costs close to
nothing either way, because every stage skips what hasn't changed -- which
is what makes it safe to put `sync` on a schedule.

Every measured figure in this project comes from one of two reference
machines: **the small machine** (4 cores, 9.7GB RAM, no GPU) and **the
multi-GPU machine** (96 cores, 251GB RAM, 4x NVIDIA A40 -- the one in the
paragraph above). **Treat each figure as that machine's, and expect yours
to differ.** [docs/PERFORMANCE.md](docs/PERFORMANCE.md) has their full
specifications, what each setting costs, and the two install-time traps
worth knowing before you start (a CPU-only host pulling several GB of
unused CUDA packages, and a GPU host where `torch.cuda.is_available()`
comes back `False`).

## 📖 Research Citation

When Chitragupta is used in academic work, the following reference may be used:

```bibtex
@software{talasila2026chitragupta,
author = {Prasad Talasila},
title = {Chitragupta: An automated research pipeline for literature review and thesis drafting},
year = {2026},
url = {https://github.com/prasadtalasila/chitragupta},
publisher = {GitHub}
}
```

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
| [docs/LADDERS.md](docs/LADDERS.md) | Where does the pipeline choose between two ways of doing one job? Every ladder it walks for you and every tier you pick yourself, and what the bottom rung costs |
| [docs/RETRIEVAL.md](docs/RETRIEVAL.md) | BM25, embeddings, topic models -- which one answers my question, and which is worth building? |

**Choosing settings**

| Document | Answers |
|---|---|
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | What does each setting *cost*? Every measured figure in one place, organised by setting |
| [docs/PDF-PARSER.md](docs/PDF-PARSER.md) | Which PDF backend should I use, why were two dropped, and why was each newer candidate not adopted? |

**Reading the output**

| Document | Answers |
|---|---|
| [docs/CITATION-PROVENANCE.md](docs/CITATION-PROVENANCE.md) | What does the provenance report say, and how do I read it? |
| [docs/WRITING-STANDARDS.md](docs/WRITING-STANDARDS.md) | What prose standards do the genre skills follow, and where in the technical-communication literature do they come from? |

### Working on it

| Document | Answers |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Why does this refuse what it refuses? The hard constraints, the conflict policy when two runs collide, and the failure analysis behind both |
| [docs/PARALLELISM.md](docs/PARALLELISM.md) | How does the parallel parse actually work, what is each component for, and what is planned next? |
| [docs/GROBID-CITATION-GRAPH.md](docs/GROBID-CITATION-GRAPH.md) | **A proposal, not a plan.** What would it take to build a corpus-internal citation graph, and is it worth a JDK and a long-running service? |
| [DEVELOPER.md](DEVELOPER.md) | How do I run the tests, where does everything live, and what is unbuilt? |
| [DOCKER.md](DOCKER.md) | How do I run this in a container? |
| [SOUL.md](SOUL.md) | One page: why this exists, the one invariant, and what it refuses to become |
| [AGENTS.md](AGENTS.md) | The rules an agent *drafting with* this pipeline must follow -- above all, never fabricate a citekey |
| [DEVELOPER-AGENTS.md](DEVELOPER-AGENTS.md) | The rules an agent *changing this repo* must follow -- test policy, the local check suite, commit/PR/release conventions |

Everything under `docs/` ships in the release archive, as do `SOUL.md`,
`AGENTS.md`, `DEVELOPER-AGENTS.md` and `.claude/`'s genre skills.
`DEVELOPER.md`, `tests/` and `bench/` (the measurement harness and its raw
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

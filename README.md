# Automated Research Pipeline

Turns a BibTeX bibliography into grounded survey papers, thesis chapters,
and undergraduate tutorial chapters, with every citation traceable back to
a paper the bibliography actually holds.

## Table of contents

- [Quickstart](#quickstart)
  - [Exporting your library from Zotero](#exporting-your-library-from-zotero)
- [Venv requirement](#venv-requirement)
- [Hardware requirements](#hardware-requirements)
- [Configuration](#configuration)
  - [Choosing a parser backend](#choosing-a-parser-backend)
  - [Choosing an embedding model](#choosing-an-embedding-model)
- [Architecture](#architecture)
  - [What works on this host](#what-works-on-this-host)
- [The heavy pipeline](#the-heavy-pipeline)
  - [Calling the heavy pipeline from a skill or agent](#calling-the-heavy-pipeline-from-a-skill-or-agent)
- [Developer material](#developer-material)
- [Acknowledgements](#acknowledgements)

See also: [DEVELOPER.md](DEVELOPER.md) (tests, repo layout, open
questions) and [DOCKER.md](DOCKER.md) (running this repo in a container).

## Quickstart

```bash
# 1. Export your reference manager's library to BibTeX at
#    papers/bibliography.bib (create the papers/ dir if needed -- it's
#    gitignored, so a fresh clone never has this file yet; see
#    "Configuration" below). Skipping this makes step 3 fail immediately
#    with a clear FileNotFoundError telling you to do exactly this.
mkdir -p papers && cp /path/to/your/exported-library.bib papers/bibliography.bib

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
# (see "Architecture" below).

# 6. Manually re-run any step of that chain yourself (no venv needed for any of these)
python3 -m src.citation_gate path/to/draft.md
python3 -m src.references path/to/draft.md --heading "References"    # --heading default: "References"
python3 -m src.heavy.render_output path/to/draft.md --format pdf     # also: --documentclass, --fontsize, --margin (--help for all)
```

### Exporting your library from Zotero

Step 1 above, in detail, for [Zotero](https://www.zotero.org/) (see its own
[export documentation](https://www.zotero.org/support/kb/exporting) for the
general feature):

1. Right-click the collection you want (or use **File -> Export Library**
   for everything) -> **Export Collection...** / **Export Library...**.
2. Format: **BibTeX**. Check **Export Files** -- without it you get
   metadata only and `pdf_text.py` will have nothing to extract.
3. Save it as `bibliography` directly inside this repo's `papers/`
   directory. Zotero writes `papers/bibliography.bib` plus a **companion
   folder** (`papers/bibliography/`, `files/<id>/<name>.pdf` inside) for
   every attachment -- the exported `.bib`'s `file` field encodes that
   folder's name as a literal relative path, tied to whatever name you
   gave the export at save time.
4. **Don't rename that companion folder afterward.** `src/bib_reader.py`'s
   `_resolve_pdf_path` resolves each entry's `file` field relative to
   wherever `bibliography.bib` itself lives (`papers/`) -- if you rename
   or move the attachments folder, that relative path breaks silently
   (entries just show up as "without a PDF attachment" after `sync`, not
   as an error).
5. Re-run `python -m src.sync`.

This is a **different mechanism from `papers/pdfs/`** (`config.toml`'s
`[source_pdfs].dir`, Quickstart step 1 above): that directory is for any
raw PDF you already have but haven't cataloged in Zotero yet (just a file
you drop there by hand -- this project has no automated fetching from any
external source) -- see [`src/heavy/corpus.py`](src/heavy/corpus.py) and
AGENTS.md's citekey invariant. Zotero's own exported attachments never
belong there, and this project's only supported way to catalog a paper
for citing is a Zotero export.

To add more papers later: add the entry in Zotero, re-export the same way
(re-check **Export Files** so new attachments are included), then re-run
`python -m src.sync`.

Removing a paper: delete the entry in Zotero, re-export, re-run `sync`.
By default `sync` only *reports* citekeys that dropped out of the bib file
(`stale   <citekey> (no longer in bibliography.bib)`, one line per
citekey, plus a single summary note pointing at `--remove-stale`) -- it
doesn't delete their `content/ledger.sqlite` row until you re-run with
`--remove-stale`. This is deliberate: a bib export that comes back short a
citekey is far more often a botched re-export or `BIB_FILE` pointing at the
wrong path than an intentional deletion, so the default keeps the ledger
untouched until a human confirms. `--remove-stale` still refuses (raises)
if the bib file comes back completely empty against a non-empty ledger,
for the same reason -- fix the export/path rather than deleting everything
in one run.

All paths are configurable in `config.toml` (repo root), overridable
per-run with an env var of the same name, e.g. `BIB_FILE=/path/to/other.bib
python -m src.sync`. See ["Configuration"](#configuration) below for the
full settings reference.


## Venv requirement

Every `python -m src.*` / `python scripts/*.py` command below needs the
venv from Quickstart step 2 -- **except** three stdlib-only tools, which
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
| Disk | ~1GB (bibtexparser + content/) | **10-20GB+** -- the full venv alone is **6.0GB** (torch pulled in twice over via sentence-transformers/docling, plus docling's own layout/OCR models); TeX Live adds several GB more on top |
| RAM | ~1-2GB (sync, citation_gate, keyword retrieval are all lightweight) | **8GB minimum, 16GB+ better**. At ~3GB free, Docling parsing a 17-page PDF pushed the process to 3.6GB RSS and the host swapped 6.3GB -- it still finished, just slowly. Bigger PDFs or a bigger corpus will make this worse |
| CPU | 1-2 cores | **4+ cores** without a GPU -- Docling's layout inference and BERTopic's UMAP/HDBSCAN are CPU-bound if there's no GPU to offload to; more cores directly reduces wall-clock time |
| GPU | none needed | **none required**, but if present, `scripts/install_full_pipeline.sh`'s `ensure_gpu_torch` detects the NVIDIA driver's supported CUDA ceiling (`nvidia-smi`) and automatically reinstalls torch from a matching CUDA-tagged wheel index -- verified end-to-end on an A40 (driver capped at CUDA 12.5; the default pip/Poetry-resolved torch wheel needed CUDA 13 and silently ran CPU-only until this ran). sentence-transformers/Docling/BERTopic all then use the GPU automatically |
| Network | needed once, for `poetry install` | also needed for first-run model downloads (the embedding model, Docling's layout/OCR models) |

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
| `[bib]` | `path` | `BIB_FILE` | `papers/bibliography.bib` | The BibTeX export `src/bib_reader.py` parses -- the only source of citekeys (AGENTS.md's hard invariant). Gitignored, per-host -- see DEVELOPER.md's "Repository layout" |
| `[content]` | `dir` | `CONTENT_DIR` | `content` | Where `sync`/heavy-pipeline outputs live: `ledger.sqlite`, `parsed/`, `docling/`, `chroma/`, `topics.json`, `rendered/` |
| `[source_pdfs]` | `dir` | `SOURCE_PDFS_DIR` | `papers/pdfs` | Raw PDFs gathered outside the bib file, no citekey -- see `src/heavy/corpus.py` |
| `[parser]` | `backend` | `PARSER` | `pdftotext` | Which backend `sync` uses to extract PDF text -- `pdftotext` or `docling` -- see below |
| `[parser]` | `long_word_chars` | `PARSE_LONG_WORD_CHARS` | `20` | Word length above which a token counts as "run-together" for the parse-quality guard |
| `[parser]` | `long_word_ratio` | `PARSE_LONG_WORD_RATIO` | `0.01` | Share of such words above which `sync` warns that the parser is losing word boundaries |
| `[parser]` | `min_tokens` | `PARSE_MIN_TOKENS` | `200` | Documents shorter than this are too noisy to judge, and are skipped by the guard |
| `[heavy]` | `embedding_model` | `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Which sentence-transformers model `src/heavy/embed_index.py` loads for semantic search -- see below |
| `[provenance]` | `weak_score` | `PROVENANCE_WEAK_SCORE` | `0.20` | Below this share of matched words, `citation_provenance` reports "no support found" -- i.e. check this one first |
| `[provenance]` | `good_score` | `PROVENANCE_GOOD_SCORE` | `0.50` | At or above this, a citation is banded "supported" |
| `[heavy]` | `docling_images` | `DOCLING_IMAGES` | `false` | Whether the Docling stage also extracts figure bitmaps + a `<doc>.figures.json` index -- see [DEVELOPER.md](DEVELOPER.md#figures-and-copyright). Changing it re-parses the whole corpus |
| `[heavy]` | `docling_image_scale` | `DOCLING_IMAGE_SCALE` | `2.0` | Render scale for those bitmaps (~144 DPI) |

### Choosing a parser backend

`src/pdf_text.py` (used by `sync`, i.e. job 1) dispatches to one of two
backends based on `config.PARSER`:

- `pdftotext` (default)
- `docling`

The dispatch is a table (`_EXTRACTORS`), so adding a backend is one
`_extract_*` function plus one entry -- a third, `markitdown`, was added
and later removed through that same seam (see
[PDF-PARSER.md](PDF-PARSER.md#why-markitdown-was-removed)).

Setting `config.PARSER = "docling"` here does **not** fold Docling into
job 1, and doesn't make `src/heavy/docling_parse.py` (job 2's own,
always-Docling, corpus-wide stage) redundant, even though both end up
calling the same library. They're two independent, purpose-built
consumers of Docling, not one feature split across two files:

- **Job 1's `pdf_text.py`** extracts plain text per citekey, on `sync`,
  into `content/parsed/<citekey>.txt` -- one file in, one text file out,
  used by the lightweight BM25 retrieval below. Docling here is just a
  higher-fidelity substitute for `pdftotext`'s job.
- **Job 2's `docling_parse.py`** (`scripts/full_pipeline.py`, opt-in)
  produces structured Markdown for the whole corpus at once into
  `content/docling/<citekey>.md`, feeding the embeddings/Chroma and
  BERTopic stages that need real reading order and section boundaries,
  not raw text. It always uses Docling regardless of `config.PARSER`,
  because those downstream stages specifically need what only Docling
  produces.

Switching `config.PARSER` to `"docling"` changes what `sync` writes and
BM25 searches over; it has no effect on whether the heavy pipeline's own
Docling stage runs, since that stage doesn't consult `config.PARSER` at
all.

| Backend | Speed | Dependency | Page boundaries in output? |
|---|---|---|---|
| `pdftotext` (default) | Fastest | `poppler-utils` on PATH, no Python package | Yes -- form-feed characters between pages |
| `docling` | Slowest (~42x `pdftotext` in total, measured on the same 5 real bib PDFs -- see Performance below) | `docling`, "heavy" group | No -- one continuous document |

Losing page boundaries isn't cosmetic: `scripts/verbatim_check.py`'s
`cmd_overlap`/`cmd_locate` report which PDF page a verbatim run came
from by splitting on those form-feed characters, so switching a citekey
to `docling` makes every hit for it report `pdf p.1`
regardless of where the text actually sits. See PDF-PARSER.md for the
full fidelity/speed comparison, including two candidates that were
evaluated and not adopted.

#### Parse-quality guard

`sync` checks each freshly extracted document and warns when an
implausible share of its words are unusually long -- the signature of a
backend that has lost the spaces between words. That failure is easy to
miss by eye and expensive downstream: `src/retrieval.py` tokenizes on
whitespace, so a query term fused into a longer run stops matching
entirely. It is a warning, never a failure: the text is still usable,
and an unusual corpus could trip it legitimately. Thresholds are the
`[parser]` settings in the table above.

#### Performance: measured on 5 real bibliography PDFs

The table above states total-time ratios; here's the underlying,
reproducible measurement, run on this repo's documented A40 host (see
["Hardware requirements"](#hardware-requirements) above), Python 3.12.3,
each backend run serially with no caching (`pdf_text.py` doesn't cache --
these are cold-extraction times, not `sync`'s steady-state, which skips
PDFs whose content hasn't changed). The `markitdown` column is retained
as the record behind its removal -- it is no longer a selectable
backend. Its PDF support was `pdfminer.six`/`pdfplumber`-based,
CPU-only, no GPU involved. Docling's layout/OCR models do use
torch/onnxruntime; GPU utilization was not confirmed for *this* run, but
has since been measured separately and is lower than it looks -- see
["Performance: the whole corpus"](#performance-the-whole-corpus) below.

| Citekey (pages) | `pdftotext` | `markitdown` | `docling` |
|---|---|---|---|
| `abbiati_modelling_2024` (39p) | 0.62s, 15,000 words | 3.90s, 6,705 words | 16.60s, 14,689 words |
| `abduvakhobov_scalable_2024` (10p) | 0.20s, 10,409 words | 2.62s, 3,894 words | 7.45s, 10,408 words |
| `afrin_resource_2021` (29p) | 0.26s, 27,095 words | 14.69s, 30,296 words | 26.58s, 28,032 words |
| `aghaabbasi_digital_2024` (21p) | 0.31s, 8,378 words | 1.76s, 4,584 words | 5.50s, 8,514 words |
| `agrawal_coupled_2024` (8p) | 0.04s¹, 8,006 words | 1.63s, 9,018 words | 4.64s, 7,922 words |
| **Total** | **1.43s, 68,888 words** | **24.60s, 54,497 words** | **60.77s, 69,565 words** |

¹ Sub-100ms, near the practical resolution of this measurement -- the
per-document ratio ranges below exclude this row rather than let one
noisy denominator set an endpoint.

**Speed.** Total-time ratio vs `pdftotext`: markitdown ~17x, docling
~42x. Per-document, excluding `agrawal_coupled_2024`: markitdown ranges
~6x-57x, docling ~18x-102x -- both track document length loosely at
best, so budget for the total-time ratio, not the fastest case.

**Fidelity.** docling's word counts track `pdftotext`'s closely across
all five documents -- 69,565 vs 68,888 in total, every document within
about 3.5% of `pdftotext`'s count. markitdown's counts are inconsistent
rather than uniformly lossy: it undercounts by roughly **45%-63%** on
three of the five
(`abbiati_modelling_2024`: 6,705 vs 15,000, a 55% shortfall), but
*over*counts on the other two (`afrin_resource_2021`: 30,296 vs 27,095).
That inconsistency, not an average, is the reason to spot-check
`markitdown`'s output on a new corpus before trusting it as a drop-in --
a consistent undercount would at least be predictable. Later measurement
found the underlying cause and it was not recoverable through
markitdown's API, which is why it was removed; see
[PDF-PARSER.md](PDF-PARSER.md#why-markitdown-was-removed).

#### Performance: the whole corpus

The five-PDF table above is enough to choose a backend. It is not enough
to answer "how long does a first-time `docling` sync of the whole bib
file take, and what is actually the bottleneck". `bench/` answers that,
reproducibly; the measurement is written up in
[bench/RESULTS.md](bench/RESULTS.md).

Measured 2026-08-02 on the documented A40 host, over all 501 PDFs
(13,400 pages) that `papers/bibliography.bib` resolves:

| | |
|---|---|
| One process, one A40 | **~1.6 hours** (0.43 s/page) |
| One process, CPU only | ~5.1 hours (1.37 s/page) |
| GPU vs CPU, same 6 PDFs | **1.79x** |

The surprise is the last row. During that run the A40 averaged **~7% SM
utilization** and 1.7 GB of its 46 GB, while the process held ~300% CPU
-- three of the 48 available cores. Docling is CPU-bound here (PDF
backend, layout post-processing, and OCR, which runs on the CPU via
onnxruntime), so the GPU buys far less than its presence suggests, and
three of this host's four GPUs are never addressed at all: docling's
`AcceleratorDevice.AUTO` resolves to `cuda:0` for every process.

[bench/PARALLELISM-PLAN.md](bench/PARALLELISM-PLAN.md) is the plan that
follows from this -- CPU-level document parallelism first, GPU
assignment second, in that order because that is where the measurement
says the wall clock is.

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

**What each one actually is**, beyond the table above:

- **`all-MiniLM-L6-v2`**: a 6-layer transformer distilled down from a
  larger model, then fine-tuned by the sentence-transformers project on
  roughly a billion general sentence pairs (paraphrase/QA/NLI-style data)
  with contrastive learning, so semantically similar sentences land close
  together in vector space. That symmetric "compare two texts for
  similarity" training objective is exactly what this codebase's
  prefix-free `encode()` call needs -- no adaptation required, which is
  why it's the default. ~22M parameters is small enough to embed and
  query fast on CPU alone, at the cost of the least semantic nuance of the
  three.
- **`all-mpnet-base-v2`**: same general-purpose sentence-pair training
  recipe as MiniLM, but on a larger 12-layer MPNet backbone (~109M
  parameters, ~5x MiniLM) -- generally the strongest all-around
  sentence-transformers model for semantic similarity when no domain
  specialization is needed. The extra quality costs roughly 4-5x the
  compute and double the vector dimensionality (768 vs. 384), which
  doubles per-chunk storage in Chroma and slows similarity search
  somewhat -- comfortable on the A40 GPU host, noticeably slower on
  CPU-only hardware.
- **`multi-qa-mpnet-base-dot-v1`**: the same MPNet-base backbone as
  `all-mpnet-base-v2`, but fine-tuned specifically on ~215M
  question/answer and query/passage pairs (MS MARCO, Natural Questions,
  Reddit, StackExchange) for dot-product retrieval rather than generic
  sentence similarity. That's the closest conceptual match to what
  `embed_index.search()` actually does -- a short query against a longer
  chunk -- and unlike BGE/E5 below it needs no explicit prefix string to
  invoke that behavior, so it's still a clean drop-in. Same cost profile
  as `all-mpnet-base-v2` (~109M params, 768-dim); no savings, just
  better-targeted training data for this project's actual retrieval
  pattern.

Not recommended without a code change first:

- **`allenai/specter`/`specter2`**: a SciBERT-based model (~110M
  parameters, similar cost to the mpnet options above) trained
  specifically on scientific paper title+abstract pairs, using citation
  graphs as the training signal -- papers that cite each other are pulled
  closer together in embedding space. It's built for whole-paper
  similarity ("find similar papers"), not passage-level retrieval, and
  expects a specific input shape (title `[SEP]` abstract) that doesn't
  match the arbitrary 200-word body-text chunks `chunk_text` actually
  produces from parsed PDFs. Using it well would mean feeding it
  titles+abstracts instead of chunks -- a real code change -- and even
  then it answers "which papers are alike", a different question than
  `embed_index.search()` asks ("which chunk answers this query").
- **BAAI `bge-*` and `intfloat e5-*` families**: general-purpose embedding
  models (33M-335M parameters depending on tier, 384-1024 dimensions)
  trained with large-scale contrastive learning, strong on public
  retrieval benchmarks. Both expect literal `"query: "` / `"passage: "`
  (or similar) prefix strings baked into the input text so the model
  knows which role each side is playing. `get_client_and_model()` adds no
  such prefix on either side -- feeding either family in as-is won't
  error, it'll just silently underperform relative to its benchmark
  numbers, since it never receives the signal it was trained to expect.
  Either family could be worth adopting later, paired with the matching
  prefix-handling code change in `get_client_and_model()` -- not as a
  config-only swap.

To switch models: edit `config.toml`'s `[heavy].embedding_model`, or set
`EMBEDDING_MODEL=...` for a single run, then re-run
`python scripts/full_pipeline.py --stages embed`. `sentence-transformers`
downloads a new model on first use (needs network), and Chroma's existing
collection isn't automatically re-embedded -- switch models only when
you're prepared to rebuild the index.

## Architecture

Two layers -- job 1 (deterministic) and job 2 (generative) in
[AGENTS.md](AGENTS.md)'s terms -- with an optional heavy-pipeline
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
|   -> content/parsed/<citekey>.txt     |   pdftotext/docling
+---------------------------------------+
          |
          v
+---------------------------------------+
| src/retrieval.py                      |
|   search(query, k) -> SearchResult    |   BM25-ranked keyword search
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
                                     | Docling -> embeddings/Chroma ->               |
                                     | BERTopic -> (same render_output.py above)     |
                                     | each stage reports: ok / skipped /            |
                                     | missing-binary                                |
                                     +-----------------------------------------------+
```

`papers/bibliography.bib` is the **source of truth** for citekeys and
metadata -- this pipeline parses it, it does not generate its own citekeys
or its own copy of the bibliography. It's a per-host, gitignored file (see
"Configuration" above), not shipped in the repo, since the PDFs it points
to aren't either. See [AGENTS.md](AGENTS.md) for why, and what changed if
you're looking at content written before 2026-07-28.

Every genre skill runs `python -m src.citation_gate` on its own output
before presenting a draft, and refuses to invent a citekey. See
[AGENTS.md](AGENTS.md) for why this is a hard gate rather than a style
suggestion. Once a draft passes, `python -m src.references` appends (or
idempotently replaces) a "## References" section built only from citekeys
the draft already cites, and `python -m src.heavy.render_output
<file> --format pdf` (or `tex`/`docx`) renders it via Pandoc/TeX Live,
resolving `[@citekey]` markers against `bibliography.bib` -- both stdlib
only, no venv required, same as `citation_gate`. `scripts/verbatim_check.py`
is a separate, ad-hoc review aid (not part of this automatic chain) for
spot-checking a draft against its sources for verbatim overlap or
page-locating a quoted phrase.

### What works on this host

| Capability | What it needs |
|---|---|
| Parse bib file, track citekeys + PDF paths | `bibtexparser` (venv, main Poetry group) |
| Extract PDF text | `pdftotext` (poppler-utils, `os-deps` stage) by default -- `docling` is an opt-in alternative, see ["Choosing a parser backend"](#choosing-a-parser-backend) |
| Track parse status incrementally | stdlib `sqlite3` |
| BM25-ranked retrieval | stdlib only |
| Citation verification gate, auto References section, standalone tex/pdf render | stdlib only, no venv needed (see "Venv requirement" above) |
| Docling layout-aware parsing, embeddings/Chroma, BERTopic | venv, `heavy` Poetry group (`src/heavy/`) |
| Compiling generated `.tex` chapters to PDF (Pandoc/TeX Live) | `pandoc`, `pdflatex`, `latexmk` (`os-deps` stage) |

Retrieval by default is a BM25 ranker over a disk-cached term-frequency
index (`src/retrieval.py`, stdlib only) -- deliberately: swapping in
`src/heavy/embed_index.py`'s embedding-based search (sentence-transformers
+ Chroma, same `search(query, k)` shape, ready to drop in without
changing callers) is a call to make when BM25 stops being enough for this
corpus, not a threshold this project asserts a number for ahead of time.
The cache is keyed by a cheap per-document fingerprint (parsed-file stat,
not content), so a `search()` call only re-tokenizes documents whose text
actually changed since the last call.

## The heavy pipeline

`scripts/full_pipeline.py` runs Docling -> sentence-transformers/Chroma
-> BERTopic -> Pandoc/LaTeX as one script for both the
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

## Developer material

Test running, the full source layout, and known gaps/unbuilt features
have moved to [DEVELOPER.md](DEVELOPER.md).

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

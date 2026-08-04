# Configuration

Every setting, what values it accepts, and what it defaults to.

What each setting *costs* lives in [PERFORMANCE.md](PERFORMANCE.md), so
this document can stay a reference rather than an argument.

## Table of contents

- [How configuration is loaded](#how-configuration-is-loaded)
- [A minimal config.toml](#a-minimal-configtoml)
- [Every setting](#every-setting)
  - [Paths](#paths)
  - [`[render]` -- citation style](#render----citation-style)
  - [`[parser]` -- PDF text extraction](#parser----pdf-text-extraction)
  - [`[provenance]` -- citation-support bands](#provenance----citation-support-bands)
  - [`[heavy]` -- the optional heavy pipeline](#heavy----the-optional-heavy-pipeline)
- [How values are parsed](#how-values-are-parsed)
- [Notes on individual settings](#notes-on-individual-settings)
- [Choosing an embedding model](#choosing-an-embedding-model)

## How configuration is loaded

`config.toml` is not in the repository -- you create it, once:

```bash
cp config.toml.example config.toml
```

`src/config.py` reads it at import time and **fails with that exact
command** if it is missing, rather than silently falling back to the
example: a machine quietly running settings its owner never chose is a
worse failure than one that refuses to start.

- The file is read **once, at import**, into plain module-level
  constants. They are fixed for the life of the process; editing
  `config.toml` mid-run changes nothing until the next run.
- Every setting can be overridden per-run by an **environment variable**
  of the same name, without editing the file:
  `BIB_FILE=/path/to/other.bib python -m src.sync`. The environment
  always wins.
- Set **`CONFIG_PATH`** to keep the file elsewhere:
  `CONFIG_PATH=/etc/research/config.toml python -m src.sync`.
- **Every key is optional.** Anything absent falls back to the default in
  the tables below, so your file only needs the settings you want to
  change.

`config.toml.example` is the tracked template, carrying the same settings
with longer commentary. This document is the authoritative list of
accepted values.

## A minimal config.toml

Because every key is optional, the smallest valid file is an **empty
file** -- that runs the whole pipeline on defaults, expecting your
bibliography at `papers/bibliography.bib`.

The smallest *useful* file names the one thing that genuinely varies
between machines:

```toml
# Minimal config.toml -- everything else falls back to its default.
[bib]
path = "papers/bibliography.bib"
```

A realistic small config, opting into the slower/higher-fidelity parser
and some parallelism:

```toml
[bib]
path = "papers/bibliography.bib"

[parser]
backend = "docling"   # "pdftotext" (default) or "docling"
workers = "auto"      # as many as this machine can sustain
```

## Every setting

Paths resolve **relative to the repository root**; an absolute path is
used as given.

### Paths

| Key | Env var | Accepts | Default |
|---|---|---|---|
| `[bib] path` | `BIB_FILE` | path | `papers/bibliography.bib` |
| `[content] dir` | `CONTENT_DIR` | path | `content` |
| `[source_pdfs] dir` | `SOURCE_PDFS_DIR` | path | `papers/pdfs` |

- **`[bib] path`** -- the BibTeX export `src/bib_reader.py` parses. The
  only source of citekeys; nothing in the pipeline invents or renames
  one. Gitignored per-host data.
- **`[content] dir`** -- everything `sync` and the heavy pipeline write:
  `ledger.sqlite`, `parsed/`, `provenance/`, `rendered/`, plus
  `docling/`, `chroma/` and `topics.json` from the heavy stages.
- **`[source_pdfs] dir`** -- raw PDFs with no bibliography entry, for the
  heavy pipeline's topic modelling and embeddings. **Never citable**: a
  PDF here has no citekey, so add it to your reference manager,
  re-export, and re-run `sync` before citing it.

### `[render]` -- citation style

Used only by `src/heavy/render_output.py`, never by `sync` or the
citation gate.

| Key | Env var | Accepts | Default |
|---|---|---|---|
| `csl` | `CSL_STYLE` | path | `assets/csl/ieee.csl` |
| `collapse_citations` | `RENDER_COLLAPSE_CITATIONS` | boolean | `true` |

- **`csl`** -- the CSL style pandoc's `--citeproc` formats citations and
  the bibliography with. The IEEE style ships with this repo (vendored,
  not fetched: rendering has to work with no network, and a style that
  changed underneath a draft would renumber one already reviewed). Point
  it at any other `.csl` file to use a different style;
  [the CSL project](https://github.com/citation-style-language/styles)
  publishes several thousand. Without this, pandoc falls back to Chicago
  author-date.
- **`collapse_citations`** -- whether a run of consecutive numbers
  collapses: `[3]-[6]` rather than `[3], [4], [5], [6]`. The IEEE
  Reference Guide's own examples use the collapsed form, but upstream
  `ieee.csl` does not produce it, so `render_output.py` injects the one
  CSL attribute that does (`collapse="citation-number"`) into a temp copy
  of the style. Set `false` to render whatever the style on disk says,
  unmodified. A style that already sets `collapse` itself is never
  overridden. See `assets/csl/README.md`.

### `[parser]` -- PDF text extraction

| Key | Env var | Accepts | Default |
|---|---|---|---|
| `backend` | `PARSER` | `"pdftotext"` \| `"docling"` | `"pdftotext"` |
| `ocr` | `PARSER_OCR` | boolean | `false` |
| `workers` | `PARSER_WORKERS` | positive integer, or `"auto"` | `1` |
| `start_method` | `PARSER_START_METHOD` | `"auto"` \| `"forkserver"` \| `"spawn"` | `"auto"` |
| `document_timeout` | `PARSER_DOCUMENT_TIMEOUT` | positive number of seconds, or `"off"` | `"off"` |
| `stall_timeout` | `PARSER_STALL_TIMEOUT` | positive number of seconds, or `"off"` | `1800` |
| `long_word_chars` | `PARSE_LONG_WORD_CHARS` | number, used as an integer | `20` |
| `long_word_ratio` | `PARSE_LONG_WORD_RATIO` | number, a fraction 0.0-1.0 | `0.01` |
| `min_tokens` | `PARSE_MIN_TOKENS` | number, used as an integer | `200` |

The values in full:

- **`backend`** -- `"pdftotext"` needs the `pdftotext` binary on `PATH`
  and no Python package; `"docling"` needs the `heavy` dependency group.
  Any other value is rejected, naming the valid ones. See
  [notes](#backend-pdftotext-or-docling).
- **`ocr`** -- only `docling` has an OCR stage; `pdftotext` ignores this.
- **`workers`** -- `1` takes a strictly serial path: no pool, no
  subprocesses, nothing about a run changes. An integer above 1, or
  `"auto"`, opts into a worker pool. The resolved count is **clamped**
  rather than obeyed blindly -- see
  [notes](#workers-and-how-it-is-clamped). `0`, negative numbers, and
  `true`/`false` are rejected at load.
- **`start_method`** -- consulted only when `workers > 1` **and**
  `backend = "docling"`; nothing else here uses a process pool.
  - `"auto"` -- `forkserver` where the platform has it (Linux, macOS),
    `spawn` where it does not (Windows).
  - `"forkserver"` -- one helper process imports torch and docling; every
    worker is forked from it.
  - `"spawn"` -- a fresh interpreter per worker, importing everything
    itself.
  - `"fork"` is **not** accepted -- see [notes](#why-fork-is-not-an-option).
- **`document_timeout`** / **`stall_timeout`** -- a positive number of
  seconds, or one of `"off"`, `"none"`, `"false"`, or an empty string,
  all meaning "no limit". `0` and negative numbers are **rejected**
  rather than read as "off", because "zero seconds" is the opposite of
  what someone writing it means. Integers and floats both work
  (`stall_timeout = 90.5` is valid).
- **`long_word_chars`** / **`min_tokens`** -- any number; the fractional
  part is truncated.
- **`long_word_ratio`** -- a fraction between 0.0 and 1.0. The range is
  not enforced, so a value above 1.0 loads fine and simply disables the
  warning, since no document can exceed it.

### `[provenance]` -- citation-support bands

| Key | Env var | Accepts | Default |
|---|---|---|---|
| `weak_score` | `PROVENANCE_WEAK_SCORE` | number, a fraction 0.0-1.0 | `0.20` |
| `good_score` | `PROVENANCE_GOOD_SCORE` | number, a fraction 0.0-1.0 | `0.50` |

`src/citation_provenance.py` bands the fraction of a citing sentence's
distinctive words found in the best-matching source passage. Below
`weak_score` a finding reads "no support found", which means *go look at
this one first* -- never "this citation is wrong". At or above
`good_score` it is banded "supported".

Round numbers on purpose: the report sets a reading order for a human,
not a pass/fail line, so tuning them precisely would be false precision.
Neither is range-checked, and nothing enforces
`weak_score < good_score`.

### `[heavy]` -- the optional heavy pipeline

Used only by `src/heavy/*` (the `heavy` dependency group), never by
`sync` or the citation gate.

| Key | Env var | Accepts | Default in code | In `config.toml.example` |
|---|---|---|---|---|
| `embedding_model` | `EMBEDDING_MODEL` | a sentence-transformers model id | `sentence-transformers/all-MiniLM-L6-v2` | `sentence-transformers/all-mpnet-base-v2` |
| `docling_images` | `DOCLING_IMAGES` | boolean | `false` | `false` |
| `docling_image_scale` | `DOCLING_IMAGE_SCALE` | number | `2.0` | `2.0` |

**The two `embedding_model` columns differ on purpose, and the
distinction matters.** The code's fallback is the smaller, faster
MiniLM -- what you get if the key is absent. The shipped example sets the
larger, more accurate mpnet, so anyone who copied `config.toml.example`
is running mpnet. Check your own file rather than assuming either. See
[Choosing an embedding model](#choosing-an-embedding-model).

## How values are parsed

Worth knowing, because two of these will surprise you.

**Booleans from the environment.** In TOML, write `true` / `false`. From
an environment variable, only `1`, `true`, `yes`, `on`
(case-insensitive) mean true -- **anything else is false**, including
typos. That is deliberate: `bool("false")` is `True` in Python, so
without it every documented way of turning a setting off via the
environment would silently turn it on.

**A wrong TOML type falls back silently.** String and boolean settings
return their default when the value has the wrong type, rather than
raising. So `ocr = "true"` -- a string, not a boolean -- is read as the
default `false`, with no complaint. Quote strings; don't quote booleans
or numbers.

**Four settings are validated at load and fail loudly:** `workers`,
`start_method`, `document_timeout`, `stall_timeout`. A bad value raises
immediately, naming the key, the environment variable, and what was
expected -- rather than surfacing much later as a nonsense pool size or a
strange timeout.

## Notes on individual settings

### `backend`: pdftotext or docling

`src/pdf_text.py` dispatches through a table, so adding a backend is one
function plus one entry -- and two candidates were added and later
removed through that same seam.

| Backend | Dependency | Page boundaries? | Speed |
|---|---|---|---|
| `pdftotext` (default) | `poppler-utils` on `PATH` | **Yes** -- form feeds between pages | Fastest |
| `docling` | `docling`, `heavy` group | No -- one continuous document | ~42x slower; see [PERFORMANCE.md](PERFORMANCE.md#parserbackend----pdftotext-or-docling) |

**Losing page boundaries is not cosmetic.**
`scripts/verbatim_check.py` reports which PDF page a verbatim run came
from by splitting on those form feeds, so a citekey parsed with `docling`
reports `pdf p.1` for every hit regardless of where the text sits.

[PDF-PARSER.md](PDF-PARSER.md) has the full fidelity comparison.

**Setting `backend = "docling"` does not fold docling into the heavy
pipeline, and does not make `src/heavy/docling_parse.py` redundant.**
They are two independent consumers of the same library:

- **`src/pdf_text.py`** (job 1, on `sync`) extracts plain text per
  citekey into `content/parsed/<citekey>.txt` for BM25 retrieval. docling
  here is a higher-fidelity substitute for `pdftotext`'s job.
- **`src/heavy/docling_parse.py`** (job 2, opt-in) produces structured
  Markdown for the whole corpus into `content/docling/`, feeding the
  embedding and topic stages that need real reading order and section
  boundaries. It **always** uses docling regardless of this setting.

### `workers`, and how it is clamped

The resolved count is the smallest of three ceilings, never below 1:

1. what you asked for,
2. what the machine can sustain,
3. how many documents actually need parsing.

The third matters more than it looks: standing up 12 docling workers to
parse 3 documents pays 12 model loads to save two documents' work.

"What the machine can sustain" counts the CPUs **this process may run
on** -- not the machine's total, which on a shared or containerised
machine can be far larger. For `docling` that count is divided by 4:

| CPUs available to the process | 4 | 8 | 16 | 48 |
|---|---|---|---|---|
| `workers = "auto"` resolves to | 1 | 2 | 4 | 12 |

**That divisor of 4 is measurably too conservative.** It models a docling
worker as occupying about 4 CPUs, which a full-corpus sweep does not
support: at 32 workers the CPU is only ~70% busy, and 32 workers run
**1.41x faster** than the 12 this table allows -- and 48 workers are no
worse, so the honest reading is "much smaller than 4", not a specific
replacement. Changing it is a behaviour change and has not been
made -- see
[PERFORMANCE.md](PERFORMANCE.md#parserworkers----document-level-parallelism).

So a four-core desktop resolves to 2, and asking for 15 there still gets
2 -- **clamped and said out loud on stderr**, rather than silently obeyed
(which thrashes) or silently ignored. docling's own internal thread count
is divided down to match, so workers x threads still fits.

**Two things the clamp cannot see:** a cgroup CPU *quota*
(`docker --cpus=2`) throttles without changing which CPUs are permitted,
and RAM is not considered at all. Set an explicit number on either.

Each backend gets the concurrency it can use: processes for `docling`
(in-process, holds the GIL), threads for `pdftotext` (an external
subprocess that releases it). Ledger writes always stay on the main
process -- sqlite has a single writer -- and results are reported in
bibliography order regardless of which worker finished first, so two
identical runs still print identically.

### Using more than one GPU

Nothing to configure. With `docling` and more than one worker, each
worker claims one CUDA device round-robin -- docling's own
`AcceleratorDevice.AUTO` resolves to `cuda:0` in *every* process, so
without this every worker would pile onto card 0 while the rest idle.

Restrict which cards are used with `CUDA_VISIBLE_DEVICES`; the pool only
ever sees what that leaves visible. Figures in
[PERFORMANCE.md](PERFORMANCE.md#multi-gpu----nothing-to-configure).

### Why `fork` is not an option

Not an oversight. By the time the pool is built, the process holds two
live sqlite connections -- the run lock and the ledger -- and SQLite's
own documentation says not to carry an open connection across `fork()`.
`forkserver` starts its server as a fresh interpreter, so workers inherit
the preloaded modules and nothing else. It also measured **no slower**
than plain `fork`, so nothing is given up.

Both available start methods re-import the calling program's `__main__`
in each worker, so a script of your own driving `sync` or `parse_corpus`
must guard its top level with `if __name__ == "__main__":`.

### The two timeouts are not the same guard

- **`document_timeout`** bounds one document, and the two backends
  enforce it with unequal strength. For `pdftotext` it is a subprocess
  timeout -- a real kill, the one case where a wedged parse can actually
  be stopped. For `docling` it is that library's own check *between*
  pipeline stages: it bounds a pathologically slow document but will not
  interrupt a hang inside a single stage.
- **`stall_timeout`** bounds a *parallel run*, by asking whether **any**
  document at all has completed recently. With several workers,
  completions arrive constantly, so total silence across the whole pool
  separates a hung worker from a merely slow document far better than any
  per-document number could. It applies only when `workers > 1`; a serial
  run has no pool to go silent.

`stall_timeout` is on by default, unlike most safety valves here, because
the failure it catches is one a user actually hit: a run that never
finishes. A false positive is cheap -- outstanding documents are marked
failed and retried next run, not lost -- and a warning is printed at half
the budget before anything is given up on.

Either way a timed-out document is **reported as a failure, not silently
truncated**, and is retried on the next run. Choosing a safe value means
knowing your slowest legitimate document; see
[PERFORMANCE.md](PERFORMANCE.md#parserdocument_timeout----what-a-safe-value-looks-like).

### The parse-quality guard

`sync` warns when an implausible share of a freshly extracted document's
words are unusually long -- the signature of a backend that has lost the
spaces between words. That is easy to miss by eye and expensive
downstream: `src/retrieval.py` tokenises on whitespace, so a query term
fused into a longer run stops matching entirely.

`long_word_chars`, `long_word_ratio` and `min_tokens` are its thresholds.
It is **a warning, never a failure** -- the text is still usable, and an
unusual corpus could trip it legitimately. It will not catch a bad `ocr`
choice: it looks for run-together words, not for content that never
arrived.

### `docling_images`

Extracts figure bitmaps into `content/docling/<doc>_artifacts/`, plus a
`<doc>.figures.json` giving each figure's page, caption, and the exact
string to cite it by.

Those images are a **reading aid** for checking a draft against its
sources. They are never inserted into a draft: a figure's copyright is
not the paper's citekey to grant. See
[DEVELOPER.md](../DEVELOPER.md#figures-and-copyright).

Changing this invalidates the whole docling cache, so the next run
re-parses the corpus from scratch. Costs in
[PERFORMANCE.md](PERFORMANCE.md#heavydocling_images----disk-and-a-full-re-parse).

## Choosing an embedding model

`src/heavy/embed_index.py` calls
`SentenceTransformer(config.EMBEDDING_MODEL).encode(...)`
**symmetrically** -- the same call embeds a 200-word document chunk
(40-word overlap) and a search query, with no prefix or instruction text
added on either side.

That one fact decides which models are drop-in and which are not.

### Drop-in

| Model | Dimensions | Relative cost | Best for | Tradeoff |
|---|---|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` (code default) | 384 | Lowest -- ~22M params, fast even on CPU | Small corpora, quick iteration, CPU-only machines | Least semantic nuance of the three; general-purpose training data, nothing science-specific |
| `sentence-transformers/all-mpnet-base-v2` (example default) | 768 | ~4-5x MiniLM -- comfortable on a GPU, noticeably slower CPU-only | Meaningfully better general-purpose semantic quality | More RAM/VRAM and slower indexing/search, for a gain that may not matter at a small corpus size |
| `sentence-transformers/multi-qa-mpnet-base-dot-v1` | 768 | Same class as `all-mpnet-base-v2` | Trained specifically on short-query-vs-long-passage retrieval -- the closest match to what `search()` actually does | Slightly weaker on generic sentence similarity outside retrieval |

What each one is:

- **`all-MiniLM-L6-v2`** -- a 6-layer transformer distilled from a larger
  model, then fine-tuned on roughly a billion general sentence pairs with
  contrastive learning, so semantically similar sentences land close
  together. That symmetric objective is exactly what the prefix-free
  `encode()` call needs, which is why it is the code default. ~22M
  parameters, fast on CPU alone.
- **`all-mpnet-base-v2`** -- the same recipe on a larger 12-layer MPNet
  backbone (~109M parameters). Generally the strongest all-around
  sentence-transformers model for semantic similarity with no domain
  specialisation. The extra quality costs roughly 4-5x the compute and
  doubles the vector dimensionality, which doubles per-chunk storage in
  Chroma and slows similarity search somewhat.
- **`multi-qa-mpnet-base-dot-v1`** -- the same MPNet backbone, fine-tuned
  on ~215M question/answer and query/passage pairs for dot-product
  retrieval rather than generic similarity. Conceptually the closest
  match to a short query against a longer chunk, and it needs no prefix,
  so it stays a clean drop-in. Same cost profile as `all-mpnet-base-v2`.

### Not without a code change first

- **`allenai/specter` / `specter2`** -- a SciBERT-based model trained on
  scientific title+abstract pairs using citation graphs as the signal. It
  is built for whole-paper similarity, not passage retrieval, and expects
  a specific input shape (title `[SEP]` abstract) that does not match the
  arbitrary 200-word body chunks this pipeline produces. Using it well
  means feeding it titles and abstracts -- a real code change -- and even
  then it answers "which papers are alike", a different question than
  "which chunk answers this query".
- **BAAI `bge-*` and `intfloat e5-*` families** -- strong on public
  retrieval benchmarks, but they expect literal `"query: "` /
  `"passage: "` prefixes baked into the input so the model knows which
  role each side plays. Nothing here adds one. Feeding either in as-is
  **will not error -- it will silently underperform**, which is the worst
  failure mode of the set. Adopting one means pairing it with matching
  prefix-handling code, not a config-only swap.

### Switching

Edit `[heavy].embedding_model`, or set `EMBEDDING_MODEL=...` for a single
run, then rebuild the index:

```bash
python scripts/full_pipeline.py --stages embed
```

The model downloads on first use (needs network), and Chroma's existing
collection is **not** re-embedded automatically -- switch only when you
are prepared to rebuild the index.

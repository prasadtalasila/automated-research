# Configuration

`config.toml` is not in the repository -- you create it, once:

```bash
cp config.toml.example config.toml
```

`src/config.py` reads it at import time and **fails with that exact
command** if it is missing, rather than silently falling back to the
example: a host quietly running settings its owner never chose is a
worse failure than one that refuses to start. Point `CONFIG_PATH` at
it to keep the file elsewhere.

Every setting can also be overridden per-run by an environment
variable of the same name, without editing the file:
`BIB_FILE=/path/to/other.bib python -m src.sync`.

## The shipped example, in full

This is `config.toml.example` verbatim. The comments are the
reference -- each one says what the setting costs, not just what it
does.

```toml
# Central configuration for the research pipeline.
# Any value here can be overridden with an environment variable of the
# same name as the corresponding setting below (e.g.
# BIB_FILE=/path/to/other.bib python -m src.sync), for container/CI
# flexibility without editing this file.

[bib]
# The BibTeX-exported .bib file -- source of truth for citekeys and
# bibliographic metadata (decision: 2026-07-28). This is a manual,
# point-in-time export from your reference manager, not continuously
# auto-synced -- re-export after adding papers, then re-run
# `python -m src.sync`. Lives under papers/ and is gitignored (personal,
# per-host data, not shippable source -- decision: 2026-07-31); see
# AGENTS.md.
# Relative to the repo root.
path = "papers/bibliography.bib"

[content]
# Where sync/heavy-pipeline outputs are written. Relative to the repo root.
dir = "content"

[source_pdfs]
# Raw PDFs gathered outside the bib file (no citekey -- see src/heavy/corpus.py).
# Relative to the repo root. Changed 2026-07-31 to live under papers/,
# alongside the bib file; this repo's older source-pdfs/ location was
# migrated here the same day and is now retired.
dir = "papers/pdfs"

[parser]
# Which backend `python -m src.sync` uses to turn a PDF into
# content/parsed/<citekey>.txt: "pdftotext" (default -- fast, needs
# poppler-utils on PATH, no Python dependency) or "docling" (best
# structural fidelity, needs the "heavy" Poetry group -- see
# docs/PDF-PARSER.md for the full tradeoff comparison). Two consequences
# worth knowing before switching off the default:
#   - docling is far slower than pdftotext -- ~42x on the same 5 real
#     bib PDFs measured in total (60.8s vs 1.4s), individual PDFs
#     ranging roughly 18x-102x -- a first-time sync over a large,
#     unparsed corpus will take hours, not minutes, and that loop still
#     runs serially (see src/sync.py's own comment on why).
#   - only pdftotext's output has page boundaries (form-feed characters
#     between pages). docling produces one continuous
#     document with no page markers, so scripts/verbatim_check.py's
#     "pdf p.N" locating degrades to reporting everything as page 1 for
#     citekeys parsed with that backend.
backend = "pdftotext"

# Whether the docling backend runs its OCR stage. Docling's own default
# is on; this project's default is off. That is a speed/completeness
# trade-off made deliberately, not a free win, so it is worth
# understanding before trusting either setting.
#
# What it buys. OCR runs on the CPU (RapidOCR on onnxruntime) and is a
# large part of why the docling path is CPU-bound rather than GPU-bound.
# Measured over 16 real bib PDFs (943 pages) on the A40 host: turning it
# off makes the parse 2.46x faster, taking a full-corpus parse from
# ~1.6 hours to ~0.65 hours. The full write-up is in bench/RESULTS.md in
# the repository -- developer-only, so it is not in the release zip;
# README's "OCR: off by default" section carries the same figures and is.
#
# What it costs, and this is the part to read twice. OCR only runs on
# *bitmap* regions -- so what it recovers is text that exists in the PDF
# as an image rather than as characters. Turning it off changed the
# extracted text of 8 of those 16 documents. Most of what disappears is
# publisher furniture ("IEEEAccess", "DTU Library") and figure
# sub-captions, but not all of it: one document lost 10.1% of its
# characters, including two complete tables (a comparison matrix and an
# abbreviations list) that were embedded as images, and another lost a
# paragraph of real body prose set in a graphical text box.
#
# So: `false` is right for a corpus of born-digital papers where you care
# more about a fast re-parse than about text baked into figures. Set it
# to `true` if your PDFs are scans (with OCR off, docling will extract
# almost nothing from a scan), or if tables and figure text matter to you
# more than parse time. The parse-quality guard below will NOT catch a
# bad choice here -- it looks for run-together words, not for content
# that never arrived.
ocr = false

# How many documents `python -m src.sync` parses at once.
#
#   1        the default, and the historical behaviour: one document at a
#            time, no pool, no subprocesses. Nothing about a run changes
#            unless you raise this.
#   <int>    that many workers, clamped -- see below.
#   "auto"   as many as this host can actually sustain.
#
# Only the number of *documents* in flight; each worker is itself
# multi-threaded when the backend is docling, which is why the ceiling
# below is not simply "one worker per CPU".
#
# The resolved count is min(what you asked for, what the host allows, how
# many documents actually need parsing), never below 1. "What the host
# allows" is the CPUs this *process* may run on -- not the machine's
# total, which on a shared or containerised host can be far larger (on
# the machine this was developed on: 96 CPUs exist, 48 are permitted) --
# divided by 4 for the docling backend, because one docling worker uses
# about 4 CPUs of its own. So:
#
#   allowed CPUs   4    8    16   48
#   "auto"         1    2    4    12
#
# A four-core, eight-thread desktop therefore resolves to 2, and asking
# for 15 there still gets you 2. An over-large request is clamped and
# said out loud rather than silently obeyed (which thrashes) or silently
# ignored. Docling's own internal thread count is divided down to match,
# so workers x threads still fits the host.
#
# Two things this cannot see: a cgroup CPU *quota* (`docker --cpus=2`)
# throttles without changing which CPUs are permitted, and RAM is not
# considered at all. On either, set an explicit number.
#
# With the docling backend, each worker also claims one CUDA device
# round-robin, so a multi-GPU host uses all of its cards rather than
# stacking every worker on cuda:0 (which is what docling's own AUTO
# device resolution does in every process). Restrict that with
# CUDA_VISIBLE_DEVICES; there is no separate setting for it. Measured
# over this project's 501-PDF corpus at 12 workers: 528s on one A40,
# 326s across four.
workers = 1

# Give up on a single document after this many seconds. "off" (the
# default) means no limit.
#
# Applies to both backends, by the mechanism each one has, and those
# mechanisms are not equally strong:
#   - pdftotext: a subprocess timeout, i.e. a real kill. This is the one
#     backend where a wedged parse can actually be stopped.
#   - docling: its own PdfPipelineOptions.document_timeout, checked
#     *between* pipeline stages. It bounds a pathologically slow
#     document; it will not interrupt a hang inside a single stage.
#
# Any value has to clear the slowest document you legitimately have. In
# this project's corpus that is a 675-page book which takes 246s on its
# own, so a threshold that is safe here may not be safe on another
# corpus -- measure before setting it. A document that times out is
# reported as a failure, not silently truncated, and is retried on the
# next run.
document_timeout = "off"

# Give up on a *parallel* run when no document at all has completed for
# this many seconds. "off" means wait forever.
#
# Not a per-document deadline, and the difference is the point: with
# several workers, completions arrive constantly, so total silence
# across the whole pool separates a hung worker from a merely slow
# document far better than any per-document number could -- which
# matters precisely because the slowest legitimate document here takes
# 246s.
#
# On by default, unlike most safety valves here, because the failure it
# catches is one a user actually hit: a run that never finishes. The
# default is loose on purpose (7x that slowest document), and a false
# positive is cheap -- the outstanding documents are marked failed and
# retried next run, not lost. Only applies when [parser].workers > 1;
# a serial run has no pool to go silent.
stall_timeout = 1800

# Parse-quality guard. `sync` warns when an extracted document has more
# than `long_word_ratio` of its words longer than `long_word_chars`,
# which is what a backend losing the spaces between words looks like --
# retrieval tokenizes on whitespace, so fused words stop matching
# queries entirely. Documents shorter than `min_tokens` words are
# skipped as too noisy to judge. A warning, never a failure: the text is
# still usable, and an unusual corpus could trip it legitimately.
long_word_chars = 20
long_word_ratio = 0.01
min_tokens = 200

[provenance]
# src/citation_provenance.py bands the fraction of a citing sentence's
# distinctive words found in the best-matching source passage. Below
# weak_score a finding reads "no support found", which means "go look at
# this one first" -- never "this citation is wrong". The report is a
# reading order for a human, not a pass/fail line, which is why these are
# round numbers rather than tuned ones.
weak_score = 0.20
good_score = 0.50

[heavy]
# Only used by src/heavy/* (pyproject.toml's "heavy" Poetry group), not
# by the core sync/citation_gate pipeline.
embedding_model = "sentence-transformers/all-mpnet-base-v2"
# Whether the docling stage also extracts figure bitmaps, into
# content/docling/<doc>_artifacts/, plus a <doc>.figures.json giving each
# figure's page, caption, and the exact string to cite it by. The images
# are a *reading aid* for checking a draft against its sources -- they
# are never inserted into content/drafts/, since a figure's copyright is
# not the paper's citekey to grant (see DEVELOPER.md's "Figures and
# copyright"). Off by default; two costs to know before turning it on:
#   - it invalidates the whole Docling cache when changed, so the next
#     run re-parses every PDF from scratch (~42x pdftotext, per above).
#     That re-parse is the point, not a bug -- the existing .md files
#     genuinely don't have the figure references in them yet.
#   - the PNGs are real disk: a 17-page paper produced 13 of them
docling_images = false
# Render scale for those bitmaps -- 2.0 is roughly 144 DPI, enough to
# read a figure back without storing print-resolution files.
docling_image_scale = 2.0
```

`config.toml` is the single place every path/URL/timeout/model setting lives.
`src/config.py` loads it once at import time via stdlib `tomllib` into
plain module-level constants (not functions, so they're fixed for the life
of the process); every setting can also be overridden per-run with an
environment variable of the same name, without editing the file, e.g.
`BIB_FILE=/path/to/other.bib python -m src.sync`.

| Section | Key | Env var | Default | What it controls |
|---|---|---|---|---|
| `[bib]` | `path` | `BIB_FILE` | `papers/bibliography.bib` | The BibTeX export `src/bib_reader.py` parses -- the only source of citekeys (AGENTS.md's hard invariant). Gitignored, per-host -- see DEVELOPER.md's "Repository layout" |
| `[content]` | `dir` | `CONTENT_DIR` | `content` | Where `sync`/heavy-pipeline outputs live: `ledger.sqlite`, `parsed/`, `docling/`, `chroma/`, `topics.json`, `rendered/` |
| `[source_pdfs]` | `dir` | `SOURCE_PDFS_DIR` | `papers/pdfs` | Raw PDFs gathered outside the bib file, no citekey -- see `src/heavy/corpus.py` |
| `[parser]` | `backend` | `PARSER` | `pdftotext` | Which backend `sync` uses to extract PDF text -- `pdftotext` or `docling` -- see below |
| `[parser]` | `ocr` | `PARSER_OCR` | `false` | Whether the `docling` backend runs its OCR stage -- 2.46x slower on, but it is what reads text stored as bitmaps -- see below |
| `[parser]` | `workers` | `PARSER_WORKERS` | `1` | How many documents `sync` parses at once; a positive integer or `"auto"`, clamped to what the host can sustain -- see below |
| `[parser]` | `document_timeout` | `PARSER_DOCUMENT_TIMEOUT` | `"off"` | Give up on one document after N seconds -- a real kill for `pdftotext`, cooperative for `docling` -- see below |
| `[parser]` | `stall_timeout` | `PARSER_STALL_TIMEOUT` | `1800` | Give up on a parallel run when *no* document completes for N seconds -- see below |
| `[parser]` | `long_word_chars` | `PARSE_LONG_WORD_CHARS` | `20` | Word length above which a token counts as "run-together" for the parse-quality guard |
| `[parser]` | `long_word_ratio` | `PARSE_LONG_WORD_RATIO` | `0.01` | Share of such words above which `sync` warns that the parser is losing word boundaries |
| `[parser]` | `min_tokens` | `PARSE_MIN_TOKENS` | `200` | Documents shorter than this are too noisy to judge, and are skipped by the guard |
| `[heavy]` | `embedding_model` | `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Which sentence-transformers model `src/heavy/embed_index.py` loads for semantic search -- see below |
| `[provenance]` | `weak_score` | `PROVENANCE_WEAK_SCORE` | `0.20` | Below this share of matched words, `citation_provenance` reports "no support found" -- i.e. check this one first |
| `[provenance]` | `good_score` | `PROVENANCE_GOOD_SCORE` | `0.50` | At or above this, a citation is banded "supported" |
| `[heavy]` | `docling_images` | `DOCLING_IMAGES` | `false` | Whether the Docling stage also extracts figure bitmaps + a `<doc>.figures.json` index -- see [DEVELOPER.md](../DEVELOPER.md#figures-and-copyright). Changing it re-parses the whole corpus |
| `[heavy]` | `docling_image_scale` | `DOCLING_IMAGE_SCALE` | `2.0` | Render scale for those bitmaps (~144 DPI) |

## Choosing a parser backend

`src/pdf_text.py` (used by `sync`, i.e. job 1) dispatches to one of two
backends based on `config.PARSER`:

- `pdftotext` (default)
- `docling`

The dispatch is a table (`_EXTRACTORS`), so adding a backend is one
`_extract_*` function plus one entry -- a third, `markitdown`, was added
and later removed through that same seam (see
[docs/PDF-PARSER.md](PDF-PARSER.md#why-markitdown-was-removed)).

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
| `docling` | Slowest (~42x `pdftotext` in total, measured on the same 5 real bib PDFs -- but with OCR on, i.e. before 0.12.0 made `[parser].ocr = false` the default, which is 2.46x faster; see Performance below) | `docling`, "heavy" group | No -- one continuous document |

Losing page boundaries isn't cosmetic: `scripts/verbatim_check.py`'s
`cmd_overlap`/`cmd_locate` report which PDF page a verbatim run came
from by splitting on those form-feed characters, so switching a citekey
to `docling` makes every hit for it report `pdf p.1`
regardless of where the text actually sits. See docs/PDF-PARSER.md for the
full fidelity/speed comparison, including two candidates that were
evaluated and not adopted.

#### OCR: off by default, and why that is a trade-off

Docling ships with OCR on. This project turns it off
(`config.toml`'s `[parser].ocr`, or the `PARSER_OCR` env var), because
OCR runs on the CPU (RapidOCR on onnxruntime) and is a large part of why
the docling path is CPU-bound. Measured over 16 real bib PDFs:

| | s/page | Full corpus |
|---|---|---|
| `ocr = true` (Docling's default) | 0.431 | ~1h 36m |
| `ocr = false` (this project's) | 0.176 | **~39m** |

**2.46x** -- more than the GPU itself is worth on this workload.

It is not free, though, and the cost is easy to miss. OCR only runs on
*bitmap* regions, so what it recovers is text stored in the PDF as an
image rather than as characters. Turning it off changed the extracted
text of 8 of those 16 documents. Mostly that text is publisher furniture
(`IEEEAccess`, `DTU Library`) and figure sub-captions -- but one document
lost 10.1% of its characters, including two complete tables embedded as
images, and another lost a paragraph of body prose set in a graphical
text box.

Set `ocr = true` if your PDFs are scans (with OCR off, docling extracts
almost nothing from a scan), or if tables-as-images matter to you more
than parse time. The parse-quality guard below will **not** catch a wrong
choice here: it looks for run-together words, not for content that never
arrived. Full breakdown in `bench/RESULTS.md` in the repository (developer-only -- it is not part of the release zip).

#### Parsing several documents at once

`sync` parses one document at a time by default. `[parser].workers` (or
`PARSER_WORKERS`) opts into more:

```toml
workers = 1        # default -- strictly serial, no pool, no subprocesses
workers = 8        # eight documents in flight
workers = "auto"   # as many as this host can sustain
```

The default is deliberate rather than timid. A routine `sync` re-parses
zero-to-few documents (the ledger skips anything whose PDF hasn't
changed), so pool setup would usually cost more than it saves. It is a
first-time or bulk sync -- 501 PDFs here -- that this exists for.

**The resolved count is clamped**, to the smallest of: what you asked
for, what the host can sustain, and how many documents actually need
parsing. "What the host can sustain" is the CPUs *this process* may run
on -- not the machine's total, which on a shared or containerised host
can be much larger -- divided by 4 for the `docling` backend, because one
docling worker uses about 4 CPUs of its own:

| CPUs available to the process | 4 | 8 | 16 | 48 |
|---|---|---|---|---|
| `workers = "auto"` resolves to | 1 | 2 | 4 | 12 |

So a four-core/eight-thread desktop resolves to 2, and asking for 15
there still gets you 2 -- clamped, and said out loud on stderr rather
than silently obeyed. Docling's own internal thread count is divided down
to match, so workers × threads still fits the host.

Two things the clamp can't see: a cgroup CPU *quota* (`docker --cpus=2`)
throttles without changing which CPUs are permitted, and RAM isn't
considered at all. Set an explicit number on either.

Threads are used for `pdftotext` (an external subprocess that releases
the GIL) and processes for `docling` (in-process, holds the GIL), so each
backend gets the kind of concurrency it can actually use. Ledger writes
always stay on the main process -- sqlite has a single writer -- and
output is reported in bibliography order regardless of which worker
finished first, so two identical runs still print identically.

#### Using more than one GPU

Nothing to configure. When the backend is `docling` and more than one
worker is running, each worker process claims one CUDA device
round-robin, so a four-GPU host uses all four.

This is not automatic in Docling: its `AcceleratorDevice.AUTO` resolves
to `cuda:0` in *every* process, so without an explicit per-worker device
every worker piles onto card 0 while the rest idle. To restrict which
cards are used, set `CUDA_VISIBLE_DEVICES` as usual -- the pool only ever
sees what that leaves visible.

Measured over the whole 501-PDF corpus at 12 workers:

| | wall clock |
|---|---|
| One A40 (Docling's own `AUTO`) | 528.0s |
| Four A40s | **326.2s** (1.62x) |

Two caveats worth knowing:

- **It only pays at corpus scale.** On a 60-document subset the same
  change made no difference at all (122.4s at 4 workers, 123.0s at 12),
  because per-worker startup -- process spawn, importing torch and
  docling, loading the models -- dominates before GPU contention does.
- **Parsed output is not bit-reproducible at high worker counts.**
  Comparing a one-GPU and a four-GPU run over all 501 documents, 6 files
  differed by under 0.06% each: Docling grouping dense reference blocks
  into elements slightly differently under load. The same words, and
  retrieval tokenises on whitespace, so ranking is unaffected -- but
  don't expect `diff` to come back empty.

#### Interrupting a run

Ctrl+C stops a parallel run in about a second, reports how far it got,
and terminates its workers rather than waiting for them:

```
  [204/501] bakirtzis_ontological_2022
  interrupted -- 204/501 document(s) parsed. Work already finished is kept; re-run to continue.
```

Nothing is lost. The ledger commits as each document lands, so a re-run
picks up where the interrupt left off and re-parses only what didn't
finish.

Progress is printed to stderr as each document completes, so a long run
over a large corpus is visibly making progress -- which matters because
docling's own OCR chatter can otherwise fill the terminal for half an
hour with no sign of whether anything is happening.

#### Seeing what the content layer holds

```bash
python3 -m src.ledger
```

```
Ledger: content/ledger.sqlite   (646 item(s) from bibliography.bib)

   500  parsed
   145  no PDF attachment
     1  found, not yet parsed

  Nothing needs attention.
```

Detail is behind flags rather than in the way:

```bash
python3 -m src.ledger --status parse_failed   # just what needs attention
python3 -m src.ledger --citekey smith_2024    # one item, in full
python3 -m src.ledger --list                  # everything
```

It is a separate command rather than a `sync` flag on purpose. `sync`
takes the write lock, so an inspect flag on it would refuse exactly when
you most want to look -- during a run. This takes no lock, and needs only
the system `python3`.

#### Only one run at a time

`python -m src.sync` and `scripts/full_pipeline.py` both take a lock
before writing anything under `content/`. A second run exits immediately
with **exit code 2** (distinct from `1`, "ran and something failed", so a
cron job can tell a skipped cycle from a real problem):

```
  another sync or pipeline run is already running (it holds
  content/pipeline.lock.db), so this run was skipped. Nothing is lost --
  the pipeline is incremental, and the next run continues from where this
  one would have started.
```

Nothing is configurable here, and nothing needs cleaning up. **The lock is
released by the operating system when its holder exits, including on a
crash or `kill -9`** -- so there is no stale lock to clear by hand, and no
`--force` flag to reach for. If you see this message, a run really is
still alive.

One lock covers both entry points rather than one each, because the
unsafe overlap is any-writer-against-any-writer: `sync` writes
`content/parsed/*.txt` non-atomically, and the heavy pipeline reads those
same files.

**Readers are deliberately unaffected.** `citation_gate`, retrieval and
the drafting skills keep working while a run holds the lock -- it is a
separate file from the ledger precisely so that stays true.

#### Timeouts

Two independent limits, both off the critical path of a healthy run.

**`[parser].document_timeout`** bounds a single document. It is `"off"` by
default, because any value has to clear the slowest document you
legitimately have -- in this corpus a 675-page book that takes 246s on
its own. The two backends honour it by different mechanisms, and they are
not equally strong: for `pdftotext` it is a subprocess timeout, i.e. a
real kill; for `docling` it is that library's own check *between*
pipeline stages, which bounds a pathologically slow document but will not
interrupt a hang inside one stage. A document that times out is reported
as a failure, never silently truncated.

**`[parser].stall_timeout`** bounds a *parallel* run, and defaults to 30
minutes. It fires when no document at all has completed for that long --
deliberately not a per-document deadline, because with several workers
completions arrive constantly, so total silence is what distinguishes a
hung worker from a slow document. It is on by default because the failure
it catches is one a user actually hit; a false positive costs one re-run,
since the outstanding documents are marked failed and retried rather than
lost.

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
[docs/PDF-PARSER.md](PDF-PARSER.md#why-markitdown-was-removed).

#### Performance: the whole corpus

The five-PDF table above is enough to choose a backend. It is not enough
to answer "how long does a first-time `docling` sync of the whole bib
file take, and what is actually the bottleneck". `bench/` answers that,
reproducibly; the measurement is written up in `bench/RESULTS.md` in the repository (developer-only -- it is not part of the release zip).
The figures below are the summary, so this section stands on its own
without it.

Measured 2026-08-02 on the documented A40 host, over all 501 PDFs
(13,400 pages) that `papers/bibliography.bib` resolves:

| | |
|---|---|
| One process, one A40, OCR on | ~1.6 hours (0.43 s/page) |
| One process, one A40, OCR off (the default since 0.12.0) | **~39 min** (0.18 s/page) |
| One process, CPU only, OCR on | ~5.1 hours (1.37 s/page) |
| GPU vs CPU, same 6 PDFs | **1.79x** |

The surprise is the last row. During that run the A40 averaged **~7% SM
utilization** and 1.7 GB of its 46 GB, while the process held ~300% CPU
-- three of the 48 available cores. Docling is CPU-bound here (PDF
backend, layout post-processing, and OCR, which runs on the CPU via
onnxruntime), so the GPU buys far less than its presence suggests, and
three of this host's four GPUs are never addressed at all: docling's
`AcceleratorDevice.AUTO` resolves to `cuda:0` for every process.

[docs/PARALLELISM.md](PARALLELISM.md) tells the whole story --
six releases, what each was worth, and the four conclusions that turned
out wrong. `bench/PARALLELISM-PLAN.md`, developer-only, is the plan that
followed from this -- CPU-level document parallelism first, GPU
assignment second, in that order because that is where the measurement
says the wall clock is.

## Choosing an embedding model

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

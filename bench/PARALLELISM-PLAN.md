# Plan: make the Docling parse path use this host's CPUs, then its GPUs

Written 2026-08-02 against the measurements in [RESULTS.md](RESULTS.md).
Read that first -- every claim about what is slow, and why, is measured
there rather than asserted here.

The one-line summary of the measurement: a full Docling parse of the
501-PDF bib corpus takes **~1.6 hours**, during which one A40 runs at
**~7% utilisation**, three A40s are idle, and **3 of 48 CPUs** are busy.
The GPU is worth only **1.79x** over CPU-only on this workload. So the
wall clock is in CPU-level document parallelism, and that is what this
plan does first.

Phases are ordered by (measured benefit) / (risk), and each is
independently shippable.

## Phase 0 -- make OCR optional, and stop rebuilding the converter -- **DONE (v0.12.0)**

Measured outcome: **2.46x** from turning OCR off, taking the full-corpus
parse from ~1.6 hours to ~39 minutes -- more than the GPU itself is worth.
It is a trade-off rather than a free win: it changed the extracted text of
8 of 16 sampled documents, because OCR is what reads text embedded as
bitmaps (mostly publisher furniture, but on one document two whole
tables). See RESULTS.md's Phase 0 sections. The original plan for this
phase follows, for the record.

No parallelism at all, and likely the largest single win. Also a
prerequisite: a process pool that rebuilds models per document would pay
that cost N times over.

- **Add an OCR toggle to `config.toml`, default off.** Docling's
  `do_ocr` defaults to `True` and its OCR runs on the CPU (RapidOCR on
  onnxruntime) -- a prime suspect for the CPU-bound profile in RESULTS.md,
  on a corpus of born-digital papers whose run log was full of
  `RapidOCR returned empty result!`. Default-off is the right default for
  *this* corpus; the key exists so a user with scanned PDFs can turn it
  back on. Note in the config comment that this changes extracted text
  for scanned documents -- it is a real behaviour change, not just a
  speed knob.
- **Hoist the converter** to a lazily-built, options-keyed singleton in
  `src/pdf_text.py`, and out of `parse_doc` into `parse_corpus` in
  `src/heavy/docling_parse.py`. `parse_doc` already takes an injected
  `cache` for exactly this reason -- follow that parameter shape, and
  keep the standalone path working by building on demand when nothing is
  injected, mirroring how `cache=None` is handled today.
- **Measure the rebuild cost first**, with the harness that already
  exists: `bench_docling.py --mode fresh` against `--mode reused`. That
  turns fact (2) in RESULTS.md from an inference into a number.

Verifiable independently of any timing claim: with OCR left on, the
emitted `.md` bytes should be unchanged.

## Phase 1 -- CPU: a process pool over documents -- **DONE for `sync` (v1.0.0)**

Shipped as `[parser].workers`, **defaulting to 1** so a default run is
still the historical serial path -- no pool, no pickling, no
subprocesses. The resolved count is clamped to
`min(requested, allowed_cpus // 4, docs_needing_parse)`, so a four-core
desktop resolves to 2 and an over-large request is clamped and reported
rather than silently obeyed.

**Still to do:** `src/heavy/docling_parse.py`'s `parse_corpus` is
untouched and remains serial. It belongs with Phase 2, since per-worker
GPU assignment lands in the same place.

The original plan for this phase follows, for the record.

**Processes, not threads.** `sync.py`'s existing comment is right that
`pdftotext` (an external subprocess, GIL released) wants threads -- but
Docling runs in-process and holds the GIL, so a `ThreadPoolExecutor`
would serialise exactly the work we want overlapped. This should be
**backend-conditional**: threads for `pdftotext`, processes for
`docling`. That changes the *conclusion* in that comment, not its
premises, and the comment must be rewritten to say so rather than left
contradicting the code.

- `ProcessPoolExecutor` with a **worker initialiser** that builds one
  converter per worker, so Phase 0's saving is amortised across that
  worker's whole shard rather than paid per task.
- **Only `extract_text` goes to the pool.** Every
  `ledger.upsert_reference` / `mark_parsed` / `mark_parse_failed` call
  stays on the main process as futures complete -- sqlite has a single
  writer, and this is the shape `sync.py`'s comment already commits to.
- Workers exchange `(citekey, pdf_path)` and return
  `(citekey, out_path | error)`. Nothing unpicklable crosses the
  boundary; the parse-quality guard and ledger semantics are untouched.
- **Worker count**: `len(os.sched_getaffinity(0)) // num_threads`, with
  Docling's `AcceleratorOptions.num_threads` set explicitly rather than
  left at its default of 4. On this host: 48/4 = 12. Exposed as
  `[parser] workers` in `config.toml`, `"auto"` by default and `1` to
  restore today's behaviour exactly. **Not** `os.cpu_count()` -- see
  fact (4) in RESULTS.md.
- **Longest-first scheduling.** With one 675-page document in the corpus,
  picking it up last bounds the whole run by that one document. Sort by
  page count descending before submitting; page counts are milliseconds
  via `pypdfium2`. This is the LPT heuristic `run_parallel.py` already
  uses for its shards.
- **Deterministic output**: futures complete out of order, so sort the
  per-document log lines before printing or `sync`'s output stops being
  reproducible run to run.
- **Failure isolation**: a worker killed by the OOM killer must fail one
  document, not the run. `BrokenProcessPool` needs handling the current
  `try/except ExtractionError` does not have.

Ceiling: at ~3 logical CPUs per worker against 48 allowed, the CPU
saturates somewhere around 12-16 workers.

## Phase 2 -- GPU: spread the workers across the four A40s -- **now the binding constraint**

This phase was written as "modest benefit, the GPUs are close to
redundant on this host". Phase 1's measurement overturned that. At 12
workers, GPU 0 runs pinned at **100%** while GPUs 1-3 sit at **0%**, and
the 4-to-12-worker speedup is 3.60x to 3.69x -- i.e. nothing. The parse
is no longer CPU-bound; it is bound by one card out of four.

Do this next, and expect it to matter. The plan below stands as written;
only the expected payoff changed.

Worth doing only after Phase 1, and only *because* of it: a single
process cannot use more than one GPU here, so there is nothing to spread
until there are several workers -- and per fact (3) in RESULTS.md, those
workers would all land on `cuda:0`.

- Assign worker `i` to GPU `i % <gpu count>` in the pool initialiser.
- **Mechanism**: prefer passing `AcceleratorOptions(device="cuda:N")`
  directly if docling 2.117 accepts an indexed device string -- check
  this first, because it sidesteps an ordering constraint. The fallback
  is setting `CUDA_VISIBLE_DEVICES` in the worker before torch
  initialises CUDA, which forces the `"spawn"` start method (under
  `"fork"`, a parent that has already touched CUDA hands the child a
  broken context). `CUDA_VISIBLE_DEVICES` is known-good -- the benchmark
  harness uses it.
- **VRAM is a non-issue**: 1.7 GB per worker against 46 GB per card. Even
  12 workers fit on one A40. Do not size the pool off GPU memory.
- **State the benefit honestly.** At ~7% SM per worker, one A40 absorbs
  roughly 12 workers before the GPU constrains anything -- which is about
  where the CPU runs out anyway. On *this* host the four GPUs are close
  to redundant for this workload. They become load-bearing if the worker
  count rises, or if Phase 0's OCR change shifts the remaining work onto
  the GPU.

## Phase 3 -- keep it measurable

- Keep `bench/`. It is what turns "docling is ~42x pdftotext" (../docs/PDF-PARSER.md,
  measured on 5 PDFs) into a figure that covers the real corpus.
- Record pages/s in `sync`'s summary line, so a regression shows up
  without anyone running a special benchmark.
- Update `config.toml`'s `[parser]` comment: it currently warns that a
  first-time Docling sync "will take hours, not minutes, and that loop
  still runs serially". After Phase 1 the second clause is false, and the
  first should carry a measured figure.

## Projected wall clock -- projections, not measurements

Scaling was **not** measured (see RESULTS.md, "Not measured"). These
extrapolate the measured 0.43 s/page serial figure and the
~3-cores-per-worker observation, and assume near-linear CPU scaling up to
saturation -- which is precisely the assumption that needs testing.

| Workers | Projected | Confidence |
|---|---|---|
| 1 (today) | ~1h 36m | measured |
| 4 | ~25 min | moderate -- well inside CPU headroom |
| 8 | ~13 min | moderate |
| 12-16 | ~8-10 min | low -- near CPU saturation, expect sublinear |

Realistic target: **single-digit-to-low-teens minutes against ~1.6 hours
today** -- and essentially all of it from Phases 0 and 1, not from the
GPUs.

## Open questions to resolve while implementing

- How much does OCR actually cost? Phase 0 should measure it, because the
  answer changes the worker-sizing arithmetic in Phase 1.
- Does docling 2.117's `AcceleratorOptions` accept `"cuda:1"`? Decides
  Phase 2's mechanism.
- Should `parse_corpus`'s incremental cache be saved incrementally under
  parallelism? It currently saves once at the end, so a killed bulk run
  loses the whole run's bookkeeping.

# Measured: what a full Docling parse of the bib corpus costs

Measured 2026-08-02. Host: 4x NVIDIA A40 46GB, driver 555.42.02, CUDA
12.5, 96 logical CPUs of which this container is allowed 48
(`Cpus_allowed_list: 0-23,48-71`). docling 2.117.0, torch 2.7.1+cu126.

Raw per-PDF timings: `results/2026-08-02-baseline/*.jsonl`.
Reproduce with the commands in [README.md](README.md).

## The corpus

`papers/bibliography.bib` has 646 entries; 501 resolve to a PDF on disk
(94 `non_pdf_attachment`, 50 `no_file_field`, 1 `pdf_path_gone`).

| | |
|---|---|
| PDFs to parse | 501 |
| Total pages | 13,400 |
| Total bytes | 1.54 GB |
| Pages: median / mean | 16 / 26.7 |
| Pages: p90 / p95 / max | 39 / 63 / 675 |

A single 675-page document is 5% of all pages. Any scheduling that picks
it up last is bounded below by that one document.

## Wall clock

Sample: 16 PDFs at even page-rank intervals (943 pages), so the sample's
page mix mirrors the corpus's.

| Configuration | s/page | Full-corpus estimate |
|---|---|---|
| 1 process, 1 A40 | 0.43 | **1h 36m** (per-page), 1h 56m (per-doc fit) |
| 1 process, CPU only | 1.37 | 5h 05m (per-page), 5h 21m (per-doc fit) |

> **Superseded 2026-08-04.** These are *extrapolations from a 16-PDF
> sample*, and both models understate a real run. Measured directly, a
> serial full-corpus pass with OCR **on** takes 6941.4s (1h 56m) -- which
> the per-doc fit got right and the per-page model missed by 21%. With
> OCR **off** the measured figure is 3330.4s (55m 30s) against a per-page
> prediction of ~39m: **41% low**. See
> ["2026-08-04: the full-corpus sweep"](#2026-08-04-the-full-corpus-sweep).

Per-PDF cost ranged 0.11-1.52 s/page, so read those as a band of roughly
1.5-2 hours on GPU, not a point estimate.

## The GPU is not the bottleneck

Sampled with `nvidia-smi dmon` during the serial run, and confirmed
against a CPU-only run of the same PDFs:

- **GPU SM utilisation averaged ~7%** -- mostly 0%, spiking to 47%.
- **VRAM: 1.7 GB of 46 GB** (3.6%).
- **The process sat at ~300% CPU** -- 3 of the 48 allowed logical CPUs.
- **Like-for-like GPU vs CPU on the same 6 PDFs: 32.0s vs 57.4s = 1.79x.**

One Docling process leaves ~93% of one A40 idle and touches 6% of the
available CPU; three of the four GPUs are never addressed at all. The
work is CPU-bound -- PDF backend, layout post-processing, and OCR (which
runs on onnxruntime, on the CPU) -- with short GPU bursts for the layout
and table models.

That 1.79x is the entire benefit the GPU currently delivers, and it is
why this work pursued CPU-level parallelism first and GPU assignment
second.

## Three code facts behind the numbers

1. **`src/sync.py`'s parse loop is serial** -- a plain
   `for ref in references:`. Its own comment names `ProcessPoolExecutor`
   as the deferred candidate and pre-commits to the right shape (ledger
   writes stay on the main thread). The reasons it gives for deferring
   hold for the `pdftotext` default and for routine incremental syncs;
   they do not hold for a bulk Docling run.

2. **`src/pdf_text.py:_extract_docling` builds `DocumentConverter()`
   inside the function** -- once per PDF. `initialized_pipelines` is an
   instance attribute, so every document re-initialises the models.
   Measured cold start for the first converter: **16.5s**.
   `src/heavy/docling_parse.py` calls `_build_converter()` inside
   `parse_doc`, with the same effect.

3. **`AcceleratorDevice.AUTO` resolves to `cuda:0`, always**
   (`decide_device("auto") == "cuda:0"`, verified directly). A bare
   `DocumentConverter()` does use a GPU with no configuration -- but
   every process that starts picks *the same* GPU. Using all four A40s
   requires explicit device assignment; it will not happen on its own.

A fourth fact, host-specific but a live trap for worker sizing:

4. **`os.cpu_count()` reports 96 here; only 48 are allowed.** Pool sizing
   must use `len(os.sched_getaffinity(0))`. Sizing off `cpu_count()`
   would oversubscribe 2x. This already bit the benchmark run: a
   `taskset -c 24-39` invocation failed outright with `Invalid argument`.

## 2026-08-02: parallel `sync`, and the ceiling it hit

Measured 2026-08-02 with the real `python -m src.sync`, not the bench
harness: 60 bib PDFs (1,166 pages), `parser.backend = "docling"`, OCR off,
via `[parser].workers`.

| workers | wall clock | speedup | efficiency |
|---|---|---|---|
| 1 | 444.4s | 1.00x | -- |
| 4 | 123.4s | **3.60x** | 90% |
| 12 | 120.3s | 3.69x | 31% |

Four workers scale almost linearly. Twelve buy essentially nothing over
four, and `nvidia-smi dmon` during that run says exactly why:

```
# gpu    sm%          <- GPU 0 pinned at 100%, GPUs 1-3 idle throughout
    0    100
    1      0
    2      0
    3      0
```

**The bottleneck has moved.** Before this work the parse was CPU-bound
and one A40 idled at ~7%. Enough CPU parallelism to saturate that GPU
turns the same workload into a *single-GPU-bound* one -- while the other
three A40s are never addressed at all, because Docling's
`AcceleratorDevice.AUTO` resolves to `cuda:0` in every worker process
(fact 3 above).

That is the argument that produced per-worker GPU assignment: the four
GPUs stopped being redundant the moment document parallelism landed. A 60-document sample already saturates one card at 12
workers, so the full 501-document corpus will too.

On smaller batches the per-worker model load dominates instead: over 8
documents, 4 workers gave 1.90x and 8 workers gave none (34.6s / 18.3s /
19.3s). Each worker pays its own cold start, so parallelism is worth
having in proportion to how much work there is -- which is why the
resolved worker count is capped by the number of documents needing a
parse.

Correctness was checked, not assumed: `content/parsed/` after a 4-worker
run is byte-identical to the serial run's, and the ledger rows match.

## 2026-08-02: OCR costs more than the GPU saves

Measured 2026-08-02 on the same 16-PDF sample and the same GPU, with
`bench_docling.py --no-ocr`. Raw timings:
`results/2026-08-02-phase0/gpu_reused_noocr.jsonl`.

| | s/page | Full corpus |
|---|---|---|
| OCR on (Docling's default) | 0.431 | ~1h 36m |
| OCR off | 0.176 | **~39m** |

**2.46x**, from one setting -- more than the 1.79x the GPU is worth.

> **Superseded 2026-08-04.** 2.46x is a *serial 16-PDF sample* figure.
> Measured end to end on the full corpus, OCR costs **2.08x serially but
> 3.91x at 12 workers and 4.79x at 24** -- it is CPU-bound, so it
> competes with the parallelism. Quoting 2.46x for a parallel run
> understates it by roughly half.
Docling's OCR runs on the CPU (RapidOCR on onnxruntime), which is a large
part of why this pipeline is CPU-bound.

### It is not free, and the cost is easy to miss

Turning OCR off changed the extracted text of **8 of the 16** documents.
That is the number to design around, not the speedup.

OCR only runs on *bitmap* regions, so what it recovers is text that
exists in the PDF as an image rather than as characters. Diffing the two
outputs, that breaks down as:

- **Publisher furniture** -- `IEEEAccess`, `DTU Library`, logos. Noise;
  losing it is an improvement.
- **Figure sub-captions** -- e.g. "(a) The system context physical block
  diagram models the boundary of the system...". Borderline.
- **Real content, on a minority of documents.** `afrin_resource_2021`
  lost **10.1%** of its characters: two complete tables, a
  three-column comparison matrix and an abbreviations list, both embedded
  as images. `perno_implementation_2022` lost a paragraph of body prose
  set in a graphical text box.

So the default is `ocr = false` (2.46x on this serial sample -- 2.08x to
4.79x measured end to end depending on worker count, see below), but it
is a trade-off rather than a free win, and the
parse-quality guard will not catch a bad choice -- it looks for
run-together words, not for content that never arrived. A corpus of scans
needs `ocr = true`; so does one where tables-as-images matter more than
parse time.

## 2026-08-02: the converter rebuild

`DocumentConverter` cold start is **16.5s** on this host, and
`initialized_pipelines` is an instance attribute -- so the pre-0.12.0
`src/pdf_text.py`, which built one converter per PDF, paid a model reload
for every document in the corpus. Both `pdf_text.py` and
`heavy/docling_parse.py` now build one converter and reuse it, and
`parse_corpus` defers the build until a document actually needs parsing,
so a fully-cached re-run loads no models at all.

## 2026-08-02: spreading workers across the four A40s

Measured 2026-08-02 with the real `python -m src.sync` over the **whole
501-PDF corpus** (13,400 pages), `docling`, OCR off, 12 workers. The A/B
is the same binary either way -- `CUDA_VISIBLE_DEVICES=0` confines every
worker to one card, which is exactly the pre-v1.1.0 behaviour, since
`AcceleratorDevice.AUTO` resolves to `cuda:0` in every process.

| | wall clock |
|---|---|
| 12 workers, one A40 (`AUTO`, i.e. before this change) | 528.0s |
| 12 workers, four A40s (round-robin) | **326.2s** |
| Speedup from using the other three cards | **1.62x** |

Against the ~39-minute serial baseline, the full corpus now parses in
**5m26s -- about 7x**.

### Corpus size decides whether this is worth anything

The same change measured on a 60-document subset showed **nothing**:
122.4s at 4 workers, 123.0s at 12, with all four GPUs busy and the CPU
~85% idle. Per-worker startup -- spawn, importing torch and docling, then
loading the models -- dominates at that size, and no amount of GPU
spreading helps. It only pays once there is enough work to amortise
twelve workers' startup, which the full corpus has and a 60-document
sample does not.

This is also why the earlier reading of the 12-worker plateau as
"GPU 0 is the bottleneck" was too simple. GPU 0 *was* pinned at 100%, but
freeing it did not speed up the 60-document run at all -- the plateau
there was startup, not contention. Both effects are real; they show up at
different scales.

### Output is not bit-reproducible under concurrency

Comparing the one-GPU and four-GPU runs over all 501 documents: **6 files
differ**, by 0 to 59 bytes out of ~100KB each (under 0.06%).

The differences are not device-dependent -- parsing the same document
explicitly on `cuda:0`, `cuda:1` and `cuda:2` gives byte-identical output
every time, and repeating a run at the same worker count reproduces
exactly. What varies is Docling's element grouping inside **dense
reference blocks** under heavy concurrency: the same words, split across
list elements or lines differently.

Nothing is lost, and retrieval tokenises on whitespace, so this does not
affect BM25 ranking.

**Can it be turned off?** Not from Docling. Its `PdfPipelineOptions` has
no determinism, seed or reproducibility setting of any kind (checked
against 2.117.0's full field list), and `AcceleratorOptions` exposes only
`device`, `num_threads` and `cuda_use_flash_attention2`. The only lever
is below Docling, in torch: `torch.use_deterministic_algorithms(True)`
plus `cudnn.deterministic`, set inside each worker. That is not taken
here, for two reasons -- it costs throughput on exactly the models this
pipeline spends its time in, and it raises rather than degrades when an
op has no deterministic implementation, which would turn a cosmetic
difference into a hard failure. Revisit if bit-reproducible parses ever
become a requirement rather than a nicety.

It does mean `content/parsed/` should not be expected to be
byte-identical across runs at high worker counts -- v1.0.0's
"byte-identical to serial" observation was measured over 8 documents at 4
workers, where it holds, and does not generalise to 501 documents at 12.

## Per-worker startup: where the ~10s goes, and how much of it is shareable

Measured 2026-08-03 on the same host. The question this section answers
is the one the 60-document plateau above raised: workers were spending
about ten seconds each before producing anything, so what *is* that ten
seconds, and can a different multiprocessing start method share any of
it?

### The breakdown

One cold process, timed at each stage, parsing a small PDF twice (docling
2.117.0, OCR off, `cuda:0`, warm HuggingFace cache):

| Stage | Time |
|---|---|
| `import torch` | 1.16s |
| `import docling` (the 4 modules the converter needs) | 2.08s |
| `DocumentConverter(...)` construction | 0.13s |
| First `convert()` -- Docling loads its models here | 5.17s |
| **Total before the first parsed page** | **8.5s** |
| Second `convert()` of the same PDF, models warm | 0.33s |

So of the ~8.5s: **3.2s is importing Python modules and ~5.0s is loading
models.** Only the first is even a candidate for sharing between
processes -- `initialized_pipelines` lives on the converter instance, in
whichever process built it.

Two things that looked like they might be in that 5s, and are not:

- **CUDA context creation is almost none of it.** The same first
  `convert()` on `device="cpu"` takes 6.32s of which 2.24s is the parse
  itself, i.e. ~4.1s of model load; on `cuda:1` it is 5.24s of which
  0.42s is the parse, i.e. ~4.8s. The GPU adds ~0.7s over the CPU-side
  work of reading weights and building modules.
- **HuggingFace hub lookups are not it either.** `HF_HUB_OFFLINE=1`
  changed the first convert by 0.24s.

### Does `torch.cuda.device_count()` initialise CUDA in the parent?

**No -- and the code comment that said it did was wrong.** That claim was
the stated reason `sync` used `spawn`. Checked directly against torch
2.7.1: after `torch.cuda.device_count()` returns 4,
`torch.cuda.is_initialized()` is still `False`, and a child forked from
that parent allocates on `cuda:0` without complaint. torch routes device
counting through NVML precisely to keep that safe. Only *using* a device
in the parent breaks the child, and then it breaks loudly:

```
RuntimeError: Cannot re-initialize CUDA in forked subprocess.
```

`gpu_count()` now asks `nvidia-smi --list-gpus` instead, falling back to
torch only when the driver's own tool isn't on PATH. That is not because
the torch path was unsafe on this host -- it wasn't -- but because it
made safety depend on an implementation detail of one torch version, and
because it imported 1.2s and ~200MB of torch into a parent with no other
use for it. `CUDA_VISIBLE_DEVICES` has to be applied by hand on that
path: nvidia-smi ignores it, and torch does not.

### fork is still ruled out, for a different reason

Not CUDA -- sqlite. By the time `sync` builds its pool it holds two live
sqlite connections: the run lock (`BEGIN IMMEDIATE`, deliberately never
committed) and the ledger. SQLite's own documentation says not to carry
an open connection across `fork()`, and a forked worker finalising an
inherited connection on its way out would be rolling back a transaction
belonging to a process it is not.

Measurement removed the temptation anyway. Wall clock for N processes to
each build a converter and parse one PDF, charging any parent-side
prewarm to the total:

| Workers | spawn | fork | fork, parent pre-imports | forkserver | forkserver + preload |
|---|---|---|---|---|---|
| 4 | 11.26s | 9.68s | 9.70s | 10.39s | **9.55s** |
| 12 | 13.96s | 13.25s | 13.00s | 13.41s | **12.61s** |

forkserver with a preload list is the fastest at both sizes *and* is the
only one that inherits nothing from the parent -- its server is a fresh
interpreter, launched with `spawnv_passfds`, so workers get the preloaded
modules and no sqlite connections, no CUDA context, no file descriptors.

### The result that decided the design: a shared import is a wash

The obvious reading of the breakdown is "3.2s x N workers, shared once by
forkserver". That reading is wrong, and the real `sync` says so:

| 8 documents, 4 workers | median of 3 |
|---|---|
| spawn | 22.9s |
| forkserver, preload set at pool construction | 22.4s |

Workers import **concurrently**. On a host with CPUs to spare their
imports were already overlapped, so what forkserver takes out of N
children it puts back into the one parent that runs the preload -- and
the preload blocks pool construction. Net: nothing.

What *is* serial is the preload itself. Started before the parent reads
the bibliography rather than when the pool is built, it runs during the
~2.5s that read takes:

| | 4 live workers ready at |
|---|---|
| forkserver started when the pool is built | 6.90s |
| forkserver started before the bib read | **4.40s** |

`multiprocessing.forkserver.ensure_running()` returns in ~0.02s -- it
launches the server and does not wait for its imports -- so this costs
the caller nothing but the ordering.

### End to end, on the real `sync`

Medians of three runs each, fresh `content/` every time so every document
needs a parse. Rank-stratified subsets of the bib corpus, `docling`, OCR
off, on the four-A40 host. `workers = 1` takes the serial path, which has
no pool and therefore no start method.

| Documents | Workers | spawn | forkserver | Saving |
|---|---|---|---|---|
| 8 | 1 (serial) | 46.2s | -- | -- |
| 8 | 4 | 23.1s | **21.8s** | 1.3s (5.6%) |
| 8 | 8 | 22.9s | **20.7s** | 2.2s (9.6%) |
| 60 | 1 (serial) | 383.2s | -- | -- |
| 60 | 4 | 103.6s | **101.9s** | 1.7s (1.6%) |
| 60 | 12 | 80.8s | **78.8s** | 2.0s (2.5%) |

Run-to-run spread was 0.3-1.0s, so the effect clears the noise at every
point, and it is the *same* effect at every point: a roughly constant
1.3-2.2s off pool startup. What changes with corpus size is only how much
of the total that is -- 9.6% of an 8-document run, 2.5% of a 60-document
one, and it would be well under 1% of the full 501-PDF corpus.

**So this does not make a bulk parse meaningfully faster, and it was
never going to.** The startup breakdown said so before any of these runs:
3.2s per worker, shared once, against a parse measured in minutes. What
it does help is the case the earlier measurements kept running into --
a handful of documents, where startup *is* the run.

Correctness was checked rather than assumed: over 8 documents at 4
workers, `content/parsed/` is byte-identical between the two start
methods and the ledger rows match. Ctrl+C behaviour is unchanged --
exit 130, no orphaned processes, and the same
`resource_tracker: ... leaked semaphore` warning that `spawn` already
produced (a consequence of `os._exit` skipping interpreter shutdown, not
of the start method).


## 2026-08-04: the full-corpus sweep

Measured with the real `python -m src.sync` over **all 501 PDFs** rather
than a sample, on the same machine (4x A40, 48 CPUs available of 96 host
logical CPUs), repository at `92c1420` (v2.1.0). Every run started from
an empty `CONTENT_DIR` and reported 501 parsed, 0 failed. Raw records:
`results/2026-08-04-full-corpus/sweep.jsonl` -- **wall clock only.** The
CPU-busy and GPU-utilisation figures quoted below came from separate
instrumented runs on the same machine and are *not* in that file; the
resource sampler was added to `sweep_sync.py` afterwards, so
`results/2026-08-04b-repeats/sweep.jsonl` is the first record set
carrying them. Treat the timings here as reproducible from the committed
data and the utilisation figures as reported, not evidenced.

Reproduce with `bench/sweep_sync.py`, which was written for exactly this
and did not exist when the earlier sections were measured.

### It corrected the baseline, which corrected everything downstream

| | |
|---|---|
| Serial, OCR off -- **measured** | **3330.4s (55m 30s)** |
| Serial, OCR off -- per-page extrapolation (what the docs quoted) | ~39m, **41% low** |
| Serial, OCR off -- per-doc fit | ~50m 32s, 9% low |

One wrong denominator propagated into every efficiency figure in this
repository. `bench/estimate.py` now leads with the per-doc model and says
plainly that both understate.

### Worker scaling

| Workers | Wall clock | Speedup | Efficiency | |
|---|---|---|---|---|
| 1 | 3330.4s | 1.00x | -- | |
| 4 | 799.2s | 4.17x | 104% | |
| 8 | 428.6s | 7.77x | 97% | |
| 12 | 310.2s | 10.74x | 89% | the most `worker_ceiling()` allows |
| 16 | 268.1s | 12.42x | 78% | |
| 24 | 237.6s | 14.02x | 58% | |
| **32** | **220.7s** | **15.09x** | 47% | single run; see 2026-08-04b |
| 48 | 226.3s | 14.72x | 31% | single run; see 2026-08-04b |

**The clamp is costing 1.41x.** `worker_ceiling()` caps at
`allowed_cpus // 4 = 12`; the optimum is near 32. Rows above 12 required
relaxing that constant and are not reachable with a stock checkout.

`[parser].workers = 16` resolves to 12 and takes 315.9s -- the clamp
working as documented.

### The `_CPUS_PER_DOCLING_WORKER = 4` model is wrong

CPU busy during these runs, against the 48 available:

| Run | CPUs busy | of the 48 allowed |
|---|---|---|
| 16 workers | 18.7 | 39% |
| 32 workers | 34.0 | 71% |
| 24 workers, OCR on | 44.6 | **93%** |

At 32 workers the CPU is still only 71% busy. The constant came from a
single "~300% CPU" observation of one process; the optimum implies a
divisor near **1.5**.

Confirmed independently: docling's own `num_threads` barely matters.

| threads (at 12 workers) | 1 | 2 | 4 (default) | 8 |
|---|---|---|---|---|
| wall clock | 305.3s | 304.3s | 310.2s | 305.6s |

**1.9% spread -- noise.** The hypothesis that `12 workers x 4 threads`
oversubscribes 48 CPUs is disproved, and it explains why more workers
help: the threads a worker is charged for are not doing much.

### GPUs

| Workers | 1 GPU | 2 GPUs | 4 GPUs | 1->2 | 2->4 |
|---|---|---|---|---|---|
| 12 | 518.4s | 339.7s | 310.2s | 1.53x | 1.10x |
| 24 | 535.8s | 298.6s | 237.6s | **1.79x** | 1.26x |

The second card is worth far more than the third and fourth, and matters
more at higher worker counts. At 24 workers, one GPU is *slower* than at
12 -- piling workers onto a single card is counterproductive.

These agree with the 2026-08-02 phase 2 figures (528.0s / 326.2s) to
within 2-5% on a rebuilt venv, which is the cross-check that the parallel
measurements were sound and only the *baseline* was wrong.

### OCR

| Workers | OCR off | OCR on | Cost |
|---|---|---|---|
| 1 | 3330.4s | 6941.4s | 2.08x |
| 12 | 310.2s | 1213.9s | 3.91x |
| 24 | 237.6s | 1139.0s | **4.79x** |

Speedup from 1 to 24 workers: **14.02x with OCR off, 6.09x with it on.**
OCR roughly halves how well the pipeline parallelises.

### Open, not glossed

- **The OCR optimum was not found** -- swept only to 24 workers, where it
  was still improving.
- **One machine, one corpus.** Any specific replacement divisor may not
  generalise, particularly to a CPU-only machine where the GPU is doing
  none of the work.

Two questions this section used to list were answered on 2026-08-04b,
below.

## 2026-08-04b: repeats, and where the time goes

The single-run sweep above left two questions. Three runs per point, with
`sweep_sync.py` timing each run's phases, settled both. Raw records:
`results/2026-08-04b-repeats/sweep.jsonl`.

| Workers | Runs (s) | Median | Spread |
|---|---|---|---|
| 24 | 234.6, 235.0, 235.8 | 235.0s | 1.2s |
| 32 | 222.8, 223.4, 309.6 | 223.4s | **86.8s** |
| 48 | 220.4, 221.4, 227.7 | 221.4s | 7.3s |

### The 32 -> 48 "reversal" was noise

Medians put 32 and 48 **0.9% apart** — while the spread *within* the
32-worker configuration alone is 86.8s. The single-run pass had 32 at
220.7s and 48 at 226.3s and read that as a knee; with repeats the
ordering flips.

**The curve plateaus past ~32; it does not turn back up.** Single runs
cannot resolve anything in that region, which is the argument for
`--repeat`.

### What flattens the curve: startup, plus the CPU filling up

Timing each run's phases separates the three candidates:

| Workers | Startup (to 1st document) | Tail (after last) | CPU busy |
|---|---|---|---|
| 24 | 18.6s — **7.9%** | 4.9s — 2.1% | 56% |
| 32 | 21.8s — **8.9%** | 5.9s — 2.6% | 70% |
| 48 | 28.5s — **12.7%** | 7.9s — 3.6% | 78% |

- **Startup is a growing tax, not a fixed one.** Every worker pays its
  own ~8.5s model load, so the cost of standing the pool up rises with
  the pool: 7.9% of the run at 24 workers, 12.7% at 48.
- **The CPU is heading for saturation** — 56% to 78% host-wide across the same
  range. Earlier the 71% figure at 32 workers was read as "the CPU is not
  the limit"; against 56% at 24 and 78% at 48 it is clearly *becoming*
  one.
- **The long-document tail is not the story**: 5-8s throughout, under 4%.

Neither cost alone explains the plateau. Together they account for it,
and both worsen with every worker added — which is why past ~32 there is
nothing left to win by adding more.

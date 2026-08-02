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

That 1.79x is the entire benefit the GPU currently delivers. It is the
reason [PARALLELISM-PLAN.md](PARALLELISM-PLAN.md) does CPU-level
parallelism first and GPU assignment second.

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

## Phase 1, measured: parallel `sync`, and the ceiling it hits

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

That is what Phase 2 of PARALLELISM-PLAN.md is for, and this measurement
is the argument for it: the four GPUs stopped being redundant the moment
Phase 1 landed. A 60-document sample already saturates one card at 12
workers, so the full 501-document corpus will too.

On smaller batches the per-worker model load dominates instead: over 8
documents, 4 workers gave 1.90x and 8 workers gave none (34.6s / 18.3s /
19.3s). Each worker pays its own cold start, so parallelism is worth
having in proportion to how much work there is -- which is why the
resolved worker count is capped by the number of documents needing a
parse.

Correctness was checked, not assumed: `content/parsed/` after a 4-worker
run is byte-identical to the serial run's, and the ledger rows match.

## Phase 0, measured: OCR costs more than the GPU saves

Measured 2026-08-02 on the same 16-PDF sample and the same GPU, with
`bench_docling.py --no-ocr`. Raw timings:
`results/2026-08-02-phase0/gpu_reused_noocr.jsonl`.

| | s/page | Full corpus |
|---|---|---|
| OCR on (Docling's default) | 0.431 | ~1h 36m |
| OCR off | 0.176 | **~39m** |

**2.46x**, from one setting -- more than the 1.79x the GPU is worth.
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

So the default is `ocr = false` (2.46x, and most documents are
unaffected), but it is a trade-off rather than a free win, and the
parse-quality guard will not catch a bad choice -- it looks for
run-together words, not for content that never arrived. A corpus of scans
needs `ocr = true`; so does one where tables-as-images matter more than
parse time.

## Phase 0, measured: the converter rebuild

`DocumentConverter` cold start is **16.5s** on this host, and
`initialized_pipelines` is an instance attribute -- so the pre-0.12.0
`src/pdf_text.py`, which built one converter per PDF, paid a model reload
for every document in the corpus. Both `pdf_text.py` and
`heavy/docling_parse.py` now build one converter and reuse it, and
`parse_corpus` defers the build until a document actually needs parsing,
so a fully-cached re-run loads no models at all.

## Phase 2, measured: spreading workers across the four A40s

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

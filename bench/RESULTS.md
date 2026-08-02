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

## Not measured

Parallel scaling. `run_parallel.py` exists and is ready, but no
multi-worker run was performed, so every multi-worker figure in the plan
is a projection from the serial measurement plus the
~3-cores-per-worker observation -- not a measurement. The plan says so
where it matters.

## An open question worth more than the plan itself

`do_ocr` defaults to `True`, and Docling's OCR (RapidOCR on onnxruntime)
runs on the CPU -- a prime suspect for the CPU-bound profile above. This
corpus is born-digital papers with real text layers, and the run log was
full of `RapidOCR returned empty result!`. Turning OCR off may be a
larger and far cheaper win than any amount of parallelism, and it would
shift what remains toward the GPU.

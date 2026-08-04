# Performance

What each setting in [CONFIG.md](CONFIG.md) costs, measured rather than
estimated. [CONFIG.md](CONFIG.md) says what a setting *does* and what
values it takes; this says what it *costs*, so neither document has to
carry both jobs.

Related reading:

- [PDF-PARSER.md](PDF-PARSER.md) -- how the two backends compare on
  fidelity, and why two other candidates were evaluated and dropped.
- [PARALLELISM.md](PARALLELISM.md) -- how the parallel parse is built:
  architecture diagrams, what each component does, and the roadmap.
- `bench/RESULTS.md` -- the raw measurement record with per-PDF timings.
  Developer-only: `bench/` is excluded from the release archive, so it is
  in the repository but not in a downloaded release.

## Read the numbers with the machine in mind

**Every figure below is one machine's, and yours will differ.** They are
here to give you ratios and orders of magnitude -- "OCR roughly halves
throughput", "the parse is CPU-bound, not GPU-bound" -- not absolute
times to plan against. Where a figure only makes sense against the
hardware, the hardware is named.

Two reference machines are used throughout, and
[README.md](../README.md#hardware-requirements) describes both in full:

| Name used below | What it is |
|---|---|
| **the small machine** | 4 cores, 9.7 GB RAM, no GPU |
| **the multi-GPU machine** | 96 logical cores (48 available to the process), 251 GB RAM, 4x NVIDIA A40 46 GB, driver 555.42.02, CUDA 12.5 |

The corpus is this project's own bibliography: **501 PDFs, 13,400 pages,
1.54 GB**, median 16 pages, with one 675-page book that is 5% of all
pages by itself. Software: docling 2.117.0, torch 2.7.1+cu126,
Python 3.12.3.

Reproduce any of it with the harness in `bench/` -- see `bench/README.md`.

## `[parser].backend` -- pdftotext or docling

Measured on 5 real bibliography PDFs, cold (no caching -- `pdf_text.py`
does not cache, so these are extraction times, not `sync`'s steady state,
which skips PDFs whose bytes have not changed).

| Backend | Total, 5 PDFs | Words extracted | Ratio |
|---|---|---|---|
| `pdftotext` | 1.43s | 68,888 | 1x |
| `docling` (OCR on, i.e. before OCR defaulted off) | 60.77s | 69,565 | ~42x |

Per document the ratio ranged **~18x-102x**, tracking document length
loosely at best -- so budget against the total, not the best case.

**Fidelity is close.** docling's word counts stay within ~3.5% of
`pdftotext`'s on every one of the five. The reason to pick docling is
structure (reading order, sections, tables), not word recovery -- and the
reason to pick `pdftotext` is speed plus page boundaries, which docling's
output does not have. [PDF-PARSER.md](PDF-PARSER.md) has the full
comparison, including the two backends that were evaluated and removed.

That ~42x figure is enough to choose a backend and useless for planning a
run, which is what the rest of this document is for.

## `[parser].ocr` -- the largest single lever, and a trade

**OCR's cost is not a single number.** It grows with worker count,
because OCR is CPU-bound and therefore competes with the parallelism you
added. Measured 2026-08-04, end to end over the whole 501-PDF corpus:

| Workers | OCR off | OCR on | Cost of OCR |
|---|---|---|---|
| 1 | 3330.4s | 6941.4s | **2.08x** |
| 12 | 310.2s | 1213.9s | **3.91x** |
| 24 | 237.6s | 1139.0s | **4.79x** |

Equivalently, from the other side -- **turning OCR on roughly halves how
well the pipeline parallelises**:

| | Speedup, 1 -> 24 workers |
|---|---|
| OCR off | 14.02x |
| OCR on | **6.09x** |

At 24 workers with OCR on, 93% of the available CPU is busy -- the one
configuration measured where this machine is genuinely full. docling's
OCR runs on the CPU (RapidOCR on onnxruntime), which is why.

An earlier figure of **2.46x** appears in older documents and in
`bench/RESULTS.md`. It came from a 16-PDF serial sample and is a
reasonable estimate of the *serial* cost (measured: 2.08x); it is not the
cost you will pay on a parallel run.

**It is not free, and this is the part to read twice.** OCR only runs on
*bitmap* regions, so what it recovers is text stored in the PDF as an
image rather than as characters. Turning it off changed the extracted
text of **8 of those 16 documents**:

- Mostly publisher furniture (`IEEEAccess`, `DTU Library`) and figure
  sub-captions. Losing that is an improvement.
- But one document lost **10.1% of its characters**, including two
  complete tables embedded as images, and another lost a paragraph of
  body prose set in a graphical text box.

So `false` suits born-digital papers. Set `true` for scans (with OCR off,
docling extracts almost nothing from a scan) or where tables-as-images
matter more than parse time. **The parse-quality guard will not catch a
wrong choice here** -- it looks for run-together words, not for content
that never arrived.

## GPU vs CPU -- less than it looks

| | s/page | Extrapolated to the corpus |
|---|---|---|
| One process, one GPU, OCR on | 0.43 | ~1.6 hours |
| One process, CPU only, OCR on | 1.37 | ~5.1 hours |
| Like for like, same 6 PDFs | | **1.79x** |

**1.79x is the entire benefit the GPU delivers on this workload.** During
that run the GPU averaged **~7% SM utilisation** and 1.7 GB of 46 GB,
while the process held ~300% CPU -- three of the 48 available cores.

docling is CPU-bound here (PDF backend, layout post-processing, and OCR
on the CPU). A GPU helps; it is not the answer, and the numbers below are
why the parallelism work went after CPU-level document concurrency first.

## `[parser].workers` -- document-level parallelism

**The largest lever on a multi-core machine, and the code currently caps
it well below where the curve flattens.**

Measured 2026-08-04 with the real `python -m src.sync` over the **whole
501-PDF corpus** (13,400 pages), docling, OCR off, 4 GPUs. Each row is
one run from an empty ledger; all reported 501 parsed, 0 failed:

| Workers | Wall clock | Speedup | Efficiency | |
|---|---|---|---|---|
| 1 | 3330.4s | 1.00x | -- | |
| 4 | 799.2s | 4.17x | 104% | |
| 8 | 428.6s | 7.77x | 97% | |
| 12 | 310.2s | 10.74x | 89% | <- **the most `worker_ceiling()` allows** |
| 16 | 268.1s | 12.42x | 78% | |
| 24 | 235.0s | 14.17x | 59% | median of 3 |
| **32** | **223.4s** | **14.91x** | 47% | median of 3 |
| 48 | 221.4s | 15.04x | 31% | median of 3 |

- **Scaling holds far better than previously documented.** 97% at 8
  workers, 89% at 12. The 104% at 4 is not an error: one worker does not
  saturate the threads it is given, so serial is a slightly unfair
  denominator.
- **The knee is somewhere past 24, not at 12.** `worker_ceiling()` caps
  at `allowed_cpus // 4`, which is 12 here. Running 32 is **~1.4x
  faster** -- available today only by changing that constant.
- **The curve plateaus from 32 to 48; it does not reverse.** Medians of
  three runs put them 0.9% apart (223.4s vs 221.4s), while the spread
  *within* the 32-worker configuration alone was 86.8s. An earlier
  single-run pass had 32 beating 48 and read that as a knee; with repeats
  the ordering flips. **Anything past ~32 is flat, and single runs cannot
  resolve it.**
- So the useful statement is "the divisor should be much smaller than 4",
  not "it should be 1.5". Any specific value here is arithmetic from
  **one machine and one corpus**, on a plateau, and a CPU-only machine
  would likely want a different one.
- Asking for 16 on this machine resolves to 12 and takes 315.9s. The
  clamp is doing exactly what it documents, and costing 1.43x.

**Why the cap is too low:** the constant models a worker as occupying 4
CPUs. It does not. Measured CPU busy, against the 48 available:

| Run | CPUs busy | of the 48 allowed |
|---|---|---|
| 16 workers | 18.7 | 39% |
| 32 workers | ~34 | ~70% |
| 24 workers, OCR on | 44.6 | **93%** |

At 32 workers -- well past the point the code will go -- the CPU is still
only ~70% busy. With OCR off a worker uses closer to one CPU than four.
(The 32-worker figure landed at 71% in one sweep and 70% in another; a
run-to-run point is worth about a percentage point, not a decimal.)

> **These CPU figures are host-wide.** `sweep_sync.py` samples
> `/proc/stat`, which counts every process on the machine, and expresses
> it against the 48 CPUs this process may use. On an otherwise-idle
> machine that is the run; on a busy one it is an **upper bound** on what
> the run used, and can exceed 100%. Treat them as "the machine was this
> busy", not "the parse used this much" -- and note that the conclusion
> below rests on the *trend* across configurations, not the absolute
> level.

### What flattens the curve past ~24 workers

Timing each run's phases separates the candidates:

| Workers | Startup (to 1st document) | Tail (after last) | CPU busy |
|---|---|---|---|
| 24 | 18.6s — **7.9%** of the run | 4.9s — 2.1% | 56% |
| 32 | 21.8s — **8.9%** | 5.9s — 2.6% | 70% |
| 48 | 28.5s — **12.7%** | 7.9s — 3.6% | 78% |

- **Startup is a growing tax, not a fixed one.** Every worker pays its
  own ~8.5s model load, so standing the pool up costs more the bigger the
  pool: 7.9% of the run at 24 workers, 12.7% at 48. (The column is time
  to the *first completion*, so it also contains the fastest document's
  parse -- an upper bound on startup rather than a measurement of it.
  The **growth** is the startup part: one document's parse does not get
  slower because the pool got bigger.)
- **The CPU is heading for saturation**, 56% to 78% across the same
  range. Read alone, "70% busy at 32" suggests headroom; read against 56%
  at 24 and 78% at 48, it is clearly *becoming* the limit.
- **The long-document tail is not the story** — 5-8s throughout, under 4%.

Neither cost alone explains the plateau; together they account for it,
and both worsen with every worker added.

**Corpus size still decides whether raising this is worth anything.** Over
8 documents, 4 workers gave 1.90x and 8 gave none at all (34.6s / 18.3s /
19.3s): each worker pays its own ~8.5s model load, so the benefit is
proportional to how much work there is to amortise it over. That is why
the resolved count is also capped by the number of documents needing a
parse.

## Multi-GPU -- nothing to configure

With docling and more than one worker, each worker process claims one
CUDA device round-robin. This is not automatic in docling: its
`AcceleratorDevice.AUTO` resolves to `cuda:0` in *every* process, so
without an explicit per-worker device every worker piles onto card 0
while the rest idle.

Measured over the whole 501-PDF corpus (2026-08-04, OCR off):

| Workers | 1 GPU | 2 GPUs | 4 GPUs | 1->2 | 2->4 |
|---|---|---|---|---|---|
| 12 | 518.4s | 339.7s | 310.2s | 1.53x | 1.10x |
| 24 | 535.8s | 298.6s | 237.6s | **1.79x** | 1.26x |

- **The second card is worth far more than the third and fourth.**
- **GPUs matter more at higher worker counts**, since more workers share
  each card.
- At 24 workers, one GPU is *slower* than at 12 (535.8s vs 518.4s):
  piling more workers onto a single card is counterproductive.

The 2026-08-02 run of the same 12-worker configuration measured 528.0s
and 326.2s -- within 2-5% of the figures above, on a different day and a
rebuilt venv.

Restrict which cards are used with `CUDA_VISIBLE_DEVICES`; there is no
separate setting, and the pool only ever sees what that leaves visible.

**And it is worth nothing on a small corpus.** The same change measured
over a 60-document subset showed no difference at all (122.4s at 4
workers, 123.0s at 12), with all four GPUs busy and the CPU ~85% idle.
Per-worker startup dominates at that size.

## `[parser].start_method` -- per-worker startup

A cold docling worker needs about **8.5s** before it produces its first
page on the multi-GPU machine:

| Stage | Time |
|---|---|
| `import torch` | 1.16s |
| `import docling` | 2.08s |
| Build the `DocumentConverter` | 0.13s |
| First `convert()` -- docling loads its models here | 5.17s |
| **Total before the first parsed page** | **8.5s** |
| A later `convert()`, models warm | 0.33s |

Only the ~3.2s of imports can be shared between processes; the ~5s model
load lives on the converter instance, in whichever process built it. So
`forkserver` -- which imports torch and docling once in a helper process
that every worker is forked from -- can address at most that 3.2s.

**And sharing the import, on its own, is worth nothing.** Workers import
concurrently, so on a host with spare CPUs that cost was already
overlapped. Measured head to head over 8 documents at 4 workers:
`spawn` 22.9s, `forkserver` 22.4s.

The saving is in *when* the preload runs. `sync` starts the forkserver
before reading the bibliography, so the import happens during the ~2.5s
that takes rather than blocking pool construction afterwards -- four live
workers ready at **4.40s instead of 6.90s**.

End to end on the real `sync`, medians of three runs, fresh output
directory each time:

| Documents | Workers | `spawn` | `forkserver` | Saving |
|---|---|---|---|---|
| 8 | 1 (serial) | 46.2s | -- | -- |
| 8 | 4 | 23.1s | **21.8s** | 1.3s (5.6%) |
| 8 | 8 | 22.9s | **20.7s** | 2.2s (9.6%) |
| 60 | 1 (serial) | 383.2s | -- | -- |
| 60 | 4 | 103.6s | **101.9s** | 1.7s (1.6%) |
| 60 | 12 | 80.8s | **78.8s** | 2.0s (2.5%) |

Run-to-run spread was 0.3-1.0s, so the effect clears the noise
everywhere -- and it is the *same* effect everywhere: a roughly constant
1.3-2.2s off pool startup. What changes is how much of the run that is:
9.6% of an 8-document run, 2.5% of a 60-document one, well under 1% of
the full corpus.

**So this does not make a bulk parse meaningfully faster, and was never
going to.** It helps the case where startup *is* the run: a handful of
documents.

## `[parser].document_timeout` -- what a safe value looks like

Not a performance knob so much as a knob whose value has to be *chosen
from* performance. Any threshold has to clear the slowest document you
legitimately have. In this corpus that is a 675-page book which takes
**246s** on its own, so a value that is safe here may not be safe on a
corpus with a longer document. Measure before setting it.

`[parser].stall_timeout`'s 1800s default is 7x that figure, chosen loose
on purpose: it is meant to catch a run that will never finish, not to
police a slow one.

## `[heavy].docling_images` -- disk, and a full re-parse

Two costs, both worth knowing before turning it on:

- **It invalidates the whole docling cache**, so the next run re-parses
  every PDF from scratch. That re-parse is the point rather than a bug --
  the existing `.md` files genuinely have no figure references in them.
- **The PNGs are real disk**: a 17-page paper produced 13 of them.
  `docling_image_scale = 2.0` is roughly 144 DPI, enough to read a figure
  back without storing print-resolution files.

## Where it all ended up

Measured end to end on 2026-08-04, rather than extrapolated:

| Change | Kind | Full 501-PDF corpus |
|---|---|---|
| Baseline: serial, OCR on | -- | **1h 56m** |
| OCR off | not parallelism | 55m 30s |
| 12 workers, 4 GPUs (today's cap) | CPU + GPU | 5m 10s |
| 32 workers, 4 GPUs (needs the clamp raised) | CPU | **3m 43s** |

**22x with the shipped defaults, 31x with the clamp raised.** Earlier
editions of this table read `~1.6 h -> ~39 min -> 8.8 min -> 5m26s`; the
first two were extrapolations that ran low, so the improvement was
understated.

**22x with the shipped defaults, and the GPU work is the smallest
contribution.** The largest is a boolean.
[PARALLELISM.md](PARALLELISM.md) describes the machinery that produces
these numbers; `bench/RESULTS.md` carries the measurements themselves,
including the conclusions later ones overturned.

## Output is not bit-reproducible under heavy concurrency

Comparing a one-GPU and a four-GPU run over all 501 documents: **6 files
differ**, by 0 to 59 bytes out of ~100 KB each (under 0.06%).

The differences are not device-dependent -- parsing the same document
explicitly on three different GPUs gives byte-identical output every
time, and repeating a run at the same worker count reproduces exactly.
What varies is docling's element grouping inside dense reference blocks
under heavy concurrency: the same words, split across list elements
differently. Nothing is lost, and retrieval tokenises on whitespace, so
BM25 ranking is unaffected.

**It cannot be turned off from docling** -- its `PdfPipelineOptions` has
no determinism, seed or reproducibility setting of any kind. The only
lever is below it, in torch, and that is deliberately not taken: it costs
throughput in exactly the models this pipeline lives in, and it *raises*
rather than degrades on an op with no deterministic implementation,
turning a cosmetic difference into a hard failure.

So do not expect `content/parsed/` to be byte-identical across runs at
high worker counts. At small ones it is: over 8 documents at 4 workers,
output is byte-identical between start methods and across repeats.

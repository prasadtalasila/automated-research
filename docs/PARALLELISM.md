# How the parser got 17x faster, and what it cost

A record of six releases of parallelism work on the Docling parse path,
written for someone who wasn't there. It is as much about the wrong turns
as the right ones, because four of the intermediate conclusions were
wrong and only the next measurement showed it.

The short version: a full parse of this project's 501-PDF bibliography
went from **~1.6 hours to 5m26s**. Almost none of that came from the
thing that looked like the bottleneck.

## Where it started

`python -m src.sync` turns each PDF named by `papers/bibliography.bib`
into `content/parsed/<citekey>.txt`. With `[parser].backend = "docling"`
that is the slowest thing this repository does.

The only figure on record was "docling is ~42x slower than pdftotext",
measured on 5 PDFs. That is enough to choose a backend and useless for
planning: it says nothing about how long a first sync of the whole corpus
takes, or what to fix if it's too slow.

So the first change was not an optimisation. It was a measurement.

## v0.11.0 -- measure first

`bench/` parses a rank-stratified sample of the real corpus and
extrapolates. The sample is drawn by page *rank* rather than at random,
so its page mix mirrors the corpus's -- which matters here because one
675-page document is 5% of all 13,400 pages, and a sample that misses it
understates everything.

The result, and the reason the rest of this document exists:

| | |
|---|---|
| 501 PDFs, 13,400 pages | **~1.6 hours** |
| GPU utilisation during it | **~7%** |
| CPU used | **3 of 48 cores** |
| GPU vs CPU-only | **1.79x** |

The machine has four A40s. One was nearly idle and three were never
addressed at all. The work was CPU-bound, and the obvious-looking fix --
"use the GPUs harder" -- was pointed at the wrong resource.

**Lesson, and the reason `bench/` is kept:** the ~42x figure was true and
useless. It described a ratio, not a bottleneck.

## v0.12.0 -- the biggest win was not parallelism

Two changes, neither of which runs anything concurrently.

**OCR off by default.** Docling ships with `do_ocr=True`, and its OCR runs
on the CPU. Turning it off was **2.46x** -- more than the GPU was worth.

That is a trade, not a free win, and the repo says so wherever the
setting appears: OCR only runs on *bitmap* regions, so what it recovers
is text stored as an image. Turning it off changed the extracted text of
**8 of 16** sampled documents. Mostly publisher furniture and figure
sub-captions -- but one document lost 10.1% of its characters, including
two complete tables embedded as images.

**One converter per run.** `DocumentConverter.initialized_pipelines` is an
*instance* attribute, and the code built a converter per PDF -- so every
document reloaded the layout, table and OCR models. Cold start: 16.5s,
against a corpus of 501 files.

Full corpus: ~1.6 hours to **~39 minutes**, with no concurrency anywhere.

## v1.0.0 -- CPU parallelism, defaulting to off

`[parser].workers`, **defaulting to `1`**. A default run takes a genuinely
serial path: no executor, no pickling, no subprocess. That is deliberate.
The ledger skips any PDF whose bytes haven't changed, so a routine sync
parses zero-to-few documents and pool setup would cost more than it
saves. This exists for the first-time and bulk runs.

Measured on 60 real PDFs: **444s serial, 123s at four workers (3.60x)**.

Three design points worth keeping:

**Worker count is clamped, not obeyed.** The resolved count is
`min(requested, host ceiling, documents needing a parse)`. The host
ceiling counts the CPUs *this process may run on*
(`len(os.sched_getaffinity(0))`), not the machine's -- on the development
host those read **48 and 96**, so sizing off `os.cpu_count()` would spawn
twice as many workers as there are CPUs to run them. It then divides by 4,
because one docling worker was measured holding ~300% CPU. A
four-core/eight-thread desktop therefore resolves to 2, and asking for 15
there still gets 2 -- clamped, and said out loud rather than silently
obeyed or silently ignored.

**Each backend gets the concurrency it can use.** Processes for `docling`
(in-process, holds the GIL); threads for `pdftotext` (an external
subprocess that releases it). `src/sync.py` had carried a comment arguing
against parallelising this loop at all; two of its three premises still
held and its conclusion didn't, so the comment was rewritten rather than
left contradicting the code.

**The parent keeps what only the parent can do.** Every ledger write stays
on the main process -- sqlite has a single writer -- and results are
reported in bibliography order regardless of which worker finished first,
so two identical runs still print identically.

## v1.1.0 -- the bottleneck moved, and the earlier reading was wrong

With the CPU no longer the constraint, 12 workers were no faster than 4.
`nvidia-smi dmon` showed GPU 0 pinned at 100% and GPUs 1-3 at 0%, because
Docling's `AcceleratorDevice.AUTO` resolves to `cuda:0` in **every**
process. So each worker now claims one device round-robin.

Full corpus at 12 workers: **528s on one A40, 326.2s across four (1.62x)**
-- 5m26s total.

Two corrections that measurement forced, both of which had been stated
confidently:

**"GPU 0 at 100% is why 12 workers buy nothing" was half the story.** GPU 0
*was* pinned -- but freeing it made no difference at all to the
60-document subset that observation came from (122.4s at 4 workers,
123.0s at 12, with all four GPUs busy and the CPU ~85% idle). At that size
per-worker startup dominates before contention does. **The multi-GPU gain
only exists at corpus scale**, which is why the headline is measured over
all 501 documents.

**"Byte-identical to serial" did not generalise.** True over 8 documents at
4 workers, where it was measured. Over 501 at 12, six files differ by
under 0.06% -- Docling grouping dense reference blocks into elements
differently under load. Not device-dependent: the same document on
`cuda:0`, `cuda:1` and `cuda:2` is byte-identical every time.

Mechanically: the device index comes from a shared counter under a lock,
because a `ProcessPoolExecutor` neither numbers its workers nor
guarantees it starts all of them. The pool uses `spawn`, because counting
GPUs initialises CUDA in the parent and a forked child inherits a broken
CUDA context from such a parent.

## v1.1.1 -- answering the question that was left open

Non-reproducibility invites one question: can it be switched off? It
cannot. Docling 2.117.0 exposes no determinism, seed or reproducibility
setting anywhere in `PdfPipelineOptions`. The only lever is torch's
`use_deterministic_algorithms`, deliberately not taken: it costs
throughput in exactly the models this pipeline lives in, and it *raises*
rather than degrades on an op with no deterministic implementation --
turning a cosmetic difference into a hard failure.

## v1.2.0 -- what parallelism broke, found by a user

Three defects, two of them introduced by the work above and both found by
someone running a real 501-document sync rather than by a test.

**Ctrl+C took minutes to exit.** `except KeyboardInterrupt` around
`as_completed()` never fires -- reproduced in a 20-line script with no
docling in it. SIGINT reaches the parent, the result loop stops
consuming, the handler never runs, and the process then sits while
in-flight workers finish. An explicit `signal.signal(SIGINT, ...)`
handler does work: **1.0s to exit, against 60s+ and counting**. It
terminates the pool's workers -- with a grace period and then `kill()`,
because a worker inside onnxruntime or torch native code does not honour
SIGTERM promptly (21 processes survived `terminate()` alone) -- and calls
`os._exit`, deliberately skipping the atexit hook that *joins* workers.
That is safe here only because the ledger commits incrementally: whatever
finished is already on disk.

**A parallel run reported nothing until it was completely finished.** Over
501 documents that is half an hour of silence, indistinguishable from
being stuck -- especially under Docling's own OCR chatter. Progress now
prints per completion, on stderr, so stdout stays deterministic.

**A failed parse was never retried.** `needs_parse` was true only for a
new or byte-changed document, so `parse_failed` persisted until the PDF
itself changed. Harmless while failures were per-document and permanent;
not harmless once one dead worker marks *every* in-flight document
failed. Unattended, that silently drops those documents from the corpus
for good.

Also fixed alongside: `convert(raises_on_error=True)` raises only on
`FAILURE`, so a `PARTIAL_SUCCESS` returned truncated text that was
written and marked parsed -- a silently incomplete source, on a pipeline
whose entire purpose is grounding claims in sources.

## v2.1.0 -- taking apart the ten seconds

Every measurement above that came out flat blamed the same thing:
"per-worker startup dominates". Nobody had measured what that startup
*was*. It turns out to be:

| | |
|---|---|
| `import torch` | 1.16s |
| `import docling` | 2.08s |
| build `DocumentConverter` | 0.13s |
| first `convert()` -- Docling loads its models | 5.17s |
| **before the first parsed page** | **8.5s** |

**Half the ceiling was gone before starting.** The 5s model load happens
inside the converter instance, in whichever process owns it, and no
multiprocessing start method shares that. Only the 3.2s of imports was
ever available.

So: forkserver, with torch and docling in its preload list, so the
imports happen once in a server process that every worker is forked
from. `[parser].start_method`, defaulting to `"auto"`.

**Then the shared import turned out to be worth nothing.** Head to head
on the real `sync`, 8 documents at 4 workers: spawn 22.9s, forkserver
22.4s. Workers import *concurrently* -- on a host with spare CPUs that
cost was already overlapped, so moving it out of N children and into one
parent nets zero. What is not overlapped is the preload, which blocks
pool construction.

The fix is ordering, not machinery: start the forkserver *before*
reading the bibliography, so its imports run during the ~2.5s that takes.
`ensure_running()` returns in 0.02s without waiting for them. Four live
workers ready at **4.40s instead of 6.90s**.

End to end on the real `sync`, medians of three:

| Documents | Workers | spawn | forkserver |
|---|---|---|---|
| 8 | 4 | 23.1s | **21.8s** |
| 8 | 8 | 22.9s | **20.7s** |
| 60 | 4 | 103.6s | **101.9s** |
| 60 | 12 | 80.8s | **78.8s** |

**Say what this is worth, not what it sounds like.** It is the same
~1.3-2.2s off pool startup at every point -- 9.6% of the smallest run,
2.5% of the largest, and it would be under 1% of the full 501-PDF corpus.
This release does not make a bulk parse faster. It helps the case every
earlier measurement kept tripping over: a handful of documents, where
startup *is* the run.

### Two things believed for three releases, both wrong

**"Counting GPUs initialises CUDA in the parent."** This was the stated
reason `sync` used `spawn`, written into the code as fact. Against torch
2.7.1 it is not what happens: `torch.cuda.device_count()` goes through
NVML, `torch.cuda.is_initialized()` stays `False`, and a child forked
from that parent uses CUDA without complaint. `gpu_count()` now reads
`nvidia-smi --list-gpus` regardless -- not to fix a bug, but so that a
safety property stops depending on an implementation detail of one torch
version, and so the parent stops importing 200MB of torch to answer a
question the driver's own CLI answers.

**"fork would be safe once that moved."** Also wrong, for a reason nobody
had raised: by the time the pool is built this process holds two live
sqlite connections -- the run lock, deliberately parked in an
uncommitted `BEGIN IMMEDIATE`, and the ledger. SQLite says not to carry
an open connection across `fork()`. forkserver's server is a fresh
interpreter launched with `spawnv_passfds`, so it inherits neither. And
fork measured no faster than forkserver anyway, so ruling it out cost
nothing.

## Where the time actually went

| Change | Kind | Full corpus |
|---|---|---|
| baseline | -- | ~1.6 h |
| OCR off + converter reuse | not parallelism | ~39 min |
| 12 CPU workers | CPU | ~8.8 min |
| four GPUs | GPU | **5m26s** |

The GPU work -- the thing that looked like the answer at the start -- is
the smallest contribution. The largest is a boolean.

## What to take from this

1. **Measure the bottleneck, not the ratio.** "42x slower" pointed at
   nothing actionable. "7% GPU, 3 of 48 cores" pointed straight at it.
2. **Every conclusion here was provisional.** The GPUs were redundant
   until parallelism made them binding. The 12-worker plateau was GPU
   contention, until a bigger sample showed it was startup cost.
3. **Sample size decides which effect you see.** 8 documents, 16, 60 and
   501 gave four different answers, and only the largest was the one
   users experience.
4. **Parallelism's cost lands in operability, not correctness.** The
   parse output stayed right. What broke was Ctrl+C, progress reporting,
   and failure recovery -- none of which a unit test was watching, and
   all of which a user hit within one run.

The harness that produced every number here is in `bench/`; the raw
per-PDF timings are in `bench/results/`, and `bench/RESULTS.md` is the
long-form measurement record.

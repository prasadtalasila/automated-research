# How the parser got 22x faster, and what it cost

A record of eight releases of parallelism work on the Docling parse
path, written for someone who wasn't there. It is as much about the wrong
turns as the right ones, because **six** of the intermediate conclusions
were wrong and only the next measurement showed it -- including, in the
end, the baseline every other number was divided by.

Every figure here is one machine's -- read them as ratios, not as times
to plan against. [PERFORMANCE.md](PERFORMANCE.md) is the
lookup-oriented version of the same measurements, organised by config
setting; this is the narrative.

The short version: a full parse of this project's 501-PDF bibliography
went from **1h 56m to 5m 10s** -- **22x**, both ends measured rather than
extrapolated. Almost none of that came from the thing that looked like
the bottleneck.

(Earlier editions said "17x, ~1.6 hours to 5m26s". The shape was right;
the baseline was an extrapolation that ran low, so the improvement was
*understated*. Raising the worker clamp takes it to 31x -- see v2.1.1.)

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
| 501 PDFs, 13,400 pages | **~1.6 hours** (extrapolated; measured later at 1h 56m) |
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
(Both figures are extrapolations from a 16-PDF sample. Measured directly
in v2.1.1: 1h 56m to 55m 30s. The 2.46x ratio held up serially -- 2.08x
measured -- but not in parallel, where OCR costs up to 4.79x.)

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

## v2.1.1 -- the baseline was wrong, and so was everything divided by it

Seven releases of measurement, and the number underneath all of it had
never been measured. Every document quoted a serial baseline extrapolated
from a 16-PDF sample at 0.176 s/page: `0.176 x 13,400 = ~39 min`.

Run for real over all 501 PDFs, a serial pass takes **55m 30s** -- the
extrapolation was **41% low**. `bench/estimate.py` had reported a second,
per-doc model all along, which was only 9% low; the documentation had
consistently quoted the wrong one of the two.

**Correcting the denominator reversed a headline conclusion.** Efficiency
at 12 workers was reported as 60%, "roughly 40% of the machine unused".
It is **89%**. There was no mystery to explain; there was a bad divisor.

And with a trustworthy baseline, the scaling curve says something the old
one could not:

| Workers | Wall clock | Speedup | Efficiency |
|---|---|---|---|
| 1 | 3330.4s | 1.00x | -- |
| 8 | 428.6s | 7.77x | 97% |
| 12 | 310.2s | 10.74x | 89% |
| **32** | **220.7s** | **15.09x** | 47% |
| 48 | 226.3s | 14.72x | 31% |

`worker_ceiling()` caps at `allowed_cpus // 4`, which is 12 here. **The
optimum is near 32, and the cap costs 1.41x.** The constant it rests on
-- one docling worker "uses about 4 CPUs" -- came from a single ~300% CPU
observation. Measured at 32 workers, the CPU is 71% busy; docling's own
`num_threads` changes the run by 1.9%, which is noise. A worker does not
use four CPUs.

That constant has **not** been changed. It alters what the pipeline does
on every machine, and this release is documentation and benchmarking
only.

**The lesson, and it is the same one as every other section here, applied
to ourselves:** an extrapolation quoted often enough starts reading as a
measurement. The tool said "per-doc is the more honest model" in its own
docstring, and the docs quoted per-page anyway, for two releases.

## Where the time actually went

Measured end to end (2026-08-04), rather than extrapolated:

| Change | Kind | Full corpus |
|---|---|---|
| baseline, serial + OCR | -- | **1h 56m** |
| OCR off | not parallelism | 55m 30s |
| 12 workers, 4 GPUs | CPU + GPU | 5m 10s |
| 32 workers, 4 GPUs (needs the clamp raised) | CPU | **3m 41s** |

Earlier editions of this table read `~1.6 h -> ~39 min -> 8.8 min ->
5m26s`. The shape was right and the first two figures were extrapolations
that ran low; the 5m26s measurement stands, at 5m10s on a rebuilt venv.

The GPU work -- the thing that looked like the answer at the start -- is
the smallest contribution but one. The largest is a boolean. And the last
release bought nothing measurable here at all, which was the honest
result rather than a disappointment: it targets small runs, where a fixed
1.3-2.2s of pool startup is most of the wall clock.

## What to take from this

1. **Measure the bottleneck, not the ratio.** "42x slower" pointed at
   nothing actionable. "7% GPU, 3 of 48 cores" pointed straight at it.
2. **Every conclusion here was provisional.** The GPUs were redundant
   until parallelism made them binding. The 12-worker plateau was GPU
   contention, until a bigger sample showed it was startup cost.
3. **Sample size decides which effect you see.** 8 documents, 16, 60 and
   501 gave four different answers, and no one of them is *the* answer --
   a bulk first sync and a three-paper top-up are different workloads,
   and the last release exists for the second one.
4. **A comment stating a reason is a claim, and claims rot.** "Counting
   GPUs initialises CUDA in the parent" sat in the code as fact for three
   releases and was never true of the torch version in use. Nothing
   failed, because the conclusion it justified happened to be right for
   an unrelated reason.
5. **Parallelism's cost lands in operability, not correctness.** The
   parse output stayed right. What broke was Ctrl+C, progress reporting,
   and failure recovery -- none of which a unit test was watching, and
   all of which a user hit within one run.

The harness that produced most of these numbers is in `bench/`; the raw
per-PDF timings are in `bench/results/`, and `bench/RESULTS.md` is the
long-form measurement record. The pool-level A/Bs -- worker counts, GPU
assignment, start method -- were measured with the real
`python -m src.sync` rather than that harness; `bench/README.md` has the
recipe.

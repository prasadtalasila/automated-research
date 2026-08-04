# Parallelism: design and roadmap

How the PDF parse path runs work in parallel, what each component is for,
and what is planned next.

This is a **design document, not a history.** It describes the code as it
is. For what any of it *costs*, see [PERFORMANCE.md](PERFORMANCE.md); for
how a setting is spelled, [CONFIG.md](CONFIG.md); for the measurements
themselves — and the conclusions later ones overturned — `bench/RESULTS.md`
and `git log`.

## Table of contents

- [Two words for two different things](#two-words-for-two-different-things)
- [Where parallelism lives](#where-parallelism-lives)
- [The parse path, end to end](#the-parse-path-end-to-end)
- [Components](#components)
- [Worker lifecycle](#worker-lifecycle)
- [How the worker count is decided](#how-the-worker-count-is-decided)
- [Failure and interruption](#failure-and-interruption)
- [Concurrency control: one writer at a time](#concurrency-control-one-writer-at-a-time)
- [What is deliberately serial](#what-is-deliberately-serial)
- [Roadmap](#roadmap)

## Two words for two different things

This repository uses **parallelism** and **concurrency control** for
different mechanisms, and keeps them apart on purpose.

| Term | What it means here | Where it lives |
|---|---|---|
| **Parallelism** | Several documents parsed at the same instant across several CPUs and GPUs, to cut the wall clock of **one** run | `src/sync.py`'s worker pool, `src/pdf_text.py` |
| **Concurrency control** | Stopping two **separate** runs from corrupting `content/` when they overlap | `src/runlock.py` |

Unrelated problems, unrelated solutions. Parallelism is an opt-in speed
feature, off by default; concurrency control is always on and exists
purely for safety. A reader who conflates them goes looking for the run
lock inside the worker pool and finds nothing.

Where no distinction is needed, "concurrent" is used loosely for "more
than one thing in flight" — matching `concurrent.futures`, the stdlib
module all of this is built on.

## Where parallelism lives

Only the PDF parse is parallel. Everything else is fast enough to be
serial, and is.

```
  bib file ──► ledger ──► PARSE ──► retrieval ──► drafting ──► render
                            ▲
                            └── the only parallel stage
```

Two entry points reach it, sharing the same machinery:

```
  python -m src.sync                     scripts/full_pipeline.py
  (job 1: bib ──► parsed text)           (job 2: heavy, opt-in)
          │                                        │
          │ src/sync.py                            │ src/heavy/docling_parse.py
          │ _parse_parallel()                      │ parse_corpus()
          │ _executor_for()                        │ _executor_for()
          └────────────────┬───────────────────────┘
                           ▼
                   src/pdf_text.py
        resolve_workers · worker_ceiling · docling_threads
        process_pool_context · prestart_pool · init_worker · gpu_count
```

`src/heavy/docling_parse.py` keeps its own `_executor_for` rather than
importing `sync`'s, so `src/heavy/` never depends on the core entry
point — the dependency runs the other way everywhere else. Both delegate
every *policy* decision to `pdf_text`, so "how many workers, which start
method, which GPU" is answered in exactly one place.

## The parse path, end to end

```
                     ┌────────────────────────────────────────────┐
  MAIN PROCESS       │ 1. prestart_pool()                         │
  holds:             │    forkserver begins importing torch       │
   · the run lock    │    + docling in the background             │
   · the ledger      │ 2. read bibliography (~2.5s) ◄── overlaps  │
     (sqlite, both   │ 3. ledger: which documents need a parse?   │
      single-writer) │ 4. resolve_workers(n_docs)                 │
                     └─────────────────────┬──────────────────────┘
                                           ▼
                     ┌────────────────────────────────────────────┐
                     │ submit biggest-file-first (LPT)            │
                     │ one 675-page book picked up last would set │
                     │ the wall clock by itself                   │
                     └─────────────────────┬──────────────────────┘
                                           ▼
   ┌──────────────────── ProcessPoolExecutor ───────────────────────┐
   │ mp_context   = forkserver (or spawn)                           │
   │ initializer  = init_worker(counter, lock, n_gpus)              │
   │                                                                │
   │  ┌──────────┐  ┌──────────┐  ┌──────────┐    ┌──────────┐      │
   │  │ worker 0 │  │ worker 1 │  │ worker 2 │ …  │ worker N │      │
   │  │ cuda:0   │  │ cuda:1   │  │ cuda:2   │    │cuda:N%G  │      │
   │  │converter │  │converter │  │converter │    │converter │      │
   │  │built once│  │built once│  │built once│    │built once│      │
   │  └────┬─────┘  └────┬─────┘  └────┬─────┘    └────┬─────┘      │
   └───────┼─────────────┼─────────────┼───────────────┼────────────┘
           └─────────────┴──────┬──────┴───────────────┘
                                │ (citekey, out_path | exception)
                                ▼
                     ┌────────────────────────────────────────────┐
  MAIN PROCESS ONLY  │ _as_they_land()  ── stall watchdog         │
                     │ ledger.mark_parsed / mark_parse_failed     │
                     │ results replayed in BIB ORDER, not         │
                     │ completion order                           │
                     └────────────────────────────────────────────┘
```

Only the extraction crosses the process boundary. Everything touching
shared state stays on the main process: **sqlite has a single writer**,
and replaying results in bibliography order is what makes two identical
runs print identically.

## Components

All in `src/pdf_text.py` unless noted.

### `resolve_workers(n_docs) -> (workers, complaint)`

```
   what you asked for ──┐
   what the machine     ├──► min(…) ──► max(1, …) ──► workers
     can sustain      ──┤
   how many documents ──┘
     actually need it
```

The third ceiling matters more than it looks: standing up 12 workers to
parse 3 documents pays 12 model loads to save two documents' work.

An over-large request is **clamped and said out loud on stderr** — never
silently obeyed (which thrashes), never silently ignored (which leaves
someone believing they configured something they didn't).

### `worker_ceiling()`

The machine ceiling alone: `allowed_cpus() // _CPUS_PER_DOCLING_WORKER`
for docling, `allowed_cpus()` for `pdftotext`. Separate from
`resolve_workers` because it is the one ceiling independent of the
document count, so `prestart_pool` can consult it before the bibliography
has been read.

`allowed_cpus()` counts the CPUs **this process may run on**
(`os.sched_getaffinity`), not the machine's. On a container the two
differ, and sizing off the machine's total oversubscribes.

> **The divisor of 4 is measurably too conservative** — 32 workers beat
> the 12 it permits by ~1.4x. Not yet changed; see [Roadmap](#roadmap).

### `docling_threads(workers)`

Divides docling's own `num_threads` down so `workers × threads` still
fits the machine. Capped at docling's default of 4, so a single-worker
run gets exactly what docling would have picked on its own.

Measured to matter far less than it looks: forcing 1/2/4/8 at 12 workers
moves a full-corpus run by 1.9%. Kept because dividing down is still the
correct thing to do when the product would exceed the machine, not
because it buys throughput.

### `process_pool_context()`

Chooses the start method and configures it:

```
   auto ──► forkserver, if the platform has it and CUDA is untouched
        └─► spawn otherwise (Windows, or CUDA already initialised)
```

**Never plain `fork`.** By the time the pool is built, this process holds
the run lock and the ledger open as live sqlite connections, and SQLite's
own documentation says not to carry an open connection across `fork()`.
It also measured no faster than `forkserver`.

### `prestart_pool()`

Starts the forkserver *before* the caller reads the bibliography, so its
torch/docling import overlaps work that has to happen anyway.

```
   without:  ├─ read bib 2.5s ─┤├─ preload 3.4s ─┤├─ pool ready
   with:     ├─ read bib 2.5s ─┤├─ pool ready
             ├─ preload 3.4s (background) ──┤
```

Declines when no pool is coming: not docling, `workers = 1`, or a machine
whose ceiling is 1 regardless of what was asked for.

### `init_worker(counter, lock, n_gpus)`

Pool initialiser. Each worker claims one CUDA device round-robin:

```
   shared counter ──(under lock)──► i ──► cuda:(i % n_gpus)
```

From a shared counter rather than a PID or position, because a pool
creates workers lazily and numbers none of them. Without this, docling's
`AcceleratorDevice.AUTO` resolves to `cuda:0` in *every* process and every
worker piles onto one card.

### `gpu_count()`

Reads `nvidia-smi --list-gpus`, applying `CUDA_VISIBLE_DEVICES` by hand
since nvidia-smi ignores it and torch does not. Falls back to torch only
where the driver's CLI is absent — the point is to answer the question
without importing torch into the parent.

### `_as_they_land()` — `src/sync.py`

Yields futures as they complete, abandoning the run if the **whole pool**
goes silent for `[parser].stall_timeout`.

Deliberately not a per-document deadline: with several workers,
completions arrive constantly, so silence across the entire pool
distinguishes a hung worker from a merely slow document far better than
any per-document number could — which matters when the slowest legitimate
document takes 246s. A warning fires at half the budget first.

## Worker lifecycle

What a cold worker pays before producing anything:

```
  forkserver:  fork ──► imports inherited ──► build converter ──► 1st convert
               ~0s          ~0s                    0.13s            5.17s
                                                              └─ models load here

  spawn:       exec ──► import torch+docling ──► build converter ──► 1st convert
               ~0.3s        3.24s                    0.13s            5.17s
```

The converter is **built once per worker and reused** across that
worker's whole shard: `DocumentConverter.initialized_pipelines` is an
*instance* attribute, so one converter per document reloads every model
per document.

The ~5s model load is per process and shareable by no start method, which
is why `forkserver` is worth a fixed 1–2s rather than a multiple.

## How the worker count is decided

```
  [parser].workers = 1       ──► strictly serial: no pool, no subprocess,
                                  no pickling. The default.
  [parser].workers = <int>   ──┐
  [parser].workers = "auto"  ──┴─► min(requested, worker_ceiling(), n_docs)
```

`1` is not "a pool of one" — it is a different code path. A routine sync
re-parses zero-to-few documents, since the ledger skips anything whose
bytes have not changed, so pool setup would cost more than it saves.
Parallelism is for first-time and bulk runs.

Each backend gets the concurrency it can use:

| Backend | Executor | Why |
|---|---|---|
| `docling` | `ProcessPoolExecutor` | in-process, holds the GIL |
| `pdftotext` | `ThreadPoolExecutor` | external subprocess, releases the GIL |

## Failure and interruption

| Event | Behaviour |
|---|---|
| One document fails | Reported, marked `parse_failed`, **retried next run**. The batch continues |
| A worker dies (OOM killer) | `BrokenProcessPool` is handled: that document fails, the run does not |
| The pool goes silent | Watchdog warns at half `stall_timeout`, then abandons the outstanding documents as failures |
| Ctrl+C | `interrupt_guard` terminates workers (SIGTERM, grace period, then kill) and `os._exit`s |

Ctrl+C needs an explicit SIGINT handler because `except KeyboardInterrupt`
around the result loop **does not work**: the loop stops consuming, the
handler never runs, and the process sits until in-flight workers finish —
minutes per document with docling.

Skipping interpreter shutdown is safe because the ledger commits
incrementally and synchronously: whatever finished is already on disk.

## Concurrency control: one writer at a time

A separate mechanism for a separate problem — two *runs* overlapping, not
two documents.

```
  run A ──► content/pipeline.lock.db ──► BEGIN IMMEDIATE ──► holds it
  run B ──► SQLITE_BUSY ──► exit 2, naming the holder's pid, host and age
  readers (citation_gate, retrieval, ledger) ──► unaffected throughout
```

A dedicated sqlite file rather than the ledger itself, so holding the
lock does not force the ledger's five commit points into one transaction.
`BEGIN IMMEDIATE` takes a RESERVED lock, which does not block readers, and
after `kill -9` it is released immediately — staleness handles itself,
with no PID liveness check and no platform-specific code.

Full conflict policy in [DESIGN.md](DESIGN.md).

## What is deliberately serial

- **Ledger writes** — sqlite has a single writer.
- **Result application** — replayed in bibliography order, so output is
  reproducible run to run.
- **The default** — `workers = 1` until someone opts in.
- **Everything outside the parse** — retrieval, gating and rendering are
  fast enough that concurrency would add risk for no measurable gain.

## Roadmap

Ordered by measured benefit over risk. Figures in
[PERFORMANCE.md](PERFORMANCE.md).

### 1. Stop hard-coding `_CPUS_PER_DOCLING_WORKER = 4`

**The largest known win: ~1.4x.** The constant models a docling worker as
occupying 4 CPUs; measured, it occupies closer to one. 32 workers beat
the 12 the constant permits, and docling's `num_threads` is worth 1.9%.

The target is a *region*, not a point: 32 and 48 workers land within 0.9%
of each other over three runs each, so the fix is "a much smaller
divisor", not a specific replacement number.

Blocked on generality rather than effort: validated on one machine and
one corpus, and a CPU-only machine — where the GPU does none of the work —
would likely want a different value. Wants a per-backend measured default,
or a short calibration run.

### 2. Selective OCR

OCR costs 2.08x serially and up to 4.79x in parallel, to recover content
in a minority of documents: of 16 sampled, 8 changed and ~2 materially.
Detecting bitmap-heavy pages cheaply and running OCR only there converts
a global tax into a per-document one.

### 3. Cache the model load across runs

~5s per worker per run, shareable by no start method. Irrelevant to a
bulk parse, dominant for a three-document top-up. Needs a resident pool,
which is a large change to a pipeline whose appeal is being a batch job.

### 4. Batch inference across documents

Each worker uses ~7% of a GPU. Batching would use the cards properly, but
docling exposes no batch API — upstream work, not local.

### Not planned

- **Intra-document splitting.** The 675-page outlier looks like a
  critical-path problem and is not at this corpus size: its floor binds
  only beyond ~35x parallelism, and LPT scheduling already handles it.
- **Threads for docling.** It holds the GIL.
- **Bit-reproducible output under load.** docling exposes no determinism
  setting, and torch's *raises* rather than degrades on ops with no
  deterministic implementation.

### Open questions

Gaps, not tasks.

- **Does the clamp finding generalise** past one machine and one corpus?
  A CPU-only machine, where the GPU does none of the work, is the case
  most likely to differ — and the one most easily hurt by getting it
  wrong.
- **Where is the OCR optimum?** Swept only to 24 workers, still improving
  there.

Answered by measurement rather than left open: the curve past ~32 workers
**plateaus rather than reversing** (32 and 48 land within 0.9% over three
runs each), and what flattens it is per-worker startup growing into 12.7%
of the run at 48 workers while the CPU climbs from 56% to 78% busy.
Neither the GPUs, `num_threads`, nor the long-document tail is involved.

# Design review for chitragupta

## Repository constraints and operating model

The repository is designed around a few hard constraints that strongly shape the architecture:

1. **Never fabricate a citekey**
   - Citekeys must come from `papers/bibliography.bib` and be synchronized into `content/ledger.sqlite`.
   - Drafts must pass `python -m src.citation_gate` before being considered valid.
   - This is the repo's primary safety invariant.

2. **Three layers**
   - Corpus layer: deterministic content maintenance (`python -m src.sync`).
   - Drafting layer: generative drafting via the Claude Code skills.
   - Enrichment layer: optional heavier processing (`scripts/full_pipeline.py`).
   - Ad-hoc review tools remain outside the automatic chain.

3. **Config and host variability are first-class concerns**
   - `config.toml` is the single source of configuration.
   - Stages must probe for binaries/services rather than assume availability.
   - Missing dependencies should degrade to honest reporting, not crashes or silent success.

4. **Incremental processing is a load-bearing design goal**
   - `ledger.py` uses stat-before-hash skipping.
   - `retrieval.py` caches term-frequency stats.
   - `embed_index.py` and `topic_model.py` cache embeddings.
   - `docling_parse.py` fingerprints each PDF by (size, mtime).
   - The intent is to avoid reprocessing unchanged material.

5. **Parallelism is opt-in, and bounded by the host rather than by the
   request**
   - `[parser].workers` defaults to `1`, so the default run is strictly
     serial -- no pool, no subprocesses. Incremental skipping means a
     routine run has almost nothing to do, and pool setup would cost more
     than it saves.
   - The resolved count is `min(requested, host ceiling, work available)`.
     The host ceiling counts CPUs *this process* may run on, not the
     machine's, and divides by the CPUs one worker actually occupies.
   - Only the parse call is dispatched. Every ledger and cache write
     stays on the parent process, because sqlite has a single writer and
     because the parent is the only place that can order results
     deterministically.
   - Backends get the kind of parallelism they can use: threads for
     `pdftotext` (external subprocess, releases the GIL), processes for
     `docling` (in-process, holds it), with one CUDA device per worker.

## Design pattern review

### 1. Pipeline architecture
The project is fundamentally a staged pipeline:
- BibTeX export -> ledger -> parsed text -> retrieval
- Optional enrichment layer: Docling -> embeddings -> BERTopic -> render

This is the dominant structural pattern and fits the use case well.

### 2. Separation of concerns
Modules are mostly narrowly scoped:
- `bib_reader.py` parses bibliographic input
- `ledger.py` persists state and change detection
- `pdf_text.py` handles plain-text extraction
- `retrieval.py` ranks documents
- `citation_gate.py` validates citations
- `render_output.py` renders drafts

This is a strong point of the codebase.

### 3. Adapter/facade pattern around external tools
Several modules wrap command-line tools or services behind Python APIs:
- `pdftotext`
- Docling
- Pandoc/TeX Live

This keeps the rest of the system insulated from tool-specific details.

### 4. Strategy-like backend substitution
The repo already supports alternate approaches for retrieval and parsing:
- BM25 retrieval in the corpus layer
- embedding retrieval in the enrichment layer
- plain extraction vs structured extraction

That is a good fit for a tiered research workflow.

### 5. Guard pattern
`citation_gate.py` is a hard validation gate that protects downstream stages. This is appropriate for a citation-grounded writing system.

### 6. Repository-style persistence layer
`ledger.py` functions as a small persistence/repository layer over SQLite. It keeps the rest of the code from directly dealing with SQL details.

## Concurrency and conflict policy

The rule the rest of this section implements. Every change to how runs
interact, fail, or refuse each other should be checked against it, in
this order of precedence:

1. **Corpus integrity outranks availability.** When in doubt, fail
   visibly and record nothing. A visible failure costs a re-run; a silent
   wrong entry costs a false citation, which is the failure this whole
   repository exists to prevent.
2. **The ledger is the sole authority, and every state in it must be
   recoverable by re-running.** No failure may require hand-editing the
   ledger, and none may become permanently invisible: failures caused by
   the *run* are retried automatically; failures caused by the *document*
   are not re-parsed, but are reported with a nonzero exit on every run
   until resolved.
3. **Every abnormal exit releases the lock, kills its children, and
   leaves the ledger stating what did not complete.** A run may be
   refused, fail, or succeed -- never end ambiguously.
4. **Because writers are serialised, no serial section may be
   unbounded.** Anything holding the lock must be observably making
   progress or be subject to a watchdog, and anything that kills work
   must warn before it acts.
5. **Exit codes are the API for unattended callers**: `0` corpus in sync;
   `1` corpus not in sync, human attention needed; `2` cycle skipped, no
   work lost. Exit 2 must not be able to persist indefinitely without
   escalation.
6. **The serial, default-configuration path does not change behaviour in
   a minor release.** Opt-in features may carry protective sub-defaults;
   anything else that changes what an existing invocation does is a major
   release.

Two worked examples of the precedence mattering. A document Docling only
half-parsed is discarded rather than stored (1 over availability). A
corrupt PDF is *not* retried, yet still fails the run every time (2:
never silent, but also never pointlessly expensive).

## Parallelism and resource design

The parse path is the only part of this repository that runs work in
**parallel** -- several documents at once, to cut the wall clock of a
single run. That is a different mechanism from the **concurrency
control** below, which stops two separate runs colliding; PARALLELISM.md
defines both and describes the components. The design rules the parse
path settled on are worth stating here.

### Opt-in, and clamped rather than obeyed

`[parser].workers` defaults to `1`, which takes a genuinely serial path
-- no executor, no pickling, no subprocess. Incremental skipping means a
routine run has almost nothing to do, so pool setup would usually cost
more than it saves.

The resolved count is `min(requested, host ceiling, work available)`,
never below 1. Two parts of that are easy to get wrong:

- **The host ceiling counts `len(os.sched_getaffinity(0))`, not
  `os.cpu_count()`.** On a shared or containerised host these differ --
  96 and 48 on the development machine -- and sizing off the larger
  number spawns workers that only deschedule each other. `sched_getaffinity`
  is Linux-only, so there is a guarded fallback for the Windows CI leg.
  Neither sees a cgroup CPU *quota*, which throttles without narrowing
  the affinity mask; an explicit worker count is the answer there.
- **A worker is not one CPU.** One Docling worker was measured holding
  ~300% CPU, so the ceiling divides by 4 -- a divisor a later
  full-corpus sweep found too conservative by roughly 2.5x (see
  docs/PERFORMANCE.md). Docling's own thread count is
  then divided down to match, keeping workers x threads inside the host.

An over-large request is clamped *and reported*. Silently obeying
thrashes; silently ignoring leaves someone believing they configured
something they did not.

### Each backend gets the concurrency it can use

Processes for `docling`, which runs in-process and holds the GIL; threads
for `pdftotext`, an external subprocess that releases it. A process pool
around `pdftotext` would add pickling and spawn cost to buy the same
OS-level concurrency; threads around `docling` would serialise exactly
the work being overlapped.

The Docling pool uses the `spawn` start method, because counting GPUs
initialises CUDA in the parent and a forked child inherits a broken CUDA
context from such a parent. The cost -- each worker re-imports torch and
docling -- is why parallelism buys nothing on a small corpus and a great
deal on a large one.

### The parent keeps what only the parent can do

Every ledger and cache write stays on the parent process: sqlite has a
single writer, and the parent is the only place that can order results
deterministically. Workers receive `(path, citekey, threads)` and return
`(citekey, out_path, exception)` -- the exception is *returned* rather
than raised so that both the value and its type survive pickling, since
`sync` reports `ExtractionError` and `BackendUnavailable` differently.

Work is submitted longest-file-first. One 675-page document in this
corpus is 5% of all its pages; picked up last it would define the wall
clock by itself. File size rather than page count, because counting pages
needs a PDF library the corpus layer deliberately does not depend on.

### Device assignment

Docling's `AcceleratorDevice.AUTO` resolves to `cuda:0` in *every*
process, so N workers contend for one card while the rest idle. Each
worker claims a device round-robin from a shared counter handed out under
a lock in the pool initialiser -- not from a PID or a worker index,
because a `ProcessPoolExecutor` neither numbers its workers nor
guarantees it starts all of them.

### Failure and interruption are part of the design

Five distinct failure modes, each handled where it can be:

- **A dead worker.** `BrokenProcessPool` is caught and the unfinished
  documents are reported as failures rather than the run being aborted.
  Results are collected with `as_completed`, not `map`, so a pool that
  dies while the largest document is still running keeps the smaller ones
  that already finished.
- **A hung pool.** A stall watchdog gives up when *no* document completes
  for `[parser].stall_timeout`. Deliberately not a per-document deadline:
  no single threshold separates a hung worker from the legitimate 246s
  document, but with several workers, total silence does. It uses
  `wait(FIRST_COMPLETED)` rather than `as_completed(timeout=...)`, whose
  timeout is measured from the original call and would fire on a healthy
  long run. On firing it terminates the workers, since abandoning them
  leaves in-flight jobs writing files for documents already reported
  failed.
- **A slow document.** `[parser].document_timeout` is honoured by each
  backend's own mechanism, and they are not equally strong: a real kill
  for `pdftotext`, a cooperative between-stages check for `docling`.
- **A document the backend cannot read.** Distinguished from the four
  below by *cause*, recorded as such, and deliberately not retried: the
  backend already read this PDF and could not parse it, so re-reading it
  every run would spend the same minutes to reach the same answer, and a
  run that exits nonzero forever trains its reader to ignore that. It
  stays reported, with a nonzero exit, until fixed or removed --
  `sync --reparse` is the override.
- **Ctrl+C.** Handled by an explicit SIGINT handler, because an
  `except KeyboardInterrupt` around `as_completed()` never fires. The
  handler terminates workers -- with a grace period then `kill()`, since
  native code does not honour SIGTERM promptly -- and calls `os._exit`,
  skipping the atexit hook that would *join* those workers. Safe only
  because the ledger commits incrementally, so finished work is already
  on disk.

### Partial success is a failure

`DocumentConverter.convert(raises_on_error=True)` raises only on
`FAILURE`. A `PARTIAL_SUCCESS` returns a document that stops early, and
writing it would give the citation gate a source that silently ends at
page k of n. Both call sites therefore check the status explicitly and
raise *before* anything is written, so a partial parse leaves no output
and never enters the incremental cache.

Correspondingly, a `parse_failed` document is retried on the next run
rather than skipped until its bytes change -- otherwise one dead worker
would remove documents from the corpus permanently.

### One writer at a time

`sync` and `full_pipeline` share a lock over `content/`, because the
unsafe overlap is any-writer-against-any-writer: `sync` writes parsed
text non-atomically and the enrichment layer reads those same files.

It is a dedicated sqlite file held under `BEGIN IMMEDIATE`, chosen from
measurement rather than taste. A `BEGIN IMMEDIATE` holder takes a
RESERVED lock, which **does not block readers** -- so `citation_gate`,
retrieval and the drafting skills keep working during a run. A second
writer gets `SQLITE_BUSY`. And a killed holder releases the lock
immediately, so staleness needs no PID liveness check and no
platform-specific branch.

Two rejected alternatives: an `O_EXCL` lock file needs exactly that
staleness heuristic, and locking the ledger itself would force a run into
one transaction, discarding `src/ledger.py`'s five incremental commit
points on a crash. Contention is detected by `sqlite_errorcode ==
SQLITE_BUSY` rather than by message, since `OperationalError` also covers
a full disk and a corrupt file, and the lock file is never deleted --
unlinking an open file fails on Windows, and a delete-then-recreate race
on POSIX gives two processes locks on different inodes.

### What this does not cover

The lock serialises writers only; readers see mid-run state by design.
And parsed output is not bit-reproducible at high worker counts --
Docling groups dense reference blocks differently under load, and exposes
no determinism setting to switch that off.

## Design improvement recommendations

### 1. Use hierarchical document representations
Instead of only parsing to flat text, preserve:
- document-level text
- section-level text
- chunk-level text
- page provenance

This would improve retrieval, reranking, and downstream citation support.

### 2. Split retrieval responsibilities
`retrieval.py` currently does tokenization, caching, scoring, and snippet building. Consider splitting this into:
- indexing/cache maintenance
- ranking
- snippet generation
- backend selection

### 3. Improve platform portability
The repository is cross-platform-friendly in code, but toolchain-dependent at runtime.

Likely rough spots:
- `pdftotext` availability
- `pandoc`/`pdflatex`
- Docling model/runtime dependencies

Suggested mitigations:
- clearer OS-specific setup docs
- optional fallback backends
- extend the CI matrix beyond the current Linux + Windows legs to macOS

### 4. Add reranking
For search quality, a good pattern would be:
- BM25 or vector retrieval first
- lightweight reranker second

That would improve precision without making every query expensive.

### 5. Preserve more structured metadata
When parsing PDFs, keep:
- page numbers
- section titles
- source tool
- extraction confidence if available
- citation/reference blocks where possible

This would improve evidence quality substantially.

## Parser backends

[PDF-PARSER.md](PDF-PARSER.md) owns the backend comparison -- the
tradeoffs, the two backends evaluated and removed, and the measured
speed figures. It is not restated here.

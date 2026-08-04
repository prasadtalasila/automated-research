# bench/ -- wall-clock measurement for the Docling parse path

`docs/PDF-PARSER.md` puts Docling at "~42x slower than pdftotext", measured on
5 PDFs. That is enough to choose a backend and not nearly enough to
answer "how long does a full sync of the bib corpus actually take, on
the machine in front of me, and what is the bottleneck". This directory answers that, and
keeps the answer reproducible so the parallelism work in
[PARALLELISM-PLAN.md](PARALLELISM-PLAN.md) can be checked against
measurement rather than argued from first principles.

Measured results live in [RESULTS.md](RESULTS.md); the raw per-PDF
timings behind them are in `results/<date>-<tag>/*.jsonl`.

## Running it

Needs the "heavy" Poetry group (`bash scripts/install_full_pipeline.sh
python-deps`), since it drives the real Docling stack.

```bash
# 1. Build the work lists from your own bib file (gitignored output --
#    they carry absolute PDF paths, like the bib file itself).
.venv-full/bin/python bench/make_corpus.py

# 2. Time a serial run on one GPU.
CUDA_VISIBLE_DEVICES=0 .venv-full/bin/python bench/bench_docling.py \
    --sample bench/sample16.json --out bench/results/gpu.jsonl \
    --device cuda --mode reused

# 3. Extrapolate to the whole corpus.
.venv-full/bin/python bench/estimate.py bench/results/gpu.jsonl

# 4. Measure parallel scaling (N worker processes over G GPUs).
.venv-full/bin/python bench/run_parallel.py \
    --sample bench/sample16.json --workers 8 --gpus 4 --tag w8
```

## Which tool to reach for

| Question | Tool |
|---|---|
| What does the **shipped pipeline** cost at these settings? | **`sweep_sync.py`** -- runs the real `python -m src.sync` |
| How does Docling itself behave per document? | `bench_docling.py` |
| How does the workload spread across N processes and G cards? | `run_parallel.py` |
| What would the whole corpus cost, from a sample? | `estimate.py` -- **but see its docstring: it understates** |

**Prefer a real measurement over an extrapolation whenever you can afford
one.** A per-page extrapolation from a 16-PDF sample understated a
measured full-corpus serial run by **41%**, and that figure was quoted as
fact across the documentation for two releases. `estimate.py` now leads
with the per-doc model (9% low) and says so.

```bash
# The whole scaling curve, from an empty ledger each time.
.venv-full/bin/python bench/sweep_sync.py --workers 1,4,8,12 --gpus 4 --tag scaling

# GPU scaling at a fixed worker count; OCR on vs off.
.venv-full/bin/python bench/sweep_sync.py --workers 12 --gpus 1,2,4 --tag gpus
.venv-full/bin/python bench/sweep_sync.py --workers 12,24 --ocr on,off --tag ocr

# See the plan without running anything (each run parses the whole corpus).
.venv-full/bin/python bench/sweep_sync.py --workers 1,12 --tag plan --dry-run
```

`sweep_sync.py` reports the **resolved** worker count, not the requested
one, and warns when they differ. That matters: `worker_ceiling()` clamps
to `allowed_cpus // 4`, so asking for 32 on a 48-CPU machine silently
gives you 12 — a trap that hid a measured 1.41x for a whole release.

## What this harness does *not* measure

`run_parallel.py` launches N **independent** worker processes, each
handed a shard and a GPU via `CUDA_VISIBLE_DEVICES`. That predates
`[parser].workers` and is deliberately a different thing from the pool
`src/sync.py` actually uses -- no shared counter, no pool initialiser, no
`start_method`. It answers "how does this workload scale across
processes and cards", not "what does the shipped pool cost".

So every **pool-level** figure in `RESULTS.md` -- worker counts,
per-worker GPU assignment, and `[parser].start_method` -- was measured
with the real `python -m src.sync`, not with this harness. `sweep_sync.py`
now automates that; the equivalent by hand is:

```bash
# A/B two settings over a subset of the real corpus, three runs each.
# A fresh CONTENT_DIR per run is the point: every document must actually
# need a parse, or you are timing the ledger's skip logic instead.
for method in spawn forkserver; do
  for rep in 1 2 3; do
    rm -rf /tmp/bench-content && mkdir -p /tmp/bench-content
    /usr/bin/time -f "$method rep$rep %e s" \
      env CONTENT_DIR=/tmp/bench-content BIB_FILE=/path/to/subset.bib \
          PARSER=docling PARSER_OCR=false \
          PARSER_WORKERS=4 PARSER_START_METHOD=$method \
      .venv-full/bin/python -m src.sync > /dev/null
  done
done
```

Take the **median of three**: run-to-run spread on a quiet machine was
0.3-1.0s, which is the same order as some of the effects being measured.

Build `subset.bib` by filtering your real bib file down to a
rank-stratified sample of entries -- the same reasoning as
`make_corpus.py`'s sampling. It must live in the same directory as the
PDFs it references, since `file =` paths resolve relative to the bib
file. Sampling the *smallest* N documents instead would make every run
startup-dominated by construction, which flatters exactly the change
being tested.

## What each file is

| File | Purpose |
|---|---|
| `make_corpus.py` | Resolves PDFs from `papers/bibliography.bib`, counts pages, draws rank-stratified samples |
| `bench_docling.py` | Times Docling per PDF; switches device (`cuda`/`cpu`) and converter reuse (`fresh`/`reused`) |
| `estimate.py` | Extrapolates a sample's timings to the full corpus, two ways |
| `run_parallel.py` | Runs N worker processes over G GPUs, reports aggregate throughput |
| `sweep_sync.py` | Sweeps the **real** `src.sync` over worker/GPU/OCR settings -- the pool-level numbers |
| `results/` | Committed raw timings -- the evidence behind `RESULTS.md` |

## The two switches that matter

**`--mode fresh` vs `--mode reused`.** `DocumentConverter.initialized_pipelines`
is an *instance* attribute, so a converter built per PDF re-initialises
the layout/table/OCR models every time. `fresh` reproduces that; `reused`
builds one converter for the whole run. This is the difference the
Phase 0 work in the plan is about.

**`--device cuda` vs `--device cpu`.** Docling's `AcceleratorDevice.AUTO`
resolves to `cuda:0` whenever a GPU is present, so the default is already
`cuda` -- `cpu` is here to measure how much that is worth, which on this
corpus turned out to be less than anyone would guess.

## Reading the estimate

`estimate.py` reports two extrapolations because they disagree:

- **per-page** assumes cost is proportional to page count.
- **per-doc** fits `seconds ~= a + b * pages` and sums the prediction over
  every corpus document. The intercept `a` is real -- a 1-page PDF does
  not cost a seventeenth of a 17-page one -- so this is the more honest
  model for a corpus whose median document is 16 pages.

Treat the pair as a band, not a point estimate. Per-PDF cost varied 0.11
to 1.52 s/page across the sample, so a single number would be false
precision.

## Generated, not committed

`bench/corpus.json`, `bench/sample*.json` and `bench/par_*/` are
gitignored: they contain absolute paths into `papers/`, which is per-host
data. Regenerate them with `make_corpus.py`. The `results/*.jsonl`
timings *are* committed -- they carry citekeys and durations, no paths,
and they are the evidence the plan rests on.

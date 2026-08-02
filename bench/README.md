# bench/ -- wall-clock measurement for the Docling parse path

`docs/PDF-PARSER.md` puts Docling at "~42x slower than pdftotext", measured on
5 PDFs. That is enough to choose a backend and not nearly enough to
answer "how long does a full sync of the bib corpus actually take, on
this host, and what is the bottleneck". This directory answers that, and
keeps the answer reproducible so the parallelism work in
[PARALLELISM-PLAN.md](PARALLELISM-PLAN.md) can be checked against
measurement rather than argued from first principles.

Measured results live in [RESULTS.md](RESULTS.md); the raw per-PDF
timings behind them are in `results/<date>-<tag>/*.jsonl`.

## Running it

Needs the "heavy" Poetry group (`bash scripts/install_full_pipeline.sh
python-deps`), since it drives the real Docling stack.

```bash
# 1. Build the work lists from this host's bib file (gitignored output --
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

## What each file is

| File | Purpose |
|---|---|
| `make_corpus.py` | Resolves PDFs from `papers/bibliography.bib`, counts pages, draws rank-stratified samples |
| `bench_docling.py` | Times Docling per PDF; switches device (`cuda`/`cpu`) and converter reuse (`fresh`/`reused`) |
| `estimate.py` | Extrapolates a sample's timings to the full corpus, two ways |
| `run_parallel.py` | Runs N worker processes over G GPUs, reports aggregate throughput |
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

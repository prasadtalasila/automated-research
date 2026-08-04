# Engineering plan: what to measure and change next

The **benchmarking** side of the parallel parse path — what is still
unknown, what would have to be measured before changing it, and how to
run that measurement.

This is deliberately narrow. For how the parse path is *built*, see
[docs/PARALLELISM.md](../docs/PARALLELISM.md), which carries the
architecture and the user-facing roadmap. For what anything costs, see
[docs/PERFORMANCE.md](../docs/PERFORMANCE.md) and [RESULTS.md](RESULTS.md).

Everything below is forward-looking. What was done and why is in
`git log` and RESULTS.md's dated sections; this file used to be a
phase-by-phase record of it, which had stopped being a plan.

## The method this directory exists to enforce

Five rules, each of which was learned by getting it wrong:

1. **Measure the thing you ship.** Every pool-level figure must come from
   the real `python -m src.sync` (`sweep_sync.py`), not from a harness
   that approximates it. `run_parallel.py` launches independent processes
   and shares none of the pool's machinery; it answers a different
   question.
2. **Measure the whole corpus, not a sample.** A per-page extrapolation
   from 16 PDFs understated a measured serial run by **41%**, and that
   number was quoted as fact for two releases.
3. **Report the resolved setting, not the requested one.**
   `worker_ceiling()` silently clamps, so a run can look like it honoured
   a setting it never applied. `sweep_sync.py` warns.
4. **A baseline is a measurement, not an assumption.** Efficiency figures
   are only as good as the denominator, and the denominator is the number
   least likely to be re-measured.
5. **Say what the instrument actually measures.** CPU busy comes from
   `/proc/stat`, which is host-wide: it counts every process on the
   machine, not this run. The JSONL keys say `host_` for that reason.
   Sweep on an idle machine, or the number is an upper bound.

## Open questions

Ranked by how much a change depends on the answer.

### 1. Does the clamp finding generalise?

Validated on one machine (48 CPUs, 4 GPUs) and one corpus. A CPU-only
machine, where the GPU does none of the work, would plausibly want a
different divisor — and that is the machine most likely to be hurt by
getting it wrong.

Needs: the same sweep on a CPU-only machine and on a different corpus
shape. Until then the constant should not move.

### 2. Where is the OCR optimum?

Swept only to 24 workers, where it was still improving (1213.9s at 12 →
1139.0s at 24) with the CPU already 93% busy. The knee is probably close,
but "probably" is what this directory exists to avoid.

## Closed, and how

### What flattens the curve past ~24 workers — *answered*

Three runs per point, plus the completion timeline `sweep_sync.py` now
records, settled it. First, the curve **plateaus rather than reversing**:
32w 223.4s and 48w 221.4s are 0.9% apart, and the spread *within* the 32w
triple alone is 86.8s — larger than the difference being compared. The
single-run reading that named 32 "the optimum" was noise.

What flattens it is two costs that both grow with the pool:

| Workers | Startup (to 1st completion) | Tail | CPU busy |
|---|---|---|---|
| 24 | 18.6s — 7.9% | 4.9s — 2.1% | 56% |
| 32 | 21.8s — 8.9% | 5.9s — 2.6% | 70% |
| 48 | 28.5s — **12.7%** | 7.9s — 3.6% | 78% |

Every worker pays its own ~8.5s model load, so a bigger pool spends a
bigger *fraction* of the run standing itself up; meanwhile the CPU heads
for saturation. Neither alone explains the plateau; together they do. The
long-document tail stays under 4% throughout and is not involved.

The consequence for the constant: the finding is "`_CPUS_PER_DOCLING_WORKER`
= 4 is much too large", not "it should be 1.5". Anywhere in 32-48 workers
buys the same ~1.4x on this machine, which is a wide target to hit — but
only question 1 below says whether it is the same target elsewhere.

## How to run any of it

```bash
# Whole scaling curve, three runs per point so a 2% difference can be
# told from noise.
.venv-full/bin/python bench/sweep_sync.py \
    --workers 1,4,8,12,16,24,32,48 --gpus 4 --ocr off --repeat 3 --tag curve

# GPU scaling at a fixed worker count, and the OCR question above.
.venv-full/bin/python bench/sweep_sync.py --workers 24 --gpus 1,2,4 --tag gpus
.venv-full/bin/python bench/sweep_sync.py --workers 24,32,48 --ocr on --tag ocr-knee
```

**Sweeping past the shipped ceiling needs instrumentation.**
`worker_ceiling()` clamps to `allowed_cpus // 4`, so `--workers 32`
resolves to 12 on a 48-CPU machine and `sweep_sync.py` will say so. The
2026-08-04 sweep added two temporary env overrides to `src/pdf_text.py`
— `BENCH_CPUS_PER_WORKER` and `BENCH_DOCLING_THREADS` — to get past it.
Those were measurement instruments and were **not** committed; re-add
them locally when you need them, and do not ship them.

Each run parses the whole corpus. A serial pass is ~55 minutes, so a
full curve with repeats is an overnight job — `--dry-run` prints the plan
first.

## Changes that are ready except for their measurement

| Change | Blocked on |
|---|---|
| Derive `_CPUS_PER_DOCLING_WORKER` | open questions 1 and 2 |
| Selective OCR (only bitmap-heavy documents) | a cheap page classifier, and question 2 |
| Resident worker pool across runs | nothing measured; it is a design change, not a tuning one |
| Batch inference across documents | upstream — docling exposes no batch API |

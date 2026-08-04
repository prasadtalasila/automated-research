"""Turn measured per-PDF Docling timings into a whole-corpus wall-clock estimate.

**Both models below understate a real run. This is measured, not
suspected**, and it matters because a figure from this tool was quoted as
fact across the documentation for two releases.

On 2026-08-04 a real serial `python -m src.sync` over the 501-PDF corpus
(OCR off) took **3330s / 55m 30s**. From the same 16-PDF sample this tool
predicted:

  per-doc     50m 32s   -- 9% low
  per-page    39m 11s   -- 41% low   <- the number the docs quoted

So: **read the per-doc figure, treat it as a floor, and prefer a real
measurement whenever you can afford one** (bench/sweep_sync.py runs the
actual pipeline). The two models:

  per-doc    for each corpus PDF, predict from a linear fit
             seconds ~= a + b*pages, then sum. The intercept `a` is real
             -- a 1-page PDF does not cost 1/17th of a 17-page one -- so
             this is the model to read.

  per-page   total_pages * (measured seconds / measured pages).
             Assumes cost is strictly proportional to pages, which drops
             the intercept entirely. Reported only as an optimistic bound.

Why both are still low: a sample of 16 documents parsed back-to-back in
one warm process pays no per-document process overhead, no ledger
bookkeeping, and no scheduling gaps -- all of which a 501-document run
does. The sample is drawn evenly by page rank (bench/sample16.json), so
the page mix is right; it is the surrounding work that is missing.
"""

import argparse
import json
from pathlib import Path


def load(path: str) -> tuple[dict, list[dict]]:
    meta, rows = {}, []
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line)
        if rec.get("record") == "meta":
            meta = rec
        elif rec.get("ok"):
            rows.append(rec)
    return meta, rows


def linfit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0
    return my - slope * mx, slope


# Measured on 2026-08-04, full 501-PDF corpus, docling, OCR off, 4x A40,
# 48 CPUs available. Efficiency = speedup / resolved workers. Used instead
# of assuming linear scaling, which overstates every parallel estimate.
#   NOTE the shipped worker_ceiling() caps at allowed_cpus // 4 = 12 here,
#   so the 24/32/48 rows required relaxing that clamp and are not
#   reachable with a stock checkout.
_MEASURED_EFFICIENCY = {1: 1.00, 4: 1.04, 8: 0.97, 12: 0.89,
                        16: 0.78, 24: 0.58, 32: 0.47, 48: 0.31}


def measured_efficiency(workers: int) -> tuple[float, bool]:
    """(efficiency, is_interpolated) for a worker count, from the curve above."""
    if workers in _MEASURED_EFFICIENCY:
        return _MEASURED_EFFICIENCY[workers], False
    points = sorted(_MEASURED_EFFICIENCY)
    if workers < points[0]:
        return _MEASURED_EFFICIENCY[points[0]], True
    if workers > points[-1]:
        return _MEASURED_EFFICIENCY[points[-1]], True
    lo = max(p for p in points if p <= workers)
    hi = min(p for p in points if p >= workers)
    if lo == hi:
        return _MEASURED_EFFICIENCY[lo], True
    span = (workers - lo) / (hi - lo)
    return (_MEASURED_EFFICIENCY[lo]
            + span * (_MEASURED_EFFICIENCY[hi] - _MEASURED_EFFICIENCY[lo])), True


def hms(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="bench/*.jsonl timing files")
    ap.add_argument("--corpus", default="bench/corpus.json")
    ap.add_argument("--workers", type=int, default=4, help="parallel GPU workers to model")
    ap.add_argument("--efficiency", type=float, default=None,
                    help="parallel efficiency override; default uses the measured "
                         "curve below rather than assuming perfect scaling")
    args = ap.parse_args()

    corpus = [c for c in json.loads(Path(args.corpus).read_text()) if c["pages"]]
    total_pages = sum(c["pages"] for c in corpus)
    n_docs = len(corpus)

    print(f"corpus: {n_docs} PDFs, {total_pages} pages\n")
    for run in args.runs:
        meta, rows = load(run)
        if not rows:
            print(f"{run}: no successful rows\n")
            continue
        pages = sum(r["pages"] for r in rows)
        secs = sum(r["seconds"] for r in rows)
        a, b = linfit([r["pages"] for r in rows], [r["seconds"] for r in rows])
        per_page_total = total_pages * (secs / pages)
        per_doc_total = sum(max(a + b * c["pages"], 0.0) for c in corpus)

        print(f"=== {Path(run).name}  [{meta.get('device')}/{meta.get('mode')}"
              f"{'/images' if meta.get('images') else ''}]")
        print(f"  sample      : {len(rows)} PDFs, {pages} pages, {secs:.1f}s "
              f"({secs/pages:.2f} s/page, {secs/len(rows):.1f} s/doc)")
        print(f"  cold start  : {meta.get('cold_start_s')}s (model load + first convert)")
        print(f"  linear fit  : seconds ~= {a:.1f} + {b:.3f} * pages")
        print(f"  serial est. : per-doc {hms(per_doc_total)}  "
              f"(per-page {hms(per_page_total)}, an optimistic bound)")
        print(f"                both understate a real run -- see this module's "
              f"docstring; measured was 9% and 41% above these respectively")
        w = args.workers
        if args.efficiency is not None:
            eff, interpolated = args.efficiency, False
            source = "your --efficiency"
        else:
            eff, interpolated = measured_efficiency(w)
            source = "measured curve" + (", interpolated" if interpolated else "")
        print(f"  {w} workers  : per-doc {hms(per_doc_total/(w*eff))}"
              f"   (at {eff:.0%} efficiency, {source})")
        print()


if __name__ == "__main__":
    main()

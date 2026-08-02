"""Turn measured per-PDF Docling timings into a whole-corpus wall-clock estimate.

Two extrapolation models, reported side by side because they disagree
and the disagreement is the interesting part:

  per-page   total_pages * (measured seconds / measured pages)
             Assumes cost is proportional to pages. This is the model to
             trust if the sample's page mix matches the corpus's.

  per-doc    for each corpus PDF, predict from a linear fit
             seconds ~= a + b*pages, then sum.
             The intercept `a` is real -- a 1-page PDF does not cost
             1/17th of a 17-page one -- so this is the more honest model
             for a corpus whose median document is small (16 pages here).

The sample is drawn evenly by page rank (bench/sample16.json), so its
page mix mirrors the corpus's by construction, tail included.
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


def hms(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="bench/*.jsonl timing files")
    ap.add_argument("--corpus", default="bench/corpus.json")
    ap.add_argument("--workers", type=int, default=4, help="parallel GPU workers to model")
    ap.add_argument("--efficiency", type=float, default=1.0,
                    help="measured parallel efficiency (1.0 = perfect scaling)")
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
        print(f"  serial est. : per-page {hms(per_page_total)}   per-doc {hms(per_doc_total)}")
        for w in (args.workers,):
            eff = args.efficiency
            print(f"  {w} workers  : per-page {hms(per_page_total/(w*eff))}   "
                  f"per-doc {hms(per_doc_total/(w*eff))}"
                  + (f"   (at {eff:.0%} efficiency)" if eff != 1.0 else ""))
        print()


if __name__ == "__main__":
    main()

"""Build the benchmark's work lists from this host's own bib corpus.

Writes three files, all gitignored because all three are per-host data
(they carry absolute PDF paths, and `papers/bibliography.bib` itself is
gitignored for the same reason -- see AGENTS.md):

  bench/corpus.json      every resolvable PDF, with its page count
  bench/sample16.json    16 PDFs drawn at even page-rank intervals
  bench/sample_small.json  the 6 smallest of those

The samples are drawn by *rank*, not at random and not by hand, so the
sample's page distribution mirrors the corpus's -- including the tail,
which matters here: one 675-page document is 5% of all pages in this
corpus, and a sample that happens to exclude it understates the total by
more than any other single choice in this harness.

Run this before bench/bench_docling.py. Needs the "heavy" Poetry group
(pypdfium2 arrives with docling).
"""

import json
import os
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
# Same shape as scripts/verbatim_check.py: running this as a file puts
# bench/ on sys.path, not the repo root, so `from src import ...` needs
# the root put back first.
sys.path.insert(0, str(BENCH_DIR.parent))

from src import bib_reader  # noqa: E402 -- needs the repo root on sys.path first


def build_corpus() -> list[dict]:
    import pypdfium2 as pdfium

    rows = []
    for ref in bib_reader.read_library():
        if not ref.pdf_path:
            continue
        try:
            doc = pdfium.PdfDocument(ref.pdf_path)
            pages = len(doc)
            doc.close()
        except Exception:  # noqa: BLE001 -- an unopenable PDF is data, not a crash
            pages = None
        rows.append({
            "citekey": ref.citekey,
            "path": ref.pdf_path,
            "pages": pages,
            "bytes": os.path.getsize(ref.pdf_path),
        })
    return rows


def rank_sample(rows: list[dict], n: int) -> list[dict]:
    """`n` PDFs at even intervals through the page-sorted corpus.

    dict.fromkeys de-duplicates the index list rather than the rows: on a
    corpus smaller than `n`, evenly spaced ranks collide, and picking the
    same document twice would double-count its pages in the throughput
    figure.
    """
    ordered = sorted((r for r in rows if r["pages"]), key=lambda r: r["pages"])
    if not ordered:
        return []
    idx = [round(i * (len(ordered) - 1) / max(n - 1, 1)) for i in range(n)]
    return [ordered[i] for i in dict.fromkeys(idx)]


def main() -> None:
    rows = build_corpus()
    pages = [r["pages"] for r in rows if r["pages"]]
    (BENCH_DIR / "corpus.json").write_text(json.dumps(rows, indent=1))

    sample16 = rank_sample(rows, 16)
    (BENCH_DIR / "sample16.json").write_text(json.dumps(sample16, indent=1))
    (BENCH_DIR / "sample_small.json").write_text(json.dumps(sample16[:6], indent=1))

    print(f"corpus     : {len(rows)} PDFs, {sum(pages)} pages, "
          f"{sum(r['bytes'] for r in rows) / 1e9:.2f} GB")
    if pages:
        ordered = sorted(pages)
        print(f"pages      : median {ordered[len(ordered) // 2]}, "
              f"mean {sum(pages) / len(pages):.1f}, max {max(pages)}")
    print(f"sample16   : {len(sample16)} PDFs, "
          f"{sum(s['pages'] for s in sample16)} pages")


if __name__ == "__main__":
    main()

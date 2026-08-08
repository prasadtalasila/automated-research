"""Wall-clock cost of `python3 -m src.dossier status --all`.

The sweep's design turns on one claim -- that throwing the BM25 index
away after each scan is affordable, because a warm cache makes the scan
nearly free and a cold one costs a single corpus tokenization shared
across every dossier (docs/DRAFT-ITERATION.md, "Why the new papers are
not found with `search()`"). That is an argument about wall-clock, and
it was made from the shape of the code rather than from a stopwatch.
This measures it.

Unlike the rest of `bench/`, this builds a **synthetic** corpus rather
than reading this host's own. The drift scan never opens a PDF -- it
reads `content/parsed/*.txt` and the ledger's rows -- so what its cost
depends on is the number of documents, the length of their text, and the
number of dossiers, all of which can be generated honestly. What a
synthetic corpus cannot tell you is whether real prose tokenizes at the
same rate as generated prose; the vocabulary here is deliberately Zipfian
for that reason, but treat the absolute numbers as this shape of corpus,
not as a promise about yours.

Defaults mirror this project's own corpus: 501 documents, a median of 16
pages at ~500 words per page, and one 675-page book that is 5% of all
pages by itself.

    python3 bench/bench_drift.py                       # the default sweep
    python3 bench/bench_drift.py --docs 2000 --dossiers 50
    python3 bench/bench_drift.py --out bench/results/<date>-drift/drift.json

Stdlib only and no GPU, like the module it measures -- this one runs
under bare `python3`.
"""

import argparse
import json
import math
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
# Same shape as bench/make_corpus.py: running this as a file puts bench/
# on sys.path, not the repo root, so `from src import ...` needs the root
# put back first.
sys.path.insert(0, str(BENCH_DIR.parent))

from src import config  # noqa: E402

# The corpus this project actually has, from docs/PERFORMANCE.md.
DEFAULT_DOCS = 501
MEDIAN_PAGES = 16
WORDS_PER_PAGE = 500
BOOK_PAGES = 675

# Enough distinct terms that the index is not trivially small, drawn
# Zipfian so a handful of terms dominate every document -- which is what
# makes BM25's idf do any work at all. A uniform vocabulary would make
# every document look equally relevant to every query and understate the
# scoring cost.
VOCAB = [f"term{n}" for n in range(4000)]
# Computed once rather than per document: it is a fixed function of VOCAB,
# and rebuilding it inside `_document` made corpus *generation* O(|VOCAB|)
# per document -- 4000 divisions each, which starts to dominate setup at
# larger --docs. Setup is not inside the timed region, so this never
# affected a published figure; it just made the benchmark slower to run.
VOCAB_WEIGHTS = [1 / (i + 1) for i in range(len(VOCAB))]
QUERY_TERMS = ["digital", "twin", "cosimulation", "model", "architecture"]

# Where --real reads from, resolved before config's paths are redirected
# at the throwaway tree.
REAL_LEDGER = config.LEDGER_PATH


def _document(rng: random.Random, pages: int) -> str:
    words = rng.choices(VOCAB, weights=VOCAB_WEIGHTS, k=pages * WORDS_PER_PAGE)
    # Salt a minority of documents with the query vocabulary, so the
    # ranked top-k is a real selection rather than an arbitrary one.
    if rng.random() < 0.2:
        words += rng.choices(QUERY_TERMS, k=200)
    return " ".join(words)


def build_corpus(docs: int, seed: int = 0) -> int:
    """A ledger plus `parsed/*.txt`, in the shape `sync` leaves them.

    Returns the total size of the parsed text in bytes -- `st_size` of
    what was actually written, which is what `adopt_real_corpus` reports
    too. `len(text)` would be characters, and the two only agree while the
    generated vocabulary stays ASCII, which is not a property this file
    should silently depend on.
    """
    from src import ledger

    rng = random.Random(seed)
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    con = ledger.connect()
    for n in range(docs):
        # One outsized document, as the real corpus has.
        pages = BOOK_PAGES if n == 0 else max(1, int(rng.lognormvariate(
            math.log(MEDIAN_PAGES), 0.7)))
        citekey = f"author{n}_paper_20{n % 30:02d}"
        text = _document(rng, pages)
        path = config.PARSED_DIR / f"{citekey}.txt"
        path.write_text(text, encoding="utf-8")
        total += path.stat().st_size
        con.execute(
            "INSERT INTO items (citekey, title, parsed_path, status, last_synced) "
            "VALUES (?, ?, ?, 'parsed', '2026-08-08')",
            (citekey, f"A paper about {rng.choice(QUERY_TERMS)} number {n}", str(path)),
        )
    con.commit()
    con.close()
    return total


def build_dossiers(count: int, queries_each: int) -> None:
    """`count` dossiers, each having logged `queries_each` retrieval calls."""
    from src import dossier

    for n in range(count):
        draft = config.DRAFTS_DIR / f"topic{n}" / "survey.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(f"# Survey {n}\n\n## 1. First\n\ntext\n")
        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        (target / "evidence.md").write_text(
            f"# Kept evidence\n\n## `author{n}_paper_20{n % 30:02d}`\n\nkept.\n"
        )
        (target / "sections.md").write_text(
            "# Sections\n\n| section | citekeys |\n|---|---|\n"
            f"| 1. First | `author{n}_paper_20{n % 30:02d}` |\n"
        )
        for q in range(queries_each):
            dossier.log_retrieval(
                draft, "search",
                f"{QUERY_TERMS[q % len(QUERY_TERMS)]} {QUERY_TERMS[(q + 1) % len(QUERY_TERMS)]}",
                15, 15, 2400,
            )


def _time(fn, repeats: int) -> dict:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    times.sort()
    return {"best": times[0], "median": times[len(times) // 2], "worst": times[-1],
            "repeats": repeats}


def adopt_real_corpus(source_ledger: Path, dest: Path) -> tuple[int, int]:
    """Point the benchmark at this host's own corpus, without writing to it.

    The ledger is **copied** rather than opened in place, because the warm
    step calls the real `retrieval.search()`, and that goes through
    `ledger.connect()` -- a write connection that runs migrations. Reading
    someone's corpus to time a scan is fine; migrating it is not. The
    copied rows keep their absolute `parsed_path` values, so the text
    being tokenized is the host's real parsed output, read-only.

    Returns (documents, bytes of parsed text).
    """
    import sqlite3

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_ledger, dest)
    con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    rows = con.execute("SELECT parsed_path FROM items").fetchall()
    con.close()
    total = 0
    for (parsed_path,) in rows:
        if parsed_path:
            try:
                total += Path(parsed_path).stat().st_size
            except OSError:
                pass
    return len(rows), total


def run(docs: int, dossier_counts: list[int], queries_each: int, repeats: int,
        real_ledger: Path | None = None) -> dict:
    """Time a sweep at each dossier count, cold and warm.

    `docs` is the size of the corpus to generate, and is **ignored when
    `real_ledger` is given** -- the count then comes from the ledger being
    copied, and is reported back in the result rather than taken on
    trust.
    """
    from src import dossier, retrieval

    if real_ledger is not None:
        docs, parsed_bytes = adopt_real_corpus(real_ledger, config.LEDGER_PATH)
    else:
        parsed_bytes = build_corpus(docs)
    build_dossiers(max(dossier_counts), queries_each)

    result = {
        "corpus": "real" if real_ledger is not None else "synthetic",
        "docs": docs,
        "parsed_bytes": parsed_bytes,
        "queries_per_dossier": queries_each,
        "dossier_counts": dossier_counts,
        "cold": {},
        "warm": {},
        "no_queries": {},
    }

    every = dossier.all_dossiers()
    real_all_dossiers = dossier.all_dossiers

    def sweep(subset):
        # Measure `drift_all()` exactly as shipped rather than a
        # reimplementation of it: the thing being timed is its one ledger
        # read and one lazily built index, and a hand-rolled loop here
        # could accidentally not have them. Narrowing what it sweeps means
        # patching its one input; `_measure` puts it back.
        dossier.all_dossiers = lambda: subset
        return dossier.drift_all()

    try:
        for count in dossier_counts:
            subset = every[:count]

            # Cold: no index cache on disk, as on the first sweep after a
            # sync that changed every fingerprint. Every repeat is cold,
            # not just the first -- the scan never writes the cache back.
            config.RETRIEVAL_INDEX_PATH.unlink(missing_ok=True)
            result["cold"][str(count)] = _time(lambda: sweep(subset), repeats)

            # Warm: the cache `python3 -m src.retrieval` leaves behind.
            # Built once here through the real indexer, then reused
            # read-only.
            retrieval.search("digital twin", k=1)
            result["warm"][str(count)] = _time(lambda: sweep(subset), repeats)

        # The path that never builds an index at all: dossiers that logged
        # no retrieval calls. Measured at the largest count only.
        for path in every:
            (path / "retrieval.md").unlink(missing_ok=True)
        config.RETRIEVAL_INDEX_PATH.unlink(missing_ok=True)
        result["no_queries"][str(max(dossier_counts))] = _time(
            lambda: sweep(every), repeats)
    finally:
        dossier.all_dossiers = real_all_dossiers
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--real", action="store_true",
                        help="Use this host's own ledger and parsed text instead of "
                             "a generated corpus (read-only: the ledger is copied)")
    parser.add_argument("--docs", type=int, default=DEFAULT_DOCS,
                        help=f"Documents to generate (default {DEFAULT_DOCS}); "
                             "ignored with --real, which takes the count from the "
                             "ledger it copies")
    parser.add_argument("--dossiers", type=int, nargs="*", default=[1, 10, 50])
    parser.add_argument("--queries-each", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", help="Write the raw result JSON here")
    parser.add_argument("--real-ledger",
                        help=f"Ledger to copy for --real (default: {REAL_LEDGER})")
    parser.add_argument("--keep", action="store_true",
                        help="Leave the generated corpus on disk")
    args = parser.parse_args(argv)

    # Validated before anything is created, so a bad --real cannot leave a
    # temp tree behind on the way out.
    real_ledger = None
    if args.real:
        real_ledger = Path(args.real_ledger) if args.real_ledger else REAL_LEDGER
        if not real_ledger.is_file():
            print(f"No ledger at {real_ledger} -- run `python -m src.sync` first, "
                  "or drop --real to generate a corpus.", file=sys.stderr)
            return 1

    # Never touch the host's real content/: everything is generated into a
    # throwaway tree, exactly as tests/conftest.py's isolated_config does.
    #
    # Saved and restored for the same reason `run()` restores
    # `all_dossiers`: these are module-level constants that every `src`
    # module reads at call time, so leaving them pointed at a directory
    # this function is about to delete would break any caller that
    # imports this one rather than running it as a script.
    root = Path(tempfile.mkdtemp(prefix="bench-drift-"))
    content = root / "content"
    redirected = {
        "CONTENT_DIR": content,
        "PARSED_DIR": content / "parsed",
        "LEDGER_PATH": content / "ledger.sqlite",
        "DRAFTS_DIR": content / "drafts",
        "DOSSIERS_DIR": content / "dossiers",
        "RETRIEVAL_INDEX_PATH": content / "retrieval_index.json",
    }
    original = {name: getattr(config, name) for name in redirected}
    for name, value in redirected.items():
        setattr(config, name, value)

    try:
        result = run(args.docs, sorted(args.dossiers), args.queries_each, args.repeats,
                     real_ledger)
    finally:
        for name, value in original.items():
            setattr(config, name, value)
        if args.keep:
            print(f"corpus kept at {root}\n", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)

    mb = result["parsed_bytes"] / 1e6
    print(f"corpus ({result['corpus']}): {result['docs']} documents, {mb:.1f} MB of parsed text, "
          f"{args.queries_each} queries per dossier\n")
    print(f"{'dossiers':>9}  {'cold (s)':>10}  {'warm (s)':>10}  {'ratio':>7}")
    for count in result["dossier_counts"]:
        cold = result["cold"][str(count)]["median"]
        warm = result["warm"][str(count)]["median"]
        print(f"{count:>9}  {cold:>10.3f}  {warm:>10.3f}  {cold / warm:>6.1f}x")
    biggest = str(max(result["dossier_counts"]))
    print(f"\nno logged queries, {biggest} dossiers: "
          f"{result['no_queries'][biggest]['median']:.3f}s "
          "(no index is built at all)")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

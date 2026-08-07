"""Does the same corpus, parsed twice, produce the same *quotable passages*?

`bench/RESULTS.md` already answers a narrower question: at high worker
counts a handful of `content/parsed/<citekey>.txt` files come back with
dense reference blocks grouped into elements slightly differently -- 6 of
501, under 0.06% of a file. That was filed as cosmetic, and for BM25 it
is: retrieval tokenises on whitespace, so regrouping the same words
changes no ranking.

It is not obviously cosmetic one layer down. `src/passages.py`'s
`passage_records()` writes **one record per `dl_doc.texts` item**, and
`PASSAGE_LABELS` includes `list_item` -- the very element type that
finding names. If Docling splits a reference block across list elements
differently, the sidecar does not merely shift bytes: the *set of
passages* changes, and with it the exact span this pipeline would quote
to a reviewer. On a tool whose whole purpose is grounded citation, "the
passage we would quote can differ between runs" is a different claim from
"6 files differ by 59 bytes", and nobody has measured which one is true.

That is what this script measures, and why it is separate from
`sweep_sync.py`: that harness answers "what does a configuration cost"
and throws its output away, while this one answers "is the output the
same" and must keep every byte to compare.

## What it varies, and what it holds still

The variance is not device-dependent -- parsing one document explicitly
on three different cards gives byte-identical output every time -- so the
knob is contention, and the comparison that produced the original finding
was one-GPU against four-GPU at a fixed worker count. This reproduces
that shape: `--workers` is fixed, `--gpus` varies, and `--cpus` pins the
affinity mask so the CPU budget cannot drift between arms (which would
change `worker_ceiling()` and `docling_threads()` underneath the
comparison).

Every configuration is also run `--repeat` times, and that control turned
out to matter more than it was meant to. `bench/RESULTS.md` states that
"repeating a run at the same worker count reproduces exactly"; **this
harness falsified that** on its first hundred-document sample, where two
runs of one identical configuration -- same 12 workers, same 4 cards,
same pinned CPU mask -- disagreed on one document. So `--repeat 1` is not
enough to interpret anything: without the same-config arm, that
difference would have been silently attributed to the GPU count.

## Three levels of difference, deliberately not conflated

A sidecar carries a `bbox` of four floats straight from Docling's
provenance. Float wobble can byte-differ a file whose passages are
identical, so reporting bytes alone would overstate the finding this
script exists to check. Each pair is therefore compared at three levels,
narrowing from "any byte" to "the thing a reviewer would be shown":

  bytes   -- the raw fact: is the file identical?
  spans   -- is the ordered list of (text, label, page) records
             identical? Ignores bbox, so float wobble in coordinates
             nothing reads yet cannot masquerade as instability.
  texts   -- the load-bearing fact: is the ordered list of passage
             *texts* identical? This is what would actually be quoted.

`spans` and `texts` are kept apart because a real run separated them.
Docling classified one identical 124-character line as `list_item` in a
one-GPU run and `text` in a four-GPU run: `spans` differs, `texts` does
not. Both labels are in `PASSAGE_LABELS`, so the passage survives either
way and nothing quotable changed -- reporting that as a changed quotation
would be an overstatement.

It is not nothing, though, and the reason is worth stating: a label that
flipped *out* of `PASSAGE_LABELS` (to `footnote`, `caption`,
`page_header`) would delete a quotable passage outright. A label flip is
therefore evidence that the classification is unstable, and only the
membership of `PASSAGE_LABELS` decides whether a given flip is harmless.

A document whose `.txt` differs while its texts do not is a real result,
and the opposite of the one hypothesised above -- it would mean the
markdown variance does not reach the quotable text. Both outcomes are
worth having; only conflating them is worthless.

## The sample, and why outliers come out

`--sample N` draws N documents from `bench/corpus.json` (run
`make_corpus.py` first) using the same rank sampling as the rest of
`bench/`, so the subset's page distribution mirrors the corpus's rather
than being picked by hand.

Page-count outliers are excluded first, by Tukey's rule -- above
`Q3 + 1.5*IQR`. On this corpus that fence sits at 49 pages and removes 36
of 497 documents (7.2%) which between them hold **42% of all pages**,
including one 675-page book that alone is 5% of the corpus and took 246s
on its own in the 2026-08-02 baseline.

Excluding them is right for *this* question and would be wrong for a
throughput benchmark, which is why it is a flag and not a default
buried elsewhere. A reproducibility check counts documents, not pages: a
single book cannot be more or less reproducible than a paper, but it can
double the wall clock of every arm and set a floor no amount of
parallelism gets under -- which buys no statistical power at all. Pass
`--keep-outliers` to measure the corpus as it actually is.

## Usage

    python bench/make_corpus.py                 # writes bench/corpus.json
    python bench/repro_check.py --sample 50 \\
        --workers 12 --gpus 1,4 --repeat 2 --cpus 0-23,48-71 --tag n50

Each run starts from an empty CONTENT_DIR, so it also gets its own
`pipeline.lock.db` and cannot contend with -- or block -- a real `sync`
on the same host.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_corpus import rank_sample  # noqa: E402 -- needs bench/ on sys.path first

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"

# `sync` prints this once, with the count worker_ceiling() actually
# resolved to rather than the one requested. Recording the resolved
# number is not pedantry: that clamp hid a measured 1.41x for an entire
# release because every run looked like it honoured the setting.
_WORKERS_LINE = re.compile(r"parsing (\d+) document\(s\) with (\d+) workers")
_PARSED_LINE = re.compile(r"Sync complete: (\d+) parsed")


def _portable(path: Path) -> str:
    """`path` relative to the repo root when it is inside it, else as-is.

    These strings end up in a committed record. An absolute path names
    the machine that produced it -- and on a throwaway worktree, a
    directory that will not exist anywhere ever again -- which makes the
    evidence unreadable to the next person and noisy in a diff.
    """
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# Below this many documents, quartiles describe nothing: Tukey's rule
# over three points is arithmetic, not statistics. It is also the range
# where `statistics.quantiles` is version-dependent -- 3.13 special-cases
# a single data point, 3.12 (still supported per pyproject.toml) raises
# below two, and an empty sequence raises on both. One guard covers the
# meaningless and the unsupported cases together.
_MIN_DOCS_FOR_OUTLIERS = 4


def outlier_fence(rows: "list[dict]") -> float:
    """Tukey's upper fence over page counts: Q3 + 1.5 * IQR.

    A named, standard rule rather than a hand-picked page cap, so the
    exclusion is something a reader can re-derive and disagree with
    rather than a judgement they have to take on trust.

    Returns infinity -- "nothing is an outlier" -- on a corpus too small
    for the rule to mean anything, rather than raising. A three-PDF
    corpus is a legitimate thing to point this at while trying the
    harness out, and refusing to trim it is the right answer; crashing
    inside a quartile is not. `--outliers-only` against such a corpus
    then selects nothing and is caught by build_sample's own guard, which
    can say something useful about it.
    """
    pages = [r["pages"] for r in rows]
    if len(pages) < _MIN_DOCS_FOR_OUTLIERS:
        return float("inf")
    q1, q3 = statistics.quantiles(pages, n=4)[0], statistics.quantiles(pages, n=4)[2]
    return q3 + 1.5 * (q3 - q1)


def build_sample(n: int, keep_outliers: bool, outliers_only: bool, out_bib: Path) -> dict:
    """Write a bib naming `n` rank-sampled documents, and describe it.

    A bib file rather than a document list because that is the only input
    `sync` takes -- running the real entrypoint is the point of this
    harness, exactly as in `sweep_sync.py`, so the subset has to arrive
    the way a corpus normally does.
    """
    corpus_path = BENCH_DIR / "corpus.json"
    if not corpus_path.exists():
        raise SystemExit(f"No {corpus_path}. Run: python bench/make_corpus.py")
    rows = [r for r in json.loads(corpus_path.read_text()) if r.get("pages")]
    fence, excluded, mode = None, 0, "trimmed"
    if outliers_only:
        # The complement of the trimmed sample, and the arm that keeps
        # the exclusion from quietly deciding the result. The mechanism
        # under test is regrouping inside dense reference blocks, and
        # these are the documents with the most elements to regroup --
        # so a null over the trimmed corpus means nothing until the
        # population it removed has been looked at directly.
        fence = outlier_fence(rows)
        rows = [r for r in rows if r["pages"] > fence]
        mode = "outliers-only"
    elif not keep_outliers:
        fence = outlier_fence(rows)
        kept = [r for r in rows if r["pages"] <= fence]
        excluded = len(rows) - len(kept)
        rows = kept
    else:
        mode = "untrimmed"
    # n larger than the population means "all of it" -- rank_sample
    # already de-duplicates, but saying so here keeps the record honest
    # about what was actually parsed.
    sample = rank_sample(rows, min(n, len(rows)))
    if not sample:
        # Reachable: --outliers-only against a corpus whose page counts
        # are tight enough to have no Tukey outliers at all. Without this
        # the next few lines divide by zero and max() an empty sequence,
        # which reports a corpus problem as a crash in the arithmetic.
        raise SystemExit(
            f"No documents to parse ({mode} selection over "
            f"{corpus_path.name} is empty"
            + (f"; Tukey fence is {fence:.1f} pages" if fence is not None else "")
            + "). Nothing to compare -- widen the selection or re-run "
              "make_corpus.py."
        )
    # Minimal entries: this bib exists to name PDFs for the parser, and
    # nothing downstream of the parse is being measured. The `file` field
    # carries an absolute path, which is what bib_reader resolves.
    out_bib.write_text("\n".join(
        "@article{%s,\n  title = {Bench document %s},\n  author = {Bench, A},\n"
        "  year = {2024},\n  file = {x.pdf:%s:application/pdf},\n}\n"
        % (r["citekey"], r["citekey"], r["path"])
        for r in sample
    ))
    return {
        "requested": n,
        "documents": len(sample),
        "pages": sum(r["pages"] for r in sample),
        "pages_per_doc": round(sum(r["pages"] for r in sample) / len(sample), 1),
        "max_pages": max(r["pages"] for r in sample),
        "sample_mode": mode,
        "outlier_rule": None if keep_outliers else "tukey_q3_plus_1.5_iqr",
        # None for both "no rule applied" and "rule applied but the
        # corpus was too small for a finite fence". json.dumps would
        # otherwise emit bare `Infinity`, which is not valid JSON and
        # which a strict reader of this record would reject outright --
        # `outliers_excluded: 0` already says nothing was trimmed.
        "outlier_fence_pages": (round(fence, 1)
                                if fence is not None and math.isfinite(fence) else None),
        "outliers_excluded": excluded,
        # The citekeys themselves, not just a path to a generated bib.
        # rank_sample is deterministic, so a reader could in principle
        # re-derive them -- but only from bench/corpus.json, which is
        # gitignored per-host data. Inline, the record says which
        # documents were measured without needing that file at all.
        "citekeys": [r["citekey"] for r in sample],
        "bib": _portable(out_bib),
    }


def gpu_state() -> "list[dict] | None":
    """Per-card memory and utilisation, or None if nvidia-smi isn't there.

    Recorded before every run because this host is shared: another
    process filling a card changes what `usable_devices()` hands out, and
    a run whose arm quietly resolved to fewer cards than requested is not
    the run the record claims it is.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cards = []
    for line in out.strip().splitlines():
        index, used, util = (part.strip() for part in line.split(","))
        cards.append({"index": int(index), "memory_used_mib": int(used),
                      "utilisation_pct": int(util)})
    return cards


def run_once(bib: Path, workers: int, gpus: int, cpus: "str | None",
             content_dir: Path, python: str) -> dict:
    """One `python -m src.sync` over `bib`, into `content_dir`, kept.

    The environment is set per run rather than via config.toml so that a
    single checkout can produce every arm without being edited between
    them -- and so the record below can state exactly what was asked for
    beside what was resolved.
    """
    env = {
        **os.environ,
        "BIB_FILE": str(bib),
        "CONTENT_DIR": str(content_dir),
        "PARSER": "docling",
        "PARSER_OCR": "false",
        "PARSER_WORKERS": str(workers),
        "CUDA_VISIBLE_DEVICES": ",".join(str(i) for i in range(gpus)),
        # A stall watchdog firing mid-run would truncate the corpus and
        # leave a short, wrong comparison looking like a clean one.
        "PARSER_STALL_TIMEOUT": "off",
    }
    # taskset rather than trusting the ambient mask: allowed_cpus() feeds
    # both worker_ceiling() and docling_threads(), so an arm that ran
    # with a different CPU budget is not comparable to its partner.
    prefix = ["taskset", "-c", cpus] if cpus else []
    before = gpu_state()
    started = time.perf_counter()
    proc = subprocess.run(
        [*prefix, python, "-m", "src.sync"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    wall = time.perf_counter() - started
    combined = proc.stdout + proc.stderr
    workers_match = _WORKERS_LINE.search(combined)
    parsed_match = _PARSED_LINE.search(combined)
    return {
        "workers_requested": workers,
        "workers_resolved": int(workers_match.group(2)) if workers_match else 1,
        "gpus_requested": gpus,
        "cpus_pinned": cpus,
        "wall_seconds": round(wall, 1),
        "parsed": int(parsed_match.group(1)) if parsed_match else None,
        "exit_code": proc.returncode,
        # usable_devices() complains to stderr when it drops a card it
        # cannot fit on; keeping the line is what makes a surprising arm
        # explicable afterwards instead of merely surprising.
        "device_complaints": [line for line in proc.stderr.splitlines()
                              if "GPU" in line or "device" in line.lower()],
        "gpu_state_before": before,
        "content_dir": _portable(content_dir),
    }


def _spans(sidecar: Path) -> "list | None":
    """The (text, label, page) of every record, in order -- the sidecar
    with its bboxes dropped.

    This is what a consumer of `src/passages.py` actually quotes. Two
    sidecars agreeing here differ only in coordinates nothing reads yet.
    """
    try:
        records = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [[r.get("text"), r.get("label"), r.get("page")] for r in records]


def fingerprint(content_dir: Path) -> dict:
    """Per citekey: the text digest, the sidecar digest, and the spans.

    Digests rather than contents so a whole run's fingerprint stays small
    enough to keep in the record beside its timings.
    """
    parsed = content_dir / "parsed"
    out = {}
    for txt in sorted(parsed.glob("*.txt")):
        citekey = txt.stem
        sidecar = parsed / f"{citekey}.passages.json"
        spans = _spans(sidecar) if sidecar.exists() else None
        out[citekey] = {
            "txt_sha": hashlib.sha256(txt.read_bytes()).hexdigest(),
            "sidecar_sha": (hashlib.sha256(sidecar.read_bytes()).hexdigest()
                            if sidecar.exists() else None),
            "spans_sha": (hashlib.sha256(
                json.dumps(spans, sort_keys=True).encode()).hexdigest()
                if spans is not None else None),
            # Texts alone: what a reviewer would actually be shown. Kept
            # apart from spans_sha so a label flip on identical text is
            # not reported as a changed quotation.
            "texts_sha": (hashlib.sha256(
                json.dumps([s[0] for s in spans], sort_keys=True).encode()).hexdigest()
                if spans is not None else None),
            "n_spans": len(spans) if spans is not None else None,
        }
    return out


def compare(left: dict, right: dict) -> dict:
    """Which citekeys differ, at each of the two levels.

    Only citekeys present in both are compared; a missing one is a failed
    parse, which is a different problem and is reported separately rather
    than counted as a difference.
    """
    shared = sorted(set(left) & set(right))
    txt_diff, sidecar_diff, spans_diff, texts_diff = [], [], [], []
    for citekey in shared:
        a, b = left[citekey], right[citekey]
        if a["txt_sha"] != b["txt_sha"]:
            txt_diff.append(citekey)
        if a["sidecar_sha"] != b["sidecar_sha"]:
            sidecar_diff.append(citekey)
        if a["spans_sha"] != b["spans_sha"]:
            spans_diff.append({"citekey": citekey,
                               "n_spans": [a["n_spans"], b["n_spans"]]})
        if a["texts_sha"] != b["texts_sha"]:
            texts_diff.append({"citekey": citekey,
                               "n_spans": [a["n_spans"], b["n_spans"]]})
    return {
        "compared": len(shared),
        "only_in_left": sorted(set(left) - set(right)),
        "only_in_right": sorted(set(right) - set(left)),
        "txt_differ": txt_diff,
        "sidecar_bytes_differ": sidecar_diff,
        "spans_differ": spans_diff,
        "texts_differ": texts_diff,
    }


def self_check() -> None:
    """Prove the detector can see a difference before trusting it not to.

    The failure this guards against is specific and quiet: `compare()`
    tests `a["sidecar_sha"] != b["sidecar_sha"]`, and two *missing*
    sidecars are both None, so they compare equal. A run where Docling
    wrote no sidecars at all would therefore report "0 differ" -- exactly
    what a perfectly stable run reports. Publishing that as stability
    would be publishing an absence of data as a result.

    `bench/` sits outside CI's coverage targets (--cov=src --cov=scripts),
    so nothing in the test suite will ever catch a regression here. This
    runs on every invocation instead: it costs microseconds, and it means
    a printed zero has been earned rather than assumed.
    """
    present = {"k": {"txt_sha": "a", "sidecar_sha": "b", "spans_sha": "c",
                     "texts_sha": "d", "n_spans": 3}}
    changed = {"k": {"txt_sha": "a", "sidecar_sha": "z", "spans_sha": "z",
                     "texts_sha": "z", "n_spans": 4}}
    hit = compare(present, changed)
    assert len(hit["sidecar_bytes_differ"]) == 1, "detector missed a sidecar difference"
    assert len(hit["spans_differ"]) == 1, "detector missed a span difference"
    assert len(hit["texts_differ"]) == 1, "detector missed a text difference"
    assert not hit["txt_differ"], "detector invented a .txt difference"
    # A label flip on identical text: spans move, texts must not. This is
    # a real case, not a hypothetical -- see the module docstring.
    label_only = {"k": {"txt_sha": "a", "sidecar_sha": "b", "spans_sha": "SHIFTED",
                        "texts_sha": "d", "n_spans": 3}}
    flip = compare(present, label_only)
    assert len(flip["spans_differ"]) == 1, "detector missed a label flip"
    assert not flip["texts_differ"], "label flip wrongly reported as changed quotation"
    # The one that matters most. Two absent sidecars ARE equal, so
    # compare() reports zero differences and is right to -- there is
    # nothing to differ. The guard against mistaking that for stability
    # cannot live in compare() at all; it is require_sidecars(), and this
    # asserts *that*. An earlier version of this function asserted
    # `compare(absent, absent)["compared"] == 1` and called it the check,
    # which was vacuous: `compared` counts citekeys found by the .txt
    # glob and is 1 whether or not a single sidecar exists.
    absent = {"k": {"txt_sha": "a", "sidecar_sha": None, "spans_sha": None,
                    "texts_sha": None, "n_spans": None}}
    assert not compare(absent, absent)["sidecar_bytes_differ"]
    assert require_sidecars([{"name": "t", "fingerprint": absent}]), (
        "a run with no passage records must be flagged -- otherwise it "
        "reports exactly what a perfectly stable run reports"
    )
    assert require_sidecars([{"name": "t", "fingerprint": present}]) is None


def require_sidecars(runs: "list[dict]") -> "str | None":
    """Complain if any run produced a document without passage records.

    The comparison is only evidence about passage boundaries if there
    were passage boundaries to compare. Reported rather than raised: the
    `.txt` half of the result is still valid, and the caller should be
    told which half to believe.
    """
    for run in runs:
        missing = [k for k, v in run["fingerprint"].items() if not v["n_spans"]]
        if missing:
            return (f"{run['name']}: {len(missing)} of {len(run['fingerprint'])} "
                    f"document(s) have no passage records "
                    f"(e.g. {', '.join(missing[:3])}) -- the sidecar and span "
                    f"columns below are not evidence for those documents")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", type=int, required=True,
                    help="documents to draw from bench/corpus.json, by page rank")
    ap.add_argument("--keep-outliers", action="store_true",
                    help="do not exclude page-count outliers (Tukey Q3+1.5*IQR)")
    ap.add_argument("--outliers-only", action="store_true",
                    help="parse ONLY the excluded outliers -- the arm that checks "
                         "whether trimming removed the population the effect lives in")
    ap.add_argument("--workers", type=int, default=12,
                    help="held fixed across arms (default: 12)")
    ap.add_argument("--gpus", default="1,4",
                    help="comma-separated GPU counts -- the varied axis (default: 1,4)")
    ap.add_argument("--repeat", type=int, default=2,
                    help="runs per configuration; >1 gives the same-config control")
    ap.add_argument("--cpus", default=None,
                    help="taskset CPU list, e.g. 0-23,48-71 -- pins allowed_cpus()")
    ap.add_argument("--tag", required=True, help="names the output file")
    ap.add_argument("--python", default=".venv-full/bin/python")
    ap.add_argument("--keep", action="store_true",
                    help="keep every run's CONTENT_DIR (default: delete after fingerprinting)")
    ap.add_argument("--out", default=None, help="output directory (default: bench/results/<tag>)")
    args = ap.parse_args()

    self_check()

    gpu_counts = [int(g) for g in args.gpus.split(",")]
    out_dir = Path(args.out) if args.out else BENCH_DIR / "results" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = out_dir / "runs"
    work_root.mkdir(exist_ok=True)

    sample = build_sample(args.sample, args.keep_outliers, args.outliers_only,
                          out_dir / "sample.bib")
    print(f"  sample: {sample['documents']} docs, {sample['pages']} pages "
          f"({sample['pages_per_doc']}/doc, max {sample['max_pages']}), "
          f"{sample['outliers_excluded']} outlier(s) excluded"
          + (f" above {sample['outlier_fence_pages']} pages"
             if sample["outlier_fence_pages"] else ""), flush=True)
    bib_path = Path(sample["bib"])

    runs = []
    for gpus in gpu_counts:
        for rep in range(args.repeat):
            name = f"g{gpus}-r{rep}"
            content_dir = work_root / name
            if content_dir.exists():
                shutil.rmtree(content_dir)
            print(f"  running {name}: {args.workers} workers, {gpus} GPU(s) ...",
                  flush=True)
            record = run_once(bib_path, args.workers, gpus, args.cpus,
                              content_dir, args.python)
            record["name"] = name
            record["fingerprint"] = fingerprint(content_dir)
            runs.append(record)
            print(f"    {record['wall_seconds']}s, {record['parsed']} parsed, "
                  f"{record['workers_resolved']} workers resolved", flush=True)
            if not args.keep:
                shutil.rmtree(content_dir, ignore_errors=True)

    # Same-config pairs first, then across-config: the controls have to
    # read as clean before the headline comparison means anything.
    comparisons = []
    by_name = {r["name"]: r for r in runs}
    for gpus in gpu_counts:
        for rep in range(1, args.repeat):
            left, right = f"g{gpus}-r0", f"g{gpus}-r{rep}"
            comparisons.append({"kind": "same-config", "left": left, "right": right,
                                **compare(by_name[left]["fingerprint"],
                                          by_name[right]["fingerprint"])})
    for i, gpus in enumerate(gpu_counts):
        for other in gpu_counts[i + 1:]:
            left, right = f"g{gpus}-r0", f"g{other}-r0"
            comparisons.append({"kind": "across-config", "left": left, "right": right,
                                **compare(by_name[left]["fingerprint"],
                                          by_name[right]["fingerprint"])})

    # Computed before the record is written, and stored in it: a reader
    # coming back to repro.json months later needs to know the sidecar
    # columns were evidence, without re-deriving it from the fingerprints.
    complaint = require_sidecars(runs)
    payload = {
        "sample": sample,
        "workers": args.workers,
        "cpus_pinned": args.cpus,
        "gpu_counts": gpu_counts,
        "repeat": args.repeat,
        "sidecar_complaint": complaint,
        "runs": runs,
        "comparisons": comparisons,
    }
    record_path = out_dir / "repro.json"
    record_path.write_text(json.dumps(payload, indent=1))

    if complaint:
        print(f"\n  WARNING {complaint}")

    print(f"\n{'comparison':<28} {'docs':>5} {'.txt':>6} {'sidecar':>8} "
          f"{'spans':>6} {'texts':>6}")
    for c in comparisons:
        label = f"{c['kind']}: {c['left']}~{c['right']}"
        print(f"{label:<28} {c['compared']:>5} {len(c['txt_differ']):>6} "
              f"{len(c['sidecar_bytes_differ']):>8} {len(c['spans_differ']):>6} "
              f"{len(c['texts_differ']):>6}")
    print(f"\nRecord: {record_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Sweep the REAL `python -m src.sync` across worker/GPU/OCR settings.

This is the harness that produces trustworthy pool-level numbers, and it
exists because the two older tools cannot:

  bench_docling.py   times Docling directly, one process, no pool.
  run_parallel.py    launches N *independent* processes over G cards --
                     it predates `[parser].workers` and shares none of
                     the pool's machinery (no shared counter, no pool
                     initialiser, no start method).

Neither answers "what does the shipped pipeline cost", which is the only
question a user actually has. This runs the shipped pipeline.

Three things it does that a hand-rolled `time python -m src.sync` does not:

1. **A fresh CONTENT_DIR per run.** The ledger skips any PDF whose bytes
   haven't changed, so a second run over the same output directory times
   the skip logic rather than the parse. Every run here starts empty.

2. **Reports the *resolved* worker count, not the requested one.**
   `worker_ceiling()` clamps to `allowed_cpus // _CPUS_PER_DOCLING_WORKER`,
   so asking for 32 on a 48-CPU machine silently gives you 12. That clamp
   hid a measured 1.41x for an entire release: every run looked like it
   honoured the setting. When the two differ, this says so, loudly.

3. **Samples CPU and GPU during the run**, so a flat spot in the scaling
   curve can be attributed instead of guessed at. `/proc/stat` covers the
   *host's* CPUs, which on a container is not what the process may use --
   the reported percentage is against `len(os.sched_getaffinity(0))`.

Usage:
    python bench/sweep_sync.py --workers 1,4,8,12 --gpus 4 --tag scaling
    python bench/sweep_sync.py --workers 12 --gpus 1,2,4 --tag gpus
    python bench/sweep_sync.py --workers 12,24 --ocr on,off --tag ocr

Each run parses the whole corpus, so a serial pass over a few hundred
PDFs is tens of minutes. Budget accordingly; --dry-run prints the plan.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"


def allowed_cpus() -> int:
    getaffinity = getattr(os, "sched_getaffinity", None)
    return len(getaffinity(0)) if getaffinity else (os.cpu_count() or 1)


class ResourceSampler:
    """Mean CPU-busy and per-GPU utilisation across a run.

    A thread rather than a subprocess so it stops exactly when the run
    does, and so a failed run still yields whatever it sampled.
    """

    def __init__(self, interval: float = 3.0):
        self.interval = interval
        self._stop = threading.Event()
        self._cpu_samples: list[float] = []
        self._gpu_samples: list[list[int]] = []
        self._thread: threading.Thread | None = None

    @staticmethod
    def _cpu_totals() -> tuple[int, int] | None:
        try:
            first = Path("/proc/stat").read_text().splitlines()[0]
        except OSError:
            return None
        fields = [int(x) for x in first.split()[1:]]
        if len(fields) < 5:
            return None
        return sum(fields), fields[3] + fields[4]  # total, idle+iowait

    @staticmethod
    def _gpu_util() -> list[int]:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        if out.returncode != 0:
            return []
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]

    def _run(self) -> None:
        prev = self._cpu_totals()
        while not self._stop.wait(self.interval):
            cur = self._cpu_totals()
            if prev and cur and cur[0] > prev[0]:
                d_total, d_idle = cur[0] - prev[0], cur[1] - prev[1]
                self._cpu_samples.append(100.0 * (d_total - d_idle) / d_total)
            prev = cur
            gpu = self._gpu_util()
            if gpu:
                self._gpu_samples.append(gpu)

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)
        return False

    def summary(self) -> dict:
        host_cpus = os.cpu_count() or 1
        mean_host = (sum(self._cpu_samples) / len(self._cpu_samples)
                     if self._cpu_samples else None)
        # /proc/stat is host-wide; express it against what we may use.
        busy_cpus = mean_host * host_cpus / 100 if mean_host is not None else None
        per_gpu = None
        if self._gpu_samples:
            # Sample rows can be short: a driver hiccup, or
            # CUDA_VISIBLE_DEVICES changing what nvidia-smi reports. Take
            # the narrowest row rather than indexing off the first, so a
            # transient blip cannot IndexError away an otherwise good run.
            n = min(len(row) for row in self._gpu_samples)
            per_gpu = [round(sum(row[i] for row in self._gpu_samples) / len(self._gpu_samples), 1)
                       for i in range(n)]
        return {
            "cpu_busy_cores": round(busy_cpus, 1) if busy_cpus is not None else None,
            "cpu_pct_of_allowed": (round(100 * busy_cpus / allowed_cpus(), 1)
                                   if busy_cpus is not None else None),
            "gpu_util_mean_per_card": per_gpu,
            "samples": len(self._cpu_samples),
        }


def one_run(workers: int, gpus: int, ocr: bool, python: str,
            keep_output: bool = False) -> dict:
    """One full `src.sync` over the whole corpus, from an empty ledger."""
    content_dir = Path(tempfile.mkdtemp(prefix="bench-content-"))
    env = {
        **os.environ,
        "CONTENT_DIR": str(content_dir),
        "PARSER": "docling",
        "PARSER_OCR": "true" if ocr else "false",
        "PARSER_WORKERS": str(workers),
        "CUDA_VISIBLE_DEVICES": ",".join(str(i) for i in range(gpus)) if gpus else "",
        # A stall watchdog firing mid-benchmark would silently truncate
        # the run and report a fast, wrong number.
        "PARSER_STALL_TIMEOUT": "off",
    }
    try:
        with ResourceSampler() as sampler:
            started = time.perf_counter()
            proc = subprocess.run([python, "-m", "src.sync"], cwd=str(REPO_ROOT),
                                  env=env, capture_output=True, text=True)
            elapsed = time.perf_counter() - started
        out, err = proc.stdout, proc.stderr
        parsed = len(re.findall(r"^  parsed ", out, flags=re.M))
        # `sync` prints per-document failures to *stderr* and the totals to
        # stdout. Counting only stdout reported every run as clean, which
        # is the exact failure mode this harness exists to catch -- so
        # prefer the authoritative summary line and fall back to counting
        # across both streams.
        summary = re.search(r"Sync complete: \d+ parsed, .*?, (\d+) failed", out)
        failed = (int(summary.group(1)) if summary
                  else len(re.findall(r"^  FAILED ", out + err, flags=re.M)))
        m = re.search(r"parsing \d+ document\(s\) with (\d+) workers", out)
        resolved = int(m.group(1)) if m else (1 if workers == 1 else None)
        return {
            "record": "run", "workers_requested": workers,
            "workers_resolved": resolved, "gpus": gpus, "ocr": ocr,
            "seconds": round(elapsed, 1), "parsed": parsed, "failed": failed,
            "returncode": proc.returncode, **sampler.summary(),
        }
    finally:
        if not keep_output:
            shutil.rmtree(content_dir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", default="1,4,8,12",
                    help="comma-separated worker counts (default: 1,4,8,12)")
    ap.add_argument("--gpus", default="4", help="comma-separated GPU counts (default: 4)")
    ap.add_argument("--ocr", default="off", help="comma-separated: on,off (default: off)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per configuration; the median is reported (default: 1)")
    ap.add_argument("--tag", required=True, help="names the output file")
    ap.add_argument("--python", default=".venv-full/bin/python",
                    help="interpreter with the heavy group installed")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    if args.repeat < 1:
        ap.error(f"--repeat must be at least 1, got {args.repeat}")
    # Validated rather than coerced: an unrecognised token used to fall
    # through to "off", which silently runs a different benchmark than the
    # one asked for and reports it under the requested name.
    ON, OFF = {"on", "true", "1", "yes"}, {"off", "false", "0", "no"}
    ocrs = []
    for token in args.ocr.split(","):
        t = token.strip().lower()
        if t in ON:
            ocrs.append(True)
        elif t in OFF:
            ocrs.append(False)
        else:
            ap.error(f"--ocr: {token!r} is neither on nor off "
                     f"(accepted: {', '.join(sorted(ON | OFF))})")
    try:
        workers = [int(w) for w in args.workers.split(",")]
        gpus = [int(g) for g in args.gpus.split(",")]
    except ValueError as exc:
        ap.error(f"--workers and --gpus take comma-separated integers: {exc}")
    if any(w < 1 for w in workers):
        ap.error(f"--workers must all be at least 1, got {args.workers!r}")
    if any(g < 0 for g in gpus):
        ap.error(f"--gpus must all be non-negative, got {args.gpus!r}")
    plan = [(w, g, o) for o in ocrs for g in gpus for w in workers]

    print(f"{len(plan)} configuration(s) x {args.repeat} run(s); "
          f"each parses the whole corpus from an empty ledger.")
    print(f"machine: {allowed_cpus()} CPUs available to this process "
          f"(host reports {os.cpu_count()})\n")
    if args.dry_run:
        for w, g, o in plan:
            print(f"  workers={w} gpus={g} ocr={'on' if o else 'off'}")
        return 0

    python = args.python
    if not Path(python).exists() and not shutil.which(python):
        print(f"error: {python} not found -- run "
              f"`bash scripts/install_full_pipeline.sh python-deps` first", file=sys.stderr)
        return 2

    out_path = BENCH_DIR / "results" / f"sweep-{args.tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with out_path.open("w") as fh:
        for w, g, o in plan:
            runs = []
            for rep in range(args.repeat):
                rec = one_run(w, g, o, python)
                rec["repeat"] = rep
                runs.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                clamp = ""
                if rec["workers_resolved"] and rec["workers_resolved"] != w:
                    clamp = (f"  !! CLAMPED to {rec['workers_resolved']}"
                             f" -- see worker_ceiling()")
                status = "" if rec["returncode"] == 0 and rec["failed"] == 0 else \
                         f"  !! rc={rec['returncode']} failed={rec['failed']}"
                print(f"  workers={w:<3} gpus={g} ocr={'on ' if o else 'off'} "
                      f"{rec['seconds']:8.1f}s  parsed={rec['parsed']:<4}"
                      f"  cpu={rec['cpu_pct_of_allowed']}%{clamp}{status}")
            runs.sort(key=lambda r: r["seconds"])
            records.append(runs[len(runs) // 2])

    baseline = next((r for r in records
                     if r["workers_resolved"] == 1 and not r["ocr"]
                     and r["gpus"] == max(gpus)), None)
    print(f"\nwrote {out_path}")
    print(f"\n{'req':>4} {'got':>4} {'gpus':>4} {'ocr':>4} {'wall':>9} "
          f"{'speedup':>8} {'eff':>5} {'cpu%':>5}")
    for r in records:
        got = r["workers_resolved"] or "?"
        sp = eff = ""
        if baseline and isinstance(got, int) and got:
            sp = f"{baseline['seconds']/r['seconds']:7.2f}x"
            eff = f"{baseline['seconds']/r['seconds']/got:4.0%}"
        print(f"{r['workers_requested']:>4} {str(got):>4} {r['gpus']:>4} "
              f"{'on' if r['ocr'] else 'off':>4} {r['seconds']:8.1f}s {sp:>8} {eff:>5} "
              f"{str(r['cpu_pct_of_allowed']):>5}")
    if not baseline:
        print("\n(no 1-worker OCR-off run in this sweep, so no speedup column -- "
              "add `1` to --workers for a baseline)")
    clamped = [r for r in records
               if r["workers_resolved"] and r["workers_resolved"] != r["workers_requested"]]
    if clamped:
        print(f"\n!! {len(clamped)} configuration(s) were clamped below what was asked "
              f"for. Those rows measure the clamp, not the setting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

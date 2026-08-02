"""Measure aggregate Docling throughput with N worker processes.

A single Docling process runs the A40 at roughly 7% SM utilisation
(measured with `nvidia-smi dmon` during the serial run) while pulling
~2.5 CPU cores, so the interesting scaling question here is not "how
many GPUs" but "how many processes before the 48 cores run out".

Workers are assigned to GPUs round-robin via CUDA_VISIBLE_DEVICES.
Shards are built longest-PDF-first (LPT) so that page counts land
evenly and the wall clock reflects throughput rather than one worker
being handed the 675-page book.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv-full" / "bin" / "python")


def lpt_shards(items: list[dict], n: int) -> list[list[dict]]:
    shards: list[list[dict]] = [[] for _ in range(n)]
    load = [0] * n
    for item in sorted(items, key=lambda d: -d["pages"]):
        i = load.index(min(load))
        shards[i].append(item)
        load[i] += item["pages"]
    return shards


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--workers", type=int, required=True)
    ap.add_argument("--gpus", type=int, default=4)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--images", action="store_true")
    args = ap.parse_args()

    items = json.loads(Path(args.sample).read_text())
    shards = lpt_shards(items, args.workers)
    outdir = REPO / "bench" / f"par_{args.tag}"
    outdir.mkdir(parents=True, exist_ok=True)

    procs, logs = [], []
    t0 = time.perf_counter()
    for i, shard in enumerate(shards):
        shard_path = outdir / f"shard{i}.json"
        shard_path.write_text(json.dumps(shard))
        cmd = [PY, str(REPO / "bench" / "bench_docling.py"),
               "--sample", str(shard_path),
               "--out", str(outdir / f"w{i}.jsonl"),
               "--device", args.device, "--mode", "reused"]
        if args.images:
            cmd.append("--images")
        # Pinned for every GPU-capable device, not just the literal
        # "cuda": AcceleratorDevice.AUTO resolves to cuda:0 for *every*
        # process, so leaving all four cards visible under --device auto
        # would silently pile every worker onto GPU 0 -- the exact
        # failure this round-robin exists to prevent, and one that shows
        # up as bad throughput rather than as an error.
        env_gpu = "" if args.device == "cpu" else str(i % args.gpus)
        log = (outdir / f"w{i}.log").open("w")
        logs.append(log)
        procs.append(subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(REPO),
            env={**os.environ, "CUDA_VISIBLE_DEVICES": env_gpu}))
    # Exit codes are checked before any .jsonl is read. A worker that died
    # (Docling import failure, OOM kill, unreadable PDF) leaves a missing
    # or truncated file, and reading it first turns a clear "worker 3
    # died, see w3.log" into a confusing parse error several lines later.
    failed = [i for i, p in enumerate(procs) if p.wait() != 0]
    wall = time.perf_counter() - t0
    for log in logs:
        log.close()
    if failed:
        raise SystemExit(
            f"worker(s) {failed} exited non-zero -- see "
            + ", ".join(str(outdir / f'w{i}.log') for i in failed)
        )

    pages, docs, cold = 0, 0, []
    for i in range(args.workers):
        for line in (outdir / f"w{i}.jsonl").read_text().splitlines():
            rec = json.loads(line)
            if rec.get("record") == "meta":
                cold.append(rec["cold_start_s"])
            elif rec.get("ok"):
                pages += rec["pages"]
                docs += 1
    summary = {
        "tag": args.tag, "workers": args.workers, "gpus": args.gpus,
        "device": args.device, "images": args.images,
        "wall_s": round(wall, 1), "docs": docs, "pages": pages,
        "pages_per_s": round(pages / wall, 2),
        "s_per_page_effective": round(wall / pages, 3),
        "max_cold_start_s": max(cold) if cold else None,
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), file=sys.stderr)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()

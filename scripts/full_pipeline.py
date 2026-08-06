#!/usr/bin/env python3
"""Orchestrates the full enrichment layer:

    Docling -> sentence-transformers/Chroma -> BERTopic
    -> citation provenance -> Pandoc/LaTeX

One script for both the host and the Docker target (docker/Dockerfile) --
the two don't need separate implementations. Each stage probes its own
prerequisites (pandoc/pdflatex on PATH) and reports a real per-stage
status instead of assuming the target implies availability. On a plain
host that's missing TeX Live, some stages report
skipped/missing-binary -- that is a correct, honest result, not a
bug in this script.

Needs the venv populated by `poetry install --with heavy` (see
pyproject.toml, and .venv-full/ on the host this was developed on). The
corpus and drafting layers (python -m src.sync, src/citation_gate.py) do
not depend on any of this and are unaffected either way.

Usage:
    python scripts/full_pipeline.py --target host
    python scripts/full_pipeline.py --stages embed,bertopic
    python scripts/full_pipeline.py --stages render --input draft.md
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.heavy import corpus, docling_parse, embed_index, render_output, topic_model
from src import citation_provenance, config, runlock

STAGE_ORDER = ["docling", "embed", "bertopic", "provenance", "render"]


def stage_docling(docs, args):
    status = docling_parse.parse_corpus(docs)
    errors = {k: v for k, v in status.items() if v.startswith("error")}
    return {"status": "ok" if not errors else "partial", "detail": status}


def stage_embed(docs, args):
    return {"status": "ok", "detail": embed_index.build_index(docs)}


def stage_bertopic(docs, args):
    result = topic_model.run_topic_model(docs)
    return {"status": "ok", "detail": {"n_docs": result["n_docs"], "assignments": result["assignments"]}}


def stage_provenance(docs, args):
    if not args.input:
        return {"status": "skipped", "detail": "no --input given"}
    written = citation_provenance.write_report(Path(args.input), ["md", "tex", "pdf"])
    missing = [f for f in ("tex", "pdf") if f not in written]
    return {
        "status": "ok" if not missing else "partial",
        "detail": {fmt: str(path) for fmt, path in written.items()},
    }


def stage_render(docs, args):
    if not args.input:
        return {"status": "skipped", "detail": "no --input given"}
    try:
        return {"status": "ok", "detail": str(render_output.render(args.input, args.output_format, args.documentclass))}
    except render_output.MissingBinary as exc:
        return {"status": "missing-binary", "detail": str(exc)}


STAGE_FUNCS = {
    "docling": stage_docling,
    "embed": stage_embed,
    "bertopic": stage_bertopic,
    "provenance": stage_provenance,
    "render": stage_render,
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=["host", "docker"], default="host",
                         help="Informational only -- stages self-probe regardless of this flag.")
    parser.add_argument("--stages", default=",".join(STAGE_ORDER),
                         help=f"Comma-separated subset of: {','.join(STAGE_ORDER)}")
    parser.add_argument("--input", help="Input file for the render stage")
    parser.add_argument("--output-format", default="pdf", help="Output format for the render stage")
    parser.add_argument("--documentclass", default="article", help="LaTeX documentclass for the render stage (default: article)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Strip and drop blanks: "--stages 'embed, bertopic'" and a trailing
    # comma are both natural to type, and without this the first makes a
    # real stage look unknown (" bertopic" matches nothing) while the
    # second puts an empty name in the warning below.
    selected = {name.strip() for name in args.stages.split(",") if name.strip()}

    # An unrecognized stage name would otherwise be a silent no-op: the
    # loop below iterates STAGE_ORDER and skips anything not selected, so
    # nothing ever reports that the name went unused. Say so instead --
    # notably for a stage name this pipeline used to have and no longer does.
    unknown = sorted(selected - set(STAGE_ORDER))
    if unknown:
        print(f"WARNING: unknown stage(s) {', '.join(unknown)} -- known stages: {', '.join(STAGE_ORDER)}")

    # Same lock as `python -m src.sync`: this stage writes content/ too,
    # and sync's parsed-text writes are not atomic, so an enrichment run
    # overlapping a sync can read a half-written .txt. One lock rather
    # than two, because the unsafe overlap is any-writer-vs-any-writer,
    # not just sync-vs-sync.
    try:
        with runlock.pipeline_lock():
            return _run_stages(args, selected)
    except runlock.AlreadyRunning as exc:
        print(f"  {exc}")
        return runlock.EXIT_ALREADY_RUNNING


def _run_stages(args, selected) -> int:
    docs = corpus.build_corpus()
    n_bib = sum(1 for d in docs if d.source == "bib")
    n_source_pdfs = sum(1 for d in docs if d.source == "source-pdfs")
    print(f"Target: {args.target}")
    print(f"Corpus: {len(docs)} doc(s) -- {n_bib} from the bib file, {n_source_pdfs} from {config.SOURCE_PDFS_DIR}/")

    results = {}
    for name in STAGE_ORDER:
        if name not in selected:
            continue
        print(f"\n=== {name} ===")
        try:
            result = STAGE_FUNCS[name](docs, args)
        except Exception as exc:  # noqa: BLE001 -- a stage failing must not abort the run
            result = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
        results[name] = result
        detail = result["detail"]
        print(f"[{result['status']}] " + (json.dumps(detail, indent=2, default=str) if not isinstance(detail, str) else detail))

    print("\n=== Summary ===")
    for name in STAGE_ORDER:
        if name in results:
            print(f"  {name:10s} {results[name]['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Orchestrates the full heavy pipeline:

    Docling -> GROBID -> sentence-transformers/Chroma -> BERTopic
    -> Pandoc/LaTeX

One script for both the host and the Docker target (docker/Dockerfile) --
the two don't need separate implementations. Each stage probes its own
prerequisites (a reachable GROBID service, pandoc/pdflatex on PATH) and
reports a real per-stage status instead of assuming the target implies
availability. On a plain host that's missing Java/TeX Live, some stages
report skipped/missing-binary -- that is a correct, honest result, not a
bug in this script.

Needs the venv described in docker/requirements-full.txt (see also
.venv-full/ on the host this was developed on). The core pipeline
(python -m src.sync, src/citation_gate.py) does not depend on any of this
and is unaffected either way.

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

from src.heavy import corpus, docling_parse, embed_index, grobid_extract, render_output, topic_model
from src import config

STAGE_ORDER = ["docling", "grobid", "embed", "bertopic", "render"]


def stage_docling(docs, args):
    status = docling_parse.parse_corpus(docs)
    errors = {k: v for k, v in status.items() if v.startswith("error")}
    return {"status": "ok" if not errors else "partial", "detail": status}


def stage_grobid(docs, args):
    if not grobid_extract.is_available():
        return {
            "status": "skipped",
            "detail": f"GROBID not reachable at {config.GROBID_URL} -- start it (docker/setup.sh, or standalone on the host; see README)",
        }
    return {"status": "ok", "detail": grobid_extract.extract_corpus(docs)}


def stage_embed(docs, args):
    return {"status": "ok", "detail": embed_index.build_index(docs)}


def stage_bertopic(docs, args):
    result = topic_model.run_topic_model(docs)
    return {"status": "ok", "detail": {"n_docs": result["n_docs"], "assignments": result["assignments"]}}


def stage_render(docs, args):
    if not args.input:
        return {"status": "skipped", "detail": "no --input given"}
    try:
        return {"status": "ok", "detail": str(render_output.render(args.input, args.output_format))}
    except render_output.MissingBinary as exc:
        return {"status": "missing-binary", "detail": str(exc)}


STAGE_FUNCS = {
    "docling": stage_docling,
    "grobid": stage_grobid,
    "embed": stage_embed,
    "bertopic": stage_bertopic,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = set(args.stages.split(","))

    docs = corpus.build_corpus()
    n_bib = sum(1 for d in docs if d.source == "bib")
    n_source_pdfs = sum(1 for d in docs if d.source == "source-pdfs")
    print(f"Target: {args.target}")
    print(f"Corpus: {len(docs)} doc(s) -- {n_bib} from the bib file, {n_source_pdfs} from source-pdfs/")

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

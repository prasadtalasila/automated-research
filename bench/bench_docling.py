"""Measure Docling's wall-clock cost on this repo's own bib corpus.

The question this answers: how long does a *complete* docling parse of
every PDF in papers/bibliography.bib take, on this host's four A40s?

Two variables matter more than anything else, so both are switches here:

  --mode fresh    build a new DocumentConverter per PDF -- what
                  src/pdf_text.py:_extract_docling actually does today.
  --mode reused   build one converter and reuse it -- what
                  src/heavy/docling_parse.py would do with one hoisted
                  line. DocumentConverter.initialized_pipelines is an
                  *instance* attribute, so "fresh" re-initialises the
                  layout/table models on every single document.

  --device auto|cuda|cpu   Docling's AcceleratorDevice. AUTO is what an
                  unconfigured DocumentConverter() picks.

Per-PDF timings go to a JSONL file so the extrapolation in
bench/estimate.py can be re-run without re-measuring.
"""

import argparse
import json
import os
import time
from pathlib import Path

import pypdfium2 as pdfium


def build_converter(device: str, images: bool):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice(device))
    if images:
        opts.generate_picture_images = True
        opts.images_scale = 2.0
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True, help="JSON list of {citekey,path,pages}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--mode", default="reused", choices=["fresh", "reused"])
    ap.add_argument("--images", action="store_true", help="match config.toml's docling_images=true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    sample = json.loads(Path(args.sample).read_text())
    if args.limit:
        sample = sample[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = out.open("w")

    # Cold-start cost of standing the models up at all -- paid once in
    # "reused", once per document in "fresh". Timed by converting a
    # throwaway 1-page PDF, since DocumentConverter defers all model
    # loading to the first convert() call.
    warm_pdf = sample[0]["path"]
    t0 = time.perf_counter()
    conv = build_converter(args.device, args.images)
    conv.convert(warm_pdf)
    init_s = time.perf_counter() - t0
    meta = {
        "record": "meta",
        "device": args.device,
        "mode": args.mode,
        "images": args.images,
        "cold_start_s": round(init_s, 2),
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "n": len(sample),
    }
    fh.write(json.dumps(meta) + "\n")
    fh.flush()
    print(json.dumps(meta), flush=True)

    for item in sample:
        if args.mode == "fresh":
            t0 = time.perf_counter()
            conv = build_converter(args.device, args.images)
        else:
            t0 = time.perf_counter()
        try:
            doc = conv.convert(item["path"]).document
            md = doc.export_to_markdown()
            elapsed = time.perf_counter() - t0
            rec = {
                "record": "pdf",
                "citekey": item["citekey"],
                "pages": item["pages"],
                "bytes": item.get("bytes"),
                "seconds": round(elapsed, 3),
                "s_per_page": round(elapsed / item["pages"], 3) if item["pages"] else None,
                "md_chars": len(md),
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001 -- one bad PDF must not end the run
            rec = {
                "record": "pdf",
                "citekey": item["citekey"],
                "pages": item["pages"],
                "seconds": round(time.perf_counter() - t0, 3),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
        print(json.dumps(rec), flush=True)

    fh.close()


if __name__ == "__main__":
    main()

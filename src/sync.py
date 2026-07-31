"""Deterministic pipeline entrypoint: bib file -> ledger -> parsed text.

Safe to run unattended / on a schedule (idempotent, incremental):
    python -m src.sync

This is "job 1" of the two-job split: no generation, no LLM calls, just
bringing the shared content layer up to date with the bibliography (see
src/bib_reader.py -- the BibTeX-exported .bib file is the source of
truth for citekeys, not something this pipeline generates). Genre-specific
drafting (job 2) is invoked separately, on demand, via the Claude Code
skills in .claude/skills/.

Needs `bibtexparser` installed -- run scripts/install_full_pipeline.sh
first (creates .venv-full/ on a bare host), then run this via that
venv's python. python -m src.citation_gate does not need it and still
runs with the bare system interpreter.
"""

import subprocess
import sys

from src import bib_reader, config, dedup, ledger, pdf_text


def run() -> int:
    print(f"Reading bibliography from {config.BIB_FILE_PATH} ...")
    references = bib_reader.read_library()
    print(f"  found {len(references)} bibliographic item(s)")

    incomplete = [r for r in references if not r.authors]
    if incomplete:
        print(f"  WARNING: {len(incomplete)} item(s) have no author metadata in the bib file "
              f"(likely a page saved as 'webpage' rather than proper item type) -- "
              f"citing them will produce a low-quality reference:")
        for ref in incomplete:
            print(f"    {ref.citekey}: {ref.title[:80]!r}")
        print("  Fix the item type/metadata in your reference manager, re-export, and re-run sync.")

    duplicate_groups = dedup.find_duplicates(references)
    if duplicate_groups:
        print(f"  WARNING: {len(duplicate_groups)} possible duplicate group(s) -- same DOI or "
              f"near-identical title under different citekeys. A shared title doesn't always "
              f"mean the same source (e.g. a blog post and a webinar about the same named "
              f"report) -- check by hand before merging or removing either citekey:")
        for group in duplicate_groups:
            citekeys = " / ".join(ref.citekey for ref in group)
            print(f"    {citekeys}: {group[0].title[:80]!r}")

    con = ledger.connect()
    parsed, failed, skipped, no_pdf = 0, 0, 0, 0
    try:
        for ref in references:
            needs_parse = ledger.upsert_reference(con, ref)
            if not ref.pdf_path:
                no_pdf += 1
                continue
            if not needs_parse:
                skipped += 1
                continue
            try:
                out_path = pdf_text.extract_text(ref.pdf_path, ref.citekey)
                ledger.mark_parsed(con, ref.citekey, out_path)
                parsed += 1
                print(f"  parsed  {ref.citekey}")
            except subprocess.CalledProcessError as exc:
                ledger.mark_parse_failed(con, ref.citekey, exc.stderr or str(exc))
                failed += 1
                print(f"  FAILED  {ref.citekey}: {exc.stderr}", file=sys.stderr)
    finally:
        con.close()

    print(
        f"Sync complete: {parsed} parsed, {skipped} unchanged, "
        f"{no_pdf} without a PDF attachment, {failed} failed."
    )
    print(f"Ledger:      {config.LEDGER_PATH}")
    print(f"Parsed text: {config.PARSED_DIR}/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())

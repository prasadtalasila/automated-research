"""Deterministic pipeline entrypoint: Zotero -> ledger -> parsed text -> library.bib.

Safe to run unattended / on a schedule (idempotent, incremental):
    python -m src.sync

This is "job 1" of the two-job split: no generation, no LLM calls, just
bringing the shared content layer up to date with the Zotero library.
Genre-specific drafting (job 2) is invoked separately, on demand, via
the Claude Code skills in .claude/skills/.
"""

import subprocess
import sys

from src import config, ledger, pdf_text, zotero_reader


def run() -> int:
    print(f"Reading Zotero library from {config.ZOTERO_DATA_DIR} ...")
    references = zotero_reader.read_library()
    print(f"  found {len(references)} bibliographic item(s)")

    incomplete = [r for r in references if not r.authors]
    if incomplete:
        print(f"  WARNING: {len(incomplete)} item(s) have no author metadata in Zotero "
              f"(likely a page saved as 'webpage' rather than proper item type) -- "
              f"citing them will produce a low-quality @misc entry:")
        for ref in incomplete:
            print(f"    {ref.citekey}: {ref.title[:80]!r}")
        print("  Fix the item type/metadata in Zotero for a citable reference.")

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

        zotero_reader.write_library_bib(references)
    finally:
        con.close()

    print(
        f"Sync complete: {parsed} parsed, {skipped} unchanged, "
        f"{no_pdf} without a PDF attachment, {failed} failed."
    )
    print(f"Ledger:       {config.LEDGER_PATH}")
    print(f"Bibliography: {config.LIBRARY_BIB_PATH}")
    print(f"Parsed text:  {config.PARSED_DIR}/")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())

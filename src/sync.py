"""Deterministic pipeline entrypoint: bib file -> ledger -> parsed text.

Safe to run unattended / on a schedule (idempotent, incremental):
    python -m src.sync

A citekey that drops out of the bib file is only *reported* by default --
pass --remove-stale to actually delete its content/ledger.sqlite row (see
"Removing a paper" in README.md and src/ledger.py's find_stale/prune_missing).

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

import argparse
import sys
from collections import Counter

from src import bib_reader, config, dedup, ledger, pdf_text


def run(remove_stale: bool = False) -> int:
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

    parser_available = pdf_text.is_available()
    if not parser_available:
        print(
            f"  WARNING: {pdf_text.unavailable_reason()} PDF text extraction will be "
            "skipped for every item that needs it this run. Bibliographic metadata is "
            "still synced to the ledger."
        )

    con = ledger.connect()
    parsed, failed, skipped, no_pdf, backend_unavailable = 0, 0, 0, 0, 0
    no_pdf_reasons: Counter[str] = Counter()
    pruned: list[tuple[str, str | None]] = []
    stale: list[tuple[str, str | None]] = []
    suspicious = False
    try:
        # This loop -- including pdf_text.extract_text's backend call
        # (pdftotext/markitdown/docling, per config.PARSER) -- runs
        # serially, one reference at a time, even though a large corpus
        # (454 PDFs/1.37GB at one audit) means a first-time or bulk sync
        # can have a lot of documents to parse in a single run.
        # Deliberately not parallelized (ProcessPoolExecutor was the
        # candidate) for two reasons: (1) src/ledger.py's (size,
        # mtime)-before-hash skip means a routine, non-bulk sync already
        # parses zero-to-few documents per run -- the case parallelism
        # would help doesn't come up often; (2) with the default
        # pdftotext backend specifically, pdftotext is already an
        # external subprocess (releases the GIL while it runs), so a
        # ProcessPoolExecutor would add pickling/IPC overhead to buy the
        # same OS-level concurrency a plain ThreadPoolExecutor gets for
        # free. Reason (2) doesn't hold for markitdown/docling (both run
        # in-process, holding the GIL) -- but reason (1) does, for all
        # three backends equally, which is why this is still deferred
        # rather than backend-conditional. If a bulk/first-run sync's
        # wall-clock time becomes a real problem, revisit with a
        # ThreadPoolExecutor around this loop's pdf_text.extract_text
        # call specifically -- keeping every ledger.mark_parsed/
        # mark_parse_failed call on the main thread as futures complete,
        # since a sqlite3 connection isn't safe to share across threads.
        for ref in references:
            needs_parse = ledger.upsert_reference(con, ref)
            if not ref.pdf_path:
                no_pdf += 1
                no_pdf_reasons[ref.pdf_resolution] += 1
                label = bib_reader.PDF_RESOLUTION_LABELS[ref.pdf_resolution]
                print(f"  no-pdf  {ref.citekey}: {label}")
                continue
            if not needs_parse:
                skipped += 1
                continue
            if not parser_available:
                backend_unavailable += 1
                continue
            try:
                out_path = pdf_text.extract_text(ref.pdf_path, ref.citekey)
                ledger.mark_parsed(con, ref.citekey, out_path)
                parsed += 1
                print(f"  parsed  {ref.citekey}")
            except pdf_text.ExtractionError as exc:
                ledger.mark_parse_failed(con, ref.citekey, str(exc))
                failed += 1
                print(f"  FAILED  {ref.citekey}: {exc}", file=sys.stderr)
            except pdf_text.BackendUnavailable:
                # The up-front probe passed, but the backend vanished
                # (pdftotext dropped from PATH, or the markitdown/docling
                # package became uninstallable) between then and this
                # specific item -- count and report it the same as the
                # up-front case instead of letting it crash sync
                # uncaught, which is exactly the failure mode probing
                # exists to prevent.
                backend_unavailable += 1
                print(
                    f"  no-{config.PARSER}  {ref.citekey}: {config.PARSER} backend no longer available",
                    file=sys.stderr,
                )
        # Only the ledger row is removed -- see prune_missing's own
        # docstring for why the corresponding content/parsed/<citekey>.txt
        # is deliberately left in place. Deletion only happens with
        # --remove-stale (default off): a bib file that comes back
        # short a citekey is far more often a mistake (a botched
        # re-export, BIB_FILE pointing at the wrong path) than an
        # intentional removal, so the default is to report it and let a
        # human confirm rather than delete on every routine sync.
        seen_citekeys = {r.citekey for r in references}
        if remove_stale:
            pruned = ledger.prune_missing(con, seen_citekeys)
            for citekey, _parsed_path in pruned:
                print(f"  pruned  {citekey} (no longer in {config.BIB_FILE_PATH.name})")
        else:
            stale = ledger.find_stale(con, seen_citekeys)
            suspicious = not seen_citekeys and bool(stale)
            if suspicious:
                # Same shape prune_missing's guard refuses on -- don't
                # tell the user to run a command that's just going to
                # raise. references came back completely empty against a
                # non-empty ledger, so this is far more likely a botched
                # re-export or BIB_FILE pointing at the wrong path than
                # every citekey being legitimately removed at once.
                print(
                    f"  SUSPICIOUS: the bib file yielded 0 references, so all "
                    f"{len(stale)} ledger item(s) show as stale. This usually "
                    f"means the bib file is empty, corrupted, or BIB_FILE is "
                    f"misconfigured -- not that every citekey was actually "
                    f"removed. Fix the export/path and re-run sync rather than "
                    f"passing --remove-stale (which would refuse and raise on "
                    f"this exact shape)."
                )
            else:
                # The "pass --remove-stale" instruction is printed once,
                # in the summary line below, rather than repeated on every
                # item here -- a bib file truncated from 200 entries to 3
                # survivors would otherwise print that instruction 197
                # times, which reads as routine per-item noise rather than
                # the "review this list before deleting" signal it's
                # meant to be.
                for citekey, _parsed_path in stale:
                    print(f"  stale   {citekey} (no longer in {config.BIB_FILE_PATH.name})")
    finally:
        con.close()

    stale_count = len(pruned) if remove_stale else len(stale)
    stale_label = "pruned" if remove_stale else "stale (not removed)"
    summary = (
        f"Sync complete: {parsed} parsed, {skipped} unchanged, "
        f"{no_pdf} without a PDF attachment, {failed} failed, {stale_count} {stale_label}."
    )
    if backend_unavailable:
        summary += f" {backend_unavailable} skipped ({config.PARSER} not installed)."
    print(summary)
    if no_pdf_reasons:
        # Least-churn fix for the masking this bucket used to cause: the
        # aggregate "N without a PDF attachment" count above is unchanged
        # (existing callers/tests depend on that exact wording), but an
        # audit no longer has to guess whether that N is "never had a
        # PDF" (routine) or "PDF path silently went missing"/"only an
        # HTML snapshot, invisible to retrieval" (both worth fixing).
        breakdown = ", ".join(
            f"{no_pdf_reasons[reason]} {label}"
            for reason, label in bib_reader.PDF_RESOLUTION_LABELS.items()
            if no_pdf_reasons[reason]
        )
        print(f"  no-PDF breakdown: {breakdown}")
    if stale_count and not remove_stale and not suspicious:
        print(f"Review the {stale_count} stale item(s) above, then re-run with "
              "--remove-stale to delete them from the ledger.")
    print(f"Ledger:      {config.LEDGER_PATH}")
    print(f"Parsed text: {config.PARSED_DIR}/")
    return 1 if failed or backend_unavailable else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync content/ledger.sqlite from the bib file (job 1 -- deterministic pipeline)."
    )
    parser.add_argument(
        "--remove-stale", action="store_true",
        help="Delete ledger rows for citekeys no longer in the bib file (default: report only, don't delete)",
    )
    args = parser.parse_args()
    raise SystemExit(run(remove_stale=args.remove_stale))

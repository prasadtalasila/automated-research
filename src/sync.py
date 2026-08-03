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
import multiprocessing
import os
import sys
from collections import Counter
from concurrent.futures import (FIRST_COMPLETED, ProcessPoolExecutor,
                                ThreadPoolExecutor, wait)
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from src import bib_reader, config, dedup, ledger, pdf_text


def _executor_for(workers: int):
    """Processes for docling, threads for pdftotext.

    The two backends want opposite things, so this is deliberately
    backend-conditional rather than one pool type for both. `pdftotext`
    is an external subprocess that releases the GIL while it runs, so a
    ThreadPoolExecutor already gets full OS-level concurrency and a
    process pool would only add pickling and spawn cost on top. `docling`
    runs in-process and holds the GIL, so threads would serialise exactly
    the work we are trying to overlap.

    The docling pool also claims one GPU per worker. Docling's
    `AcceleratorDevice.AUTO` resolves to `cuda:0` in *every* process, so
    without this every worker contends for one card while the rest of the
    machine's GPUs idle -- measured at 12 workers: GPU 0 pinned at 100%,
    GPUs 1-3 at 0%, and no faster than 4 workers. The index is handed out
    by a shared counter under a lock, because a pool creates its workers
    lazily and numbers none of them.

    "spawn", not the Linux default "fork": counting GPUs initialises CUDA
    in this parent process, and a forked child inherits a broken CUDA
    context from a parent that has. Each worker reloads Docling's models
    anyway, so spawn's extra startup is noise against that.

    Also the seam the tests substitute: a real ProcessPoolExecutor runs
    its work in a child interpreter, where the test process's
    monkeypatches don't exist.
    """
    if config.PARSER == "docling":
        ctx = multiprocessing.get_context("spawn")
        return ProcessPoolExecutor(
            max_workers=workers,
            mp_context=ctx,
            initializer=pdf_text.init_worker,
            initargs=(ctx.Value("i", 0), ctx.Lock(), pdf_text.gpu_count()),
        )
    return ThreadPoolExecutor(max_workers=workers)


def _pdf_size(path: str) -> int:
    """Bytes, or 0 if the file can't be stat'd.

    Only ever used to sort work biggest-first, so a file that vanished
    between bib resolution and here just sorts last -- the parse will
    report the real error a moment later, which is the better place for
    it.
    """
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _as_they_land(futures, executor, stalled):
    """Yield futures as they complete, giving up if the whole pool goes
    silent for config.PARSER_STALL_TIMEOUT.

    `wait(..., FIRST_COMPLETED)` rather than `as_completed(timeout=...)`:
    as_completed measures its timeout from the original call, i.e. total
    elapsed, so on a long corpus it would fire on a perfectly healthy
    run. What is wanted is the gap *between* completions -- with several
    workers those arrive constantly, so silence across the entire pool is
    what distinguishes a hung worker from a merely slow document. That
    distinction matters because the slowest legitimate document in this
    corpus takes 246s, and no per-document deadline can be both above
    that and a useful hang detector.

    The workers are terminated on the way out, not merely abandoned.
    Without that, in-flight jobs keep running and write
    content/parsed/<citekey>.txt for documents this run has already
    reported as failed -- a file on disk contradicting the ledger -- and
    the processes stay alive holding GPU memory.

    Giving up here is not a data loss: the caller reports the
    unfinished documents as failures, and since v1.2.0 a failed document
    is retried on the next run rather than dropped.
    """
    pending = set(futures)
    while pending:
        done, pending = wait(pending, timeout=config.PARSER_STALL_TIMEOUT,
                             return_when=FIRST_COMPLETED)
        if not done:
            stalled.append(True)
            pdf_text.terminate_workers(executor)
            print(f"  WARNING no document finished in "
                  f"{config.PARSER_STALL_TIMEOUT}s ([parser].stall_timeout) -- "
                  f"giving up on the {len(pending)} still outstanding. They are "
                  "reported as failures below and retried on the next run.",
                  file=sys.stderr)
            return
        yield from done


def _parse_serial(refs):
    """The historical path, taken whenever [parser].workers resolves to 1.

    Deliberately not "a pool with one worker": no executor, no pickling,
    no subprocess, and pdf_text.extract_text is called with exactly the
    arguments it always was.
    """
    for ref in refs:
        try:
            yield ref.citekey, str(pdf_text.extract_text(ref.pdf_path, ref.citekey)), None
        except (pdf_text.ExtractionError, pdf_text.BackendUnavailable) as exc:
            yield ref.citekey, None, exc


def _parse_parallel(refs, workers: int, threads: int | None):
    """Same triples as _parse_serial, produced by `workers` at once.

    Submitted biggest-file-first (the LPT heuristic). One 675-page
    document in this project's own corpus is 5% of all its pages; picked
    up last it would define the wall clock single-handedly. File size
    rather than page count on purpose -- counting pages needs a PDF
    library, and the core pipeline deliberately has no such dependency.
    """
    jobs = [(r.pdf_path, r.citekey, threads)
            for r in sorted(refs, key=lambda r: -_pdf_size(r.pdf_path))]
    results = {}
    broken = None
    stalled = []
    # submit() plus _as_they_land() rather than map(): map yields in *input*
    # order, so a pool that breaks while the first (largest) job is still
    # running would raise before yielding the smaller jobs that had
    # already finished, throwing away real work and reporting parsed
    # documents as failures. _as_they_land records each result at the
    # moment it lands, so a broken pool costs only what was actually in
    # flight.
    # Not `with _executor_for(...)`: the context manager's __exit__ calls
    # shutdown(wait=True), and every job is submitted up front, so a
    # KeyboardInterrupt would drain the *entire* remaining queue before
    # exiting. Reported from real use on a 501-document corpus -- Ctrl+C
    # "took forever to exit" and emitted docling teardown tracebacks from
    # workers still being fed. Shutdown is therefore explicit below, with
    # cancel_futures on the interrupt path.
    executor = _executor_for(workers)
    done = 0
    try:
        with pdf_text.interrupt_guard(
            executor, lambda: f"{done}/{len(jobs)} document(s) parsed"
        ):
            futures = [executor.submit(pdf_text.extract_one, job) for job in jobs]
            for future in _as_they_land(futures, executor, stalled):
                try:
                    citekey, out_path, exc = future.result()
                except BrokenProcessPool as pool_exc:
                    broken = pool_exc
                    continue
                results[citekey] = (out_path, exc)
                done += 1
                # Live progress, on stderr so stdout stays in
                # bibliography order and diffable between runs. Without
                # it a parallel run over a real corpus prints nothing for
                # tens of minutes, which is indistinguishable from being
                # stuck -- especially under docling's own OCR chatter.
                print(f"  [{done}/{len(jobs)}] {citekey}", file=sys.stderr)
    except BrokenProcessPool as pool_exc:
        # submit() itself raises once the pool is already known-broken.
        broken = pool_exc
    except KeyboardInterrupt:
        # cancel_futures drops everything not yet started; wait=False
        # means we don't block on the handful still running. Whatever
        # finished is still recorded by the caller, so an interrupted run
        # keeps its work rather than discarding it.
        executor.shutdown(wait=False, cancel_futures=True)
        pdf_text.terminate_workers(executor)
        print(f"\n  interrupted after {done}/{len(jobs)} document(s) -- "
              "work already finished is kept; re-run to continue.",
              file=sys.stderr)
        raise
    finally:
        executor.shutdown(wait=False)
    if broken is not None:
        # A worker killed outright (the OOM killer is the realistic
        # cause) takes the whole pool with it, and every future still in
        # flight. Reported against the documents that didn't get parsed
        # rather than raised, so the run still writes its ledger updates,
        # its summary, and a nonzero exit code.
        print(f"  WARNING a parse worker died ({broken}) -- the documents it "
              "had not finished are reported as failures below. A lower "
              "[parser].workers is the usual fix.", file=sys.stderr)
    unfinished = ("gave up waiting: no document finished within "
                  f"{config.PARSER_STALL_TIMEOUT}s ([parser].stall_timeout)"
                  if stalled else
                  "parse worker died before this document was parsed")
    for ref in refs:
        if ref.citekey not in results:
            results[ref.citekey] = (None, pdf_text.ExtractionError(unfinished))
    return ((ref.citekey, *results[ref.citekey]) for ref in refs)


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
            f"  WARNING: {pdf_text.unavailable_reason()} Parsing will be skipped "
            "for every item that needs it this run. Bibliographic metadata is "
            "still synced to the ledger."
        )

    con = ledger.connect()
    parsed, failed, skipped, no_pdf, backend_unavailable = 0, 0, 0, 0, 0
    low_quality: list[str] = []
    no_pdf_reasons: Counter[str] = Counter()
    pruned: list[tuple[str, str | None]] = []
    stale: list[tuple[str, str | None]] = []
    suspicious = False
    try:
        # Split into "decide" and "parse" rather than one loop doing both.
        # Every ledger call stays here, on the main thread, because a
        # sqlite3 connection is not safe to share across threads and
        # sqlite has a single writer regardless -- only the backend call
        # (pdftotext/docling, per config.PARSER) is ever handed to a pool.
        #
        # Whether there is a pool at all is [parser].workers, which
        # defaults to 1: a routine sync parses zero-to-few documents
        # (src/ledger.py's (size, mtime)-before-hash skip), so paying pool
        # setup by default would cost more than it saves. It is a bulk or
        # first-time sync that needs this -- 501 PDFs at one audit, ~39
        # minutes serial with docling -- and that case is opt-in.
        to_parse = []
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
            to_parse.append(ref)

        workers, complaint = pdf_text.resolve_workers(len(to_parse))
        if complaint:
            print(complaint, file=sys.stderr)
        if workers > 1:
            print(f"  parsing {len(to_parse)} document(s) with {workers} workers")
            results = _parse_parallel(to_parse, workers, pdf_text.docling_threads(workers))
        else:
            results = _parse_serial(to_parse)

        # Applied in bib order, not completion order: futures finish in
        # whatever order they finish, and letting that reach stdout would
        # make two identical runs print differently and stop anyone
        # diffing them.
        for ref, (citekey, out_path, exc) in zip(to_parse, results):
            if exc is None:
                out_path = Path(out_path)
                ledger.mark_parsed(con, citekey, out_path)
                parsed += 1
                print(f"  parsed  {citekey}")
                # Reported per document rather than only in the summary:
                # the fix is usually per document (a scan, an unusual
                # font) or global (the wrong backend), and seeing which
                # citekeys trip it is what tells the two apart.
                warning = pdf_text.quality_warning(
                    out_path.read_text(encoding="utf-8", errors="replace")
                )
                if warning:
                    low_quality.append(citekey)
                    print(f"  WARNING {citekey}: {warning}", file=sys.stderr)
            elif isinstance(exc, pdf_text.BackendUnavailable):
                # The up-front probe passed, but the backend vanished
                # (pdftotext dropped from PATH, or the docling
                # package became uninstallable) between then and this
                # specific item -- count and report it the same as the
                # up-front case instead of letting it crash sync
                # uncaught, which is exactly the failure mode probing
                # exists to prevent. str(exc) carries the same actionable
                # install hint as the up-front WARNING (both come from
                # pdf_text.unavailable_reason()), not just "unavailable".
                backend_unavailable += 1
                print(f"  no-{config.PARSER}  {citekey}: {exc}", file=sys.stderr)
            else:
                ledger.mark_parse_failed(con, citekey, str(exc))
                failed += 1
                print(f"  FAILED  {citekey}: {exc}", file=sys.stderr)
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
        summary += f" {backend_unavailable} skipped ({config.PARSER} unavailable)."
    print(summary)
    if low_quality:
        # Named in full rather than counted: a handful of citekeys points
        # at those documents, while most of the corpus tripping it points
        # at the backend, and the list is what distinguishes the two.
        print(
            f"  WARNING: {len(low_quality)} document(s) look like the parser lost "
            f"word boundaries: {', '.join(low_quality)}. See config.toml's "
            f"[parser] quality-guard settings and docs/PDF-PARSER.md."
        )
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

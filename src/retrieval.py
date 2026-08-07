"""BM25-ranked keyword retrieval over the shared corpus layer.

This is the default retrieval implementation genre skills call against
(AGENTS.md's "Retrieval" section) -- stdlib-only, no venv or model
download needed. `src/enrich/embed_index.py` (sentence-transformers +
Chroma/Qdrant) is a verified, working embedding-based upgrade path with
a matching `search(query, k)` shape, ready to swap in without changing
callers once BM25 stops being enough for this corpus -- that's a
deliberate call to make when it comes up, not a threshold this module
should assert a number for. It is a *replacement*, not a complement:
nothing here fuses or re-ranks the two, and a caller uses one or the
other (docs/RETRIEVAL.md).

Two boundaries worth knowing, because they're easy to assume otherwise.
This module reads the ledger's `parsed_path` -- `content/parsed/*.txt` --
and never `content/docling/`, so running the enrichment layer's Docling
stage does not change what BM25 ranks or what its snippets say; only `[parser].backend`
does. And nothing in `scripts/enrich.py` imports this module, so
the enrichment layer neither uses nor updates this index.

Ranking is Okapi BM25 (stdlib-only: no rank_bm25 dependency), not raw
term-frequency -- term-frequency alone has no document-length
normalization, so a long document only needs to accumulate more raw
hits than a short one to outrank it, regardless of how small a
fraction of the long document those hits represent.

Scale: a naive implementation re-reads and re-tokenizes every
document's parsed text from disk on every call, which grows linearly
with corpus size and with each document's length. Term-frequency stats
per document are cached to disk (config.RETRIEVAL_INDEX_PATH), keyed by
a cheap per-item fingerprint (parsed-file stat -- exists/size/mtime, not
content), so a call only re-tokenizes documents whose text actually
changed since the last run -- mirroring src/ledger.py's own
stat-before-hash skip logic and src/enrich/embed_index.py's embedding
cache. Building a snippet for the returned top-k still reads those
(bounded, small) documents' text fresh, since a snippet needs the real
surrounding text, not just term counts.
"""

import json
import math
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from src import config, ledger

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
    "is", "are", "be", "this", "that", "as", "by", "from", "at",
}

# Standard Okapi BM25 constants (term-frequency saturation and length
# normalization strength) -- the usual defaults, not tuned against this
# corpus specifically.
_K1 = 1.5
_B = 0.75

_INDEX_SCHEMA_VERSION = 1


@dataclass
class SearchResult:
    citekey: str
    title: str
    score: float
    snippet: str


def _tokenize(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) > 2 and w not in _STOPWORDS
    ]


# Occurrences of one query term that `_windows` will anchor a candidate
# window on before it stops looking for more of that term. A ceiling on
# work for a pathological document, not a quality knob: 500 anchors of one
# term already spread across the whole text, and the top few windows come
# out of scoring, not out of how many candidates were offered.
_MAX_ANCHORS_PER_TERM = 500


def _windows(text: str, terms: set[str], width: int, count: int) -> list[str]:
    """The `count` best-matching windows of `text`, in document order.

    Scored by how many *distinct* query terms fall inside, not by raw hit
    count, so a passage repeating one word doesn't outrank one that
    actually covers the query. Candidate windows are anchored on every
    occurrence of every term and then de-overlapped, so a passage from
    late in a long document is reachable.

    Deterministic, which matters more than it looks. `terms` is a set, and
    string hashing is randomised per process, so anything that depends on
    the order those terms come out in gives a different answer run to run.
    Nothing here does: anchors are sorted before scoring, the score is a
    count over the whole term set, and ties break on position.
    """
    lower = text.lower()
    anchors: list[int] = []
    for term in terms:
        start = lower.find(term)
        found = 0
        # Bounded per term rather than across all of them, so a book-length
        # document that says "twin" ten thousand times cannot crowd out
        # every anchor for "greenhouse". Scoring rewards distinct-term
        # coverage, so losing a term's anchors entirely would work directly
        # against what the window is chosen for -- and a shared budget
        # would pick its victim by set order, i.e. at random.
        while start != -1 and found < _MAX_ANCHORS_PER_TERM:
            anchors.append(start)
            found += 1
            start = lower.find(term, start + 1)
    if not anchors:
        return []

    scored: list[tuple[int, int, int]] = []
    half = width // 2
    for anchor in sorted(set(anchors)):
        begin = max(0, anchor - half)
        end = min(len(text), begin + width)
        window = lower[begin:end]
        hits = sum(1 for term in terms if term in window)
        scored.append((hits, begin, end))

    chosen: list[tuple[int, int]] = []
    for _, begin, end in sorted(scored, key=lambda item: (-item[0], item[1])):
        if any(begin < other_end and end > other_begin for other_begin, other_end in chosen):
            continue
        chosen.append((begin, end))
        if len(chosen) == count:
            break
    return [" ".join(text[begin:end].split()) for begin, end in sorted(chosen)]


def _snippet(text: str, terms: set[str], window: int = 500) -> str:
    """The single best `window` characters of `text` for `terms`.

    This used to return the window around the *first* occurrence of
    whichever term came out of the `terms` set first -- and since string
    hashing is randomised per process, that made the same query on the
    same document return a different snippet run to run. Harmless-ish at
    a 500-character window, where you get enough context either way, and
    not harmless at all at the short windows an earlier version of this
    module rejected candidates on -- an irreproducible snippet there meant
    an irreproducible rejection (docs/REJECTION.md).

    Shared with `evidence` through `_windows`, so a snippet is the
    best-covering passage rather than an arbitrary one, and the same
    passage every run.
    """
    best = _windows(text, terms, width=window, count=1)
    if best:
        return best[0]
    return " ".join(text[:window].split())


def _full_text(item: sqlite3.Row) -> str:
    text_parts = [item["title"] or ""]
    if item["parsed_path"]:
        try:
            text_parts.append(Path(item["parsed_path"]).read_text(errors="ignore"))
        except OSError:
            pass
    return "\n".join(text_parts)


def _parsed_file_stat(parsed_path: str | None) -> tuple[bool, int, int]:
    if parsed_path:
        try:
            st = Path(parsed_path).stat()
            return True, st.st_size, st.st_mtime_ns
        except OSError:
            pass
    return False, 0, 0


def _fingerprint(item: sqlite3.Row) -> list:
    exists, size, mtime_ns = _parsed_file_stat(item["parsed_path"])
    return [item["title"] or "", item["parsed_path"] or "", exists, size, mtime_ns]


def _tokenize_item(item: sqlite3.Row) -> dict:
    tokens = _tokenize(_full_text(item))
    return {"length": len(tokens), "term_freqs": dict(Counter(tokens))}


def _load_cache() -> dict:
    try:
        with open(config.RETRIEVAL_INDEX_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _INDEX_SCHEMA_VERSION:
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def _save_cache(items_index: dict) -> None:
    config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _INDEX_SCHEMA_VERSION, "items": items_index}
    # Write to a per-process/per-call-unique temp file in the same
    # directory, then os.replace (atomic on POSIX) -- deep-research
    # dispatches several parallel subagents that may all call search()
    # concurrently, and a shared fixed temp filename would let one
    # writer's partial write collide with another's.
    tmp_path = config.RETRIEVAL_INDEX_PATH.with_name(
        f"{config.RETRIEVAL_INDEX_PATH.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    )
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_path, config.RETRIEVAL_INDEX_PATH)


def _load_index(items: list[sqlite3.Row]) -> dict:
    """Build the term-frequency index for `items`, reusing cached
    per-document stats for anything whose fingerprint hasn't changed.

    Any cache read/schema problem (missing file, corrupt JSON, stale
    schema version, or valid JSON in an unexpected shape -- a bare array,
    an "items"/per-citekey entry that isn't a dict) is treated as a cache
    miss -- rebuild from scratch rather than fail the search.
    """
    cached = _load_cache()
    current_citekeys = {item["citekey"] for item in items}
    new_index = {}
    changed = bool(set(cached) - current_citekeys)  # stale citekeys dropped
    for item in items:
        citekey = item["citekey"]
        fp = _fingerprint(item)
        cached_entry = cached.get(citekey)
        if isinstance(cached_entry, dict) and cached_entry.get("fingerprint") == fp:
            new_index[citekey] = cached_entry
        else:
            new_index[citekey] = {"fingerprint": fp, **_tokenize_item(item)}
            changed = True
    if changed:
        _save_cache(new_index)
    return new_index


def _bm25_scores(index: dict, terms: list[str]) -> dict[str, float]:
    doc_count = len(index)
    if doc_count == 0:
        return {}
    avgdl = sum(entry["length"] for entry in index.values()) / doc_count

    term_set = set(terms)
    doc_freq = {
        t: sum(1 for entry in index.values() if entry["term_freqs"].get(t))
        for t in term_set
    }
    idf = {
        t: math.log((doc_count - doc_freq[t] + 0.5) / (doc_freq[t] + 0.5) + 1)
        for t in term_set
    }

    scores: dict[str, float] = {}
    for citekey, entry in index.items():
        doc_len = entry["length"]
        norm = 1 - _B + _B * (doc_len / avgdl if avgdl else 0)
        score = 0.0
        for t in term_set:
            freq = entry["term_freqs"].get(t, 0)
            if freq == 0:
                continue
            score += idf[t] * (freq * (_K1 + 1)) / (freq + _K1 * norm)
        if score > 0:
            scores[citekey] = score
    return scores


def search(query: str, k: int = 5, snippet_chars: int = 500) -> list[SearchResult]:
    """Rank ledger items by BM25 relevance to `query`. Returns top-k.

    `snippet_chars` defaults to enough context for a caller (e.g. a genre
    skill) to judge relevance itself before citing -- see the "Retrieve"
    step in the genre skills for why that judgment shouldn't just trust
    the score.
    """
    terms = _tokenize(query)
    if not terms:
        return []

    con = ledger.connect()
    try:
        items = ledger.all_items(con)
    finally:
        con.close()

    index = _load_index(items)
    scores = _bm25_scores(index, terms)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]

    by_citekey = {item["citekey"]: item for item in items}
    term_set = set(terms)
    results = []
    for citekey, score in ranked:
        item = by_citekey[citekey]
        results.append(
            SearchResult(
                citekey=citekey,
                title=item["title"],
                score=score,
                snippet=_snippet(_full_text(item), term_set, window=snippet_chars),
            )
        )
    return results


# Zooming in on one document
# --------------------------
#
# `search` above answers "which documents, and roughly why", with a
# snippet long enough to judge a candidate on. `evidence` answers the
# question that comes after it for a document you have already decided is
# worth the attention: "what in *this* paper actually bears on my query?"
#
# It is a lookup, not a stage. Nothing has to call it, and a caller that
# is satisfied by a `search` snippet is done. That framing is deliberate
# and was arrived at the hard way -- an earlier version of this module
# made a short-window `triage` pass the mandatory first stage of drafting,
# with `evidence` reserved for whatever survived it. docs/REJECTION.md is
# the full reckoning; the short version is that it put an irreversible
# decision (rejecting a source, which `rejected.md` then makes permanent)
# on a third of the evidence, in exchange for a saving that was
# conditional, unmeasured, and real for one genre out of five.
#
# What survived that reckoning is here, because none of it needed the
# split: the window chooser `_windows` (now shared with `_snippet`, and
# the reason a snippet is deterministic at all), this CLI, `evidence`
# itself, and `--log`. Used this way `evidence` deepens an *acceptance*
# rather than cheapening a rejection, which is the safer direction: being
# more careful about a source you are about to cite cannot lose you one
# you never saw.

EVIDENCE_CHARS = 600
EVIDENCE_WINDOWS = 2


def evidence(
    citekey: str, query: str, chars: int = EVIDENCE_CHARS, windows: int = EVIDENCE_WINDOWS
) -> list[str]:
    """The passages of one document that bear on `query`.

    A lookup for one document you already care about, not a stage
    anything is obliged to run: use it when a `search` snippet is not
    enough to judge a source you are minded to cite. Returns more text
    per document than a snippet, chosen for the query. Returns `[]` for a
    citekey with no parsed text: a source the corpus layer could not read
    is a real answer, not an error.

    Deliberately reads `parsed_path` rather than going through
    `src/passages.py`: this ranks the same text BM25 ranked, so what
    comes back is what the score was about. `passages.py` owns the
    quotable-paragraph/page ladder that `citation_provenance` needs to
    *attribute* a claim -- a different question, asked after drafting.
    """
    # The citekey is checked before the query, so that naming a key the
    # ledger doesn't have is reported as the caller error it is even when
    # the query happens to tokenize to nothing.
    con = ledger.connect()
    try:
        # row_factory set and cleared around the read, matching
        # ledger.all_items: connect() leaves rows as tuples, and
        # _full_text addresses its columns by name.
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT title, parsed_path FROM items WHERE citekey = ?", (citekey,)
        ).fetchone()
        con.row_factory = None
    finally:
        con.close()
    if row is None:
        raise KeyError(f"{citekey} is not in the ledger")
    terms = set(_tokenize(query))
    if not terms:
        return []
    return _windows(_full_text(row), terms, width=chars, count=windows)


# ---------------------------------------------------------------------
# CLI: `python3 -m src.retrieval`
#
# Its own entrypoint rather than the `python3 -c "from src import
# retrieval; [print(r.citekey, r.snippet) for r in ...]"` one-liner the
# skills used to carry. Three reasons, all about the caller's context
# rather than convenience: the one-liner's output shape was whatever the
# author of each skill happened to write, `--log` needs somewhere to
# hang, and a `--chars` flag with a documented default is a much more
# obvious knob than an argument buried in a shell-quoted Python
# expression.
# ---------------------------------------------------------------------


def _print_results(results: list[SearchResult]) -> int:
    """One block per result. Returns the payload size in characters."""
    chars = 0
    for result in results:
        chars += len(result.snippet)
        print(f"\n{result.citekey}  (score {result.score:.1f})")
        print(f"  {result.title}")
        print(f"  {result.snippet}")
    return chars


def main(argv: "list[str] | None" = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m src.retrieval",
        description="BM25 retrieval over the synced corpus. Read-only, takes no "
                    "lock, and runs with the bare system python3.",
        epilog="`search` ranks the corpus and hands back a snippet to judge each "
               "candidate on. `evidence` zooms in on one document you already care "
               "about. Neither is a stage: nothing has to call evidence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser(
        "search", help="Rank the corpus and return a snippet per candidate")
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=5, help="Results to return (default 5)")
    p_search.add_argument("--chars", type=int, default=500, help="Snippet size (default 500)")

    p_evidence = sub.add_parser(
        "evidence", help="The passages of one document that bear on the query")
    p_evidence.add_argument("query")
    p_evidence.add_argument("--citekey", required=True)
    p_evidence.add_argument("--chars", type=int, default=EVIDENCE_CHARS,
                            help=f"Window size (default {EVIDENCE_CHARS})")
    p_evidence.add_argument("--windows", type=int, default=EVIDENCE_WINDOWS,
                            help=f"Passages to return (default {EVIDENCE_WINDOWS})")

    for each in (p_search, p_evidence):
        each.add_argument(
            "--log", metavar="DRAFT",
            help="Record this call in DRAFT's dossier (content/dossiers/...), so the "
                 "cost of retrieval for this draft is measured rather than estimated")

    args = parser.parse_args(argv)

    if not config.LEDGER_PATH.exists():
        print(f"No ledger at {config.LEDGER_PATH}.", file=sys.stderr)
        print("Run `python -m src.sync` to build it from your bib file.", file=sys.stderr)
        return 1

    if args.command == "evidence":
        try:
            passages = evidence(args.citekey, args.query, args.chars, args.windows)
        except KeyError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 1
        if not passages:
            print(f"{args.citekey}: no passage matches that query "
                  "(or the corpus layer has no parsed text for it).")
        for passage in passages:
            print(f"\n  {passage}")
        results, chars = len(passages), sum(len(p) for p in passages)
    else:
        found = search(args.query, k=args.k, snippet_chars=args.chars)
        if not found:
            print("No results.")
        chars = _print_results(found)
        results = len(found)
        if found:
            print("\n  Judge each snippet yourself -- a high score means the query's "
                  "words are in the document, not that it supports your claim. Run "
                  "`evidence --citekey <key>` where a snippet is not enough to decide.")

    print(f"\n  {results} result(s), {chars:,} characters returned.")
    if args.log:
        from src import dossier

        try:
            # The logged `k` is "how much was asked for", which is `--k`
            # for the ranking modes and `--windows` for `evidence` --
            # `evidence` has no `--k`, and logging a bare 1 there put a
            # number in the column that meant nothing.
            asked_for = args.windows if args.command == "evidence" else args.k
            path = dossier.log_retrieval(
                Path(args.log), args.command, args.query,
                asked_for, results, chars,
            )
        except (dossier.DossierError, OSError) as exc:
            # A measurement is worth less than the retrieval it measures:
            # report and carry on rather than failing the search. OSError
            # is caught alongside DossierError because the failure this
            # has to survive is not only "that path isn't a draft" -- a
            # read-only content/, a full disk or a permissions problem
            # would otherwise let a bookkeeping write throw away results
            # the caller has already paid to compute.
            print(f"  [not logged] {exc}", file=sys.stderr)
        else:
            print(f"  Logged to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

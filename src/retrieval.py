"""BM25-ranked keyword retrieval over the shared corpus layer.

This is the default retrieval implementation genre skills call against
(AGENTS.md's "Retrieval" section) -- stdlib-only, no venv or model
download needed. `src/heavy/embed_index.py` (sentence-transformers +
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
does. And nothing in `scripts/full_pipeline.py` imports this module, so
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
stat-before-hash skip logic and src/heavy/embed_index.py's embedding
cache. Building a snippet for the returned top-k still reads those
(bounded, small) documents' text fresh, since a snippet needs the real
surrounding text, not just term counts.
"""

import json
import math
import os
import re
import sqlite3
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


def _snippet(text: str, terms: set[str], window: int = 500) -> str:
    lower = text.lower()
    for term in terms:
        idx = lower.find(term)
        if idx != -1:
            start = max(0, idx - window // 2)
            end = min(len(text), idx + window // 2)
            return " ".join(text[start:end].split())
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

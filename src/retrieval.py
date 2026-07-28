"""Keyword-based retrieval over the shared content layer.

This is a deliberate placeholder for embedding-based retrieval
(sentence-transformers + Chroma/Qdrant, per the original pipeline
design). That stack needs a venv (PEP 668 blocks system pip here), a
model download, and a corpus large enough to justify clustering --
none of which hold yet with a 2-item library. `search()` below is the
contract genre skills call against; swap the implementation later
without changing callers.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from src import ledger

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "for", "and", "to", "with",
    "is", "are", "be", "this", "that", "as", "by", "from", "at",
}


@dataclass
class SearchResult:
    citekey: str
    title: str
    score: int
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


def search(query: str, k: int = 5, snippet_chars: int = 500) -> list[SearchResult]:
    """Rank ledger items by term-overlap with `query`. Returns top-k.

    `snippet_chars` defaults to enough context for a caller (e.g. a genre
    skill) to judge relevance itself before citing -- see the "Retrieve"
    step in the genre skills for why that judgment shouldn't just trust
    the score.
    """
    terms = set(_tokenize(query))
    if not terms:
        return []

    con = ledger.connect()
    try:
        items = ledger.all_items(con)
    finally:
        con.close()

    results = []
    for item in items:
        text_parts = [item["title"] or ""]
        if item["parsed_path"]:
            try:
                text_parts.append(Path(item["parsed_path"]).read_text(errors="ignore"))
            except OSError:
                pass
        full_text = "\n".join(text_parts)
        tokens = _tokenize(full_text)
        score = sum(tokens.count(t) for t in terms)
        if score == 0:
            continue
        results.append(
            SearchResult(
                citekey=item["citekey"],
                title=item["title"],
                score=score,
                snippet=_snippet(full_text, terms, window=snippet_chars),
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:k]

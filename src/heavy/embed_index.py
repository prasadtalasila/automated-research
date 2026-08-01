"""Stage 3: sentence-transformers embeddings persisted in a Chroma collection.

This is the real embedding-based retrieval the core pipeline's
src/retrieval.py deliberately deferred (keyword overlap only, pending a
larger corpus). Needs `sentence-transformers` and `chromadb` from
pyproject.toml's "heavy" Poetry group, in a venv.

build_index() is incremental, mirroring the core pipeline's
src/ledger.py: skip reprocessing whatever hasn't detectably changed
since the last run. Here that means each chunk's stored
metadata carries a hash of the *text that produced it* (not the PDF
bytes -- Docling reprocessing the same PDF, or a manually edited parsed
.txt, can change the embedded text without the PDF itself changing), and
a doc whose current text hashes the same as what's already indexed skips
model.encode() entirely. Only genuinely new/changed docs pay the encode
cost. This also fixes a latent bug: previously, a doc whose chunk count
*shrank* between runs left its old, now-orphaned trailing chunks in
Chroma forever (upsert only ever adds/overwrites, never removes) -- an
unchanged-vs-changed check that deletes-then-reinserts on a real change
closes that gap too.
"""

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

from src import config
from src.heavy.corpus import CorpusDoc, safe_filename

_COLLECTION_PREFIX = "corpus"


def _collection_name() -> str:
    """Chroma collection name for the currently configured embedding model.

    Different models produce different-dimensioned vectors (e.g.
    MiniLM-L6-v2's 384 vs mpnet-base-v2's 768). A single shared collection
    would either raise a dimension-mismatch error from Chroma on the first
    upsert after a model swap, or -- since the skip logic below only keys
    off the text hash -- silently keep serving stale vectors from the old
    model for any doc whose text hasn't changed. Namespacing the collection
    by model sidesteps both: switching `embedding_model` in config.toml
    starts a fresh, empty collection instead of corrupting or stale-skipping
    the old one.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", config.EMBEDDING_MODEL).strip("-.")
    return f"{_COLLECTION_PREFIX}-{slug}"[:63].rstrip("-.")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def get_text(doc: CorpusDoc) -> str | None:
    """Best available text for a doc: Docling output > existing parsed text
    > on-the-fly pdftotext. Doesn't require the Docling stage to have run."""
    docling_path = config.DOCLING_DIR / f"{safe_filename(doc.doc_id)}.md"
    if docling_path.exists():
        return docling_path.read_text()
    if doc.text_path and Path(doc.text_path).exists():
        return Path(doc.text_path).read_text()
    if doc.pdf_path:
        # delete=False + a manual unlink in finally, not the plain `with
        # ... as tmp:` shortcut: on Windows, NamedTemporaryFile keeps its
        # own handle open (and the file exclusively locked) for the
        # block's duration, and pdftotext writing to that same path
        # while Python still holds it open fails with PermissionError --
        # POSIX allows a second open of the same path, which is why this
        # only surfaced on this repo's Windows CI leg. Closing tmp
        # immediately releases that lock before pdftotext ever touches
        # the path.
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()
        try:
            subprocess.run(
                ["pdftotext", "-layout", doc.pdf_path, tmp.name],
                check=True, capture_output=True,
            )
            return Path(tmp.name).read_text(errors="ignore")
        finally:
            os.unlink(tmp.name)
    return None


def chunk_text(text: str, chunk_words: int = 200, overlap_words: int = 40) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = chunk_words - overlap_words
    return [" ".join(words[i:i + chunk_words]) for i in range(0, len(words), step)]


def get_client_and_model():
    import chromadb
    from sentence_transformers import SentenceTransformer

    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    model = SentenceTransformer(config.EMBEDDING_MODEL)
    return client, model


def build_index(docs: list[CorpusDoc]) -> dict[str, int]:
    """Embeds and upserts each doc's chunks, skipping docs whose text is
    unchanged since the last call. Returns {doc_id: n_chunks}."""
    client, model = get_client_and_model()
    collection = client.get_or_create_collection(_collection_name())

    counts = {}
    for doc in docs:
        text = get_text(doc)
        if not text:
            counts[doc.doc_id] = 0
            continue

        text_hash = hash_text(text)
        existing = collection.get(where={"doc_id": doc.doc_id})
        if existing["ids"] and all(m.get("text_hash") == text_hash for m in existing["metadatas"]):
            counts[doc.doc_id] = len(existing["ids"])
            continue
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        chunks = chunk_text(text)
        if not chunks:
            counts[doc.doc_id] = 0
            continue
        embeddings = model.encode(chunks, show_progress_bar=False).tolist()
        ids = [f"{safe_filename(doc.doc_id)}::{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id": doc.doc_id,
                "citekey": doc.citekey or "",
                "source": doc.source,
                "title": doc.title,
                "text_hash": text_hash,
            }
            for _ in chunks
        ]
        collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
        counts[doc.doc_id] = len(chunks)
    return counts


def search(query: str, k: int = 5, snippet_chars: int = 500) -> list[dict]:
    """`snippet_chars` defaults to enough context for a caller to judge
    relevance itself before citing, rather than trusting distance alone."""
    client, model = get_client_and_model()
    collection = client.get_or_create_collection(_collection_name())
    query_embedding = model.encode([query], show_progress_bar=False).tolist()
    raw = collection.query(query_embeddings=query_embedding, n_results=k)

    results = []
    for doc_text, metadata, distance in zip(raw["documents"][0], raw["metadatas"][0], raw["distances"][0]):
        results.append({**metadata, "snippet": doc_text[:snippet_chars], "distance": distance})
    return results

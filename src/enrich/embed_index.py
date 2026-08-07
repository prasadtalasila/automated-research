"""Stage 3: sentence-transformers embeddings persisted in a Chroma collection.

This is the real embedding-based retrieval the corpus layer's
src/retrieval.py deliberately deferred (keyword overlap only, pending a
larger corpus). Needs `sentence-transformers` and `chromadb` from
pyproject.toml's "enrich" Poetry group, in a venv.

build_index() is incremental, mirroring the corpus layer's
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
from src.enrich.corpus import CorpusDoc

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


def strip_image_refs(markdown: str) -> str:
    """Drop Docling's image markers from text on its way to the embedder.

    Two forms, depending on config.DOCLING_IMAGES: a bare `<!-- image -->`
    placeholder, or a real `![Image](<stem>_artifacts/image_000000_<64 hex
    chars>.png)` reference. Neither carries meaning an embedding can use,
    and the second is worse than the first: chunk_text() splits on
    whitespace, so a ~100-character path hashes down to a single "word"
    that displaces real text from a 200-word chunk.

    Captions survive deliberately -- Docling emits them as their own
    text items ("Figure 3. Sensor placement..."), not as the image's alt
    text, so they're real prose about the figure and worth embedding.
    """
    without_refs = re.sub(r"^[ \t]*!\[[^\]]*\]\([^)]*\)[ \t]*$", "", markdown, flags=re.MULTILINE)
    without_placeholders = re.sub(r"^[ \t]*<!--\s*image\s*-->[ \t]*$", "", without_refs, flags=re.MULTILINE)
    # Collapse the blank runs those deletions leave behind, so chunking
    # doesn't see paragraph gaps where a figure used to sit.
    return re.sub(r"\n{3,}", "\n\n", without_placeholders)


def get_text(doc: CorpusDoc) -> str | None:
    """Best available text for a doc: Docling output > existing parsed text
    > on-the-fly pdftotext. Doesn't require the Docling stage to have run."""
    docling_path = config.DOCLING_DIR / f"{doc.doc_id}.md"
    if docling_path.exists():
        return strip_image_refs(docling_path.read_text())
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
    unchanged since the last call. Returns {doc_id: n_chunks}.

    Reports each document as it is *reached*, not as it finishes: the
    line is opened before `model.encode()` and closed by whatever the
    document turned out to be. A stage that prints only on completion
    still leaves the slowest document in the corpus looking like a hang
    for as long as it takes -- and a stage that printed nothing at all
    until it returned is how issue #50 came to be filed against a run
    that was working fine, then Ctrl-C'd at 399 of 501 documents.

    Same `  [done/total] <doc_id>` shape src/sync.py and
    docling_parse.py already use for their own per-document progress, and
    flushed for the reason pdf_text.py flushes its interrupt notice:
    stdout is block-buffered when it isn't a terminal, and the tail of an
    interrupted run is exactly the part worth keeping.
    """
    client, model = get_client_and_model()
    collection = client.get_or_create_collection(_collection_name())

    counts = {}
    n_embedded = n_unchanged = n_no_text = 0
    try:
        for position, doc in enumerate(docs, start=1):
            print(f"  [{position}/{len(docs)}] {doc.doc_id}", end="", flush=True)

            text = get_text(doc)
            if not text:
                counts[doc.doc_id] = 0
                n_no_text += 1
                print(" -- no text to embed", flush=True)
                continue

            text_hash = hash_text(text)
            existing = collection.get(where={"doc_id": doc.doc_id})
            if existing["ids"] and all(m.get("text_hash") == text_hash for m in existing["metadatas"]):
                counts[doc.doc_id] = len(existing["ids"])
                n_unchanged += 1
                print(f" -- unchanged, {len(existing['ids'])} chunk(s)", flush=True)
                continue
            if existing["ids"]:
                collection.delete(ids=existing["ids"])

            chunks = chunk_text(text)
            if not chunks:
                # Whitespace-only text lands here rather than above.
                # Reported the same way because it amounts to the same
                # thing for a reader: nothing of this document is in the
                # index, and no amount of waiting will change that.
                counts[doc.doc_id] = 0
                n_no_text += 1
                print(" -- no text to embed", flush=True)
                continue

            embeddings = model.encode(chunks, show_progress_bar=False).tolist()
            ids = [f"{doc.doc_id}::{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "doc_id": doc.doc_id,
                    "citekey": doc.citekey,
                    "title": doc.title,
                    "text_hash": text_hash,
                }
                for _ in chunks
            ]
            collection.upsert(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)
            counts[doc.doc_id] = len(chunks)
            n_embedded += 1
            print(f" -- embedded, {len(chunks)} chunk(s)", flush=True)
    except KeyboardInterrupt:
        # Chroma has already persisted every chunk upserted so far, and
        # the next run's text-hash check skips those documents -- so an
        # interrupted run is worth something and the message says so,
        # rather than leaving the reader to guess (the same promise
        # docling_parse.py makes when its own pool is interrupted).
        print(f"\n  interrupted after {len(counts)}/{len(docs)} document(s) "
              "-- what is embedded is kept; re-run to continue.", flush=True)
        raise

    print(f"  {len(docs)} document(s): {n_embedded} embedded, {n_unchanged} unchanged, "
          f"{n_no_text} with no text -- {sum(counts.values())} chunk(s) in the index",
          flush=True)
    return counts


def search(query: str, k: int = 5, snippet_chars: int = 500) -> list[dict]:
    """`snippet_chars` defaults to enough context for a caller to judge
    relevance itself before citing, rather than trusting distance alone.

    Deliberately the same shape as `src.retrieval.search()`, so this is a
    drop-in for it, and every hit is citable either way: both draw on the
    ledger, so a returned `citekey` always resolves against it. That is
    what restricting the corpus to the bibliography buys (see corpus.py)
    -- there is no longer any such thing as a hit a draft may not cite.

    The two do not cover quite the same *documents*, though, and a caller
    choosing between them should know which way it cuts. BM25 indexes
    each item's title whether or not it has parsed text, so a
    metadata-only entry is still findable there; `build_index` skips any
    document `get_text` returns nothing for, because there is nothing to
    embed. A bib entry whose PDF is missing or failed to parse is
    therefore searchable by title and not by meaning."""
    client, model = get_client_and_model()
    collection = client.get_or_create_collection(_collection_name())
    query_embedding = model.encode([query], show_progress_bar=False).tolist()
    raw = collection.query(query_embeddings=query_embedding, n_results=k)

    results = []
    for doc_text, metadata, distance in zip(raw["documents"][0], raw["metadatas"][0], raw["distances"][0]):
        results.append({**metadata, "snippet": doc_text[:snippet_chars], "distance": distance})
    return results

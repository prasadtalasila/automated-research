"""Stage 4: BERTopic clustering over the corpus.

Needs `bertopic` from pyproject.toml's "enrich" Poetry group, in a venv. With a
handful of documents, HDBSCAN's default min_cluster_size (10) will
legitimately put everything in the outlier topic (-1) -- that is the
correct output for a small corpus, not a bug. Don't lower
min_cluster_size to force clusters into existence; report the outlier
result honestly and let the topic model become meaningful once the
corpus is large enough to justify it.

Unlike every other stage, this one has no downstream consumer in the
repository: nothing imports it, and no genre skill reads
config.TOPICS_PATH. `survey-writer` groups its themes by judgement over
the evidence it retrieved and says so ("With a small corpus there's no
BERTopic step"). The output is written for a human deciding what a survey
should cover. Wiring it in is a real option -- DEVELOPER.md's
"`content/topics.json` has no consumer" records what that would take and
why it hasn't been done -- but until then, don't assume a caller
somewhere depends on this file's shape.

It is also the one stage that can't be incremental, which is the reason
that matters here: clustering is whole-corpus, so one added document can
move every assignment, and topic ids are not stable between runs.

BERTopic's clustering step is inherently whole-corpus -- adding one new
document can shift every cluster assignment, so unlike
embed_index.build_index() there's no "skip this doc" option for the
clustering itself. What *is* skippable is the expensive part before it:
re-embedding a document's full text with model.encode(). This module
caches one whole-text embedding per doc_id (config.TOPIC_EMBED_CACHE_PATH,
keyed by the same hash_text() embed_index.py uses for its own per-chunk
cache) and only calls encode() for docs whose text hash changed since the
last run, batching all of them into one encode() call rather than one per
doc. Note this cache is intentionally separate from embed_index.py's
Chroma collection: that one stores per-*chunk* embeddings for retrieval,
this one stores one whole-document embedding per doc for clustering --
different granularity, not reusable as-is between the two.
"""

import json

from src import config
from src.enrich import embed_index
from src.enrich.corpus import CorpusDoc


def _load_embed_cache() -> dict:
    if config.TOPIC_EMBED_CACHE_PATH.exists():
        return json.loads(config.TOPIC_EMBED_CACHE_PATH.read_text())
    return {}


def _save_embed_cache(cache: dict) -> None:
    config.TOPIC_EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TOPIC_EMBED_CACHE_PATH.write_text(json.dumps(cache))


def run_topic_model(docs: list[CorpusDoc]) -> dict:
    import numpy as np
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from umap import UMAP

    doc_texts = {}
    for doc in docs:
        text = embed_index.get_text(doc)
        if text:
            doc_texts[doc.doc_id] = text

    if len(doc_texts) < 2:
        raise ValueError(f"Need at least 2 documents with text to run BERTopic; got {len(doc_texts)}")

    _client, model = embed_index.get_client_and_model()  # reuse the same embedding model

    cache = _load_embed_cache()
    doc_hashes = {doc_id: embed_index.hash_text(text) for doc_id, text in doc_texts.items()}
    # Track which model produced each cached vector, not just the text hash
    # that produced it: swapping config.toml's embedding_model changes every
    # vector's dimensionality without changing any doc's text, and mixing
    # dimensions in the same `embeddings` array below breaks BERTopic outright.
    stale_doc_ids = [
        doc_id for doc_id in doc_texts
        if doc_id not in cache
        or cache[doc_id]["hash"] != doc_hashes[doc_id]
        or cache[doc_id].get("model") != config.EMBEDDING_MODEL
    ]
    if stale_doc_ids:
        new_vecs = model.encode([doc_texts[d] for d in stale_doc_ids], show_progress_bar=False)
        for doc_id, vec in zip(stale_doc_ids, new_vecs):
            cache[doc_id] = {
                "hash": doc_hashes[doc_id],
                "model": config.EMBEDDING_MODEL,
                "embedding": vec.tolist(),
            }
        _save_embed_cache(cache)

    doc_ids = list(doc_texts)
    texts = [doc_texts[d] for d in doc_ids]
    embeddings = np.array([cache[d]["embedding"] for d in doc_ids])

    # UMAP's spectral initialization needs n_neighbors < n_samples or it
    # raises outright (not just a bad clustering) -- BERTopic's own
    # defaults (n_neighbors=15) assume a corpus far larger than this
    # project's current few documents. Scaling down is a correctness fix
    # for small N, not an attempt to manufacture nicer-looking clusters:
    # HDBSCAN's min_cluster_size is left low but not forced to 2, so a
    # tiny corpus still honestly reports as mostly/all outliers.
    # Spectral initialization needs n_components + 1 < n_samples (it solves
    # for n_components+1 eigenvectors of an n_samples x n_samples graph),
    # a tighter constraint than n_neighbors < n_samples alone.
    n_docs = len(texts)
    umap_model = UMAP(
        n_neighbors=min(15, n_docs - 1),
        n_components=min(5, max(2, n_docs - 2)),
        min_dist=0.0, metric="cosine", random_state=42,
    )
    hdbscan_model = HDBSCAN(
        min_cluster_size=max(2, min(10, n_docs // 2)),
        metric="euclidean", cluster_selection_method="eom", prediction_data=True,
    )

    topic_model = BERTopic(
        embedding_model=model, umap_model=umap_model, hdbscan_model=hdbscan_model,
        calculate_probabilities=False, verbose=False,
    )
    topics, _probs = topic_model.fit_transform(texts, embeddings)

    result = {
        "n_docs": len(texts),
        "assignments": dict(zip(doc_ids, [int(t) for t in topics])),
        "topic_info": json.loads(topic_model.get_topic_info().to_json(orient="records")),
    }
    config.TOPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.TOPICS_PATH.write_text(json.dumps(result, indent=2))
    return result

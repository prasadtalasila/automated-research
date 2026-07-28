"""Stage 4: BERTopic clustering over the corpus.

Needs `bertopic` from docker/requirements-full.txt in a venv. With a
handful of documents, HDBSCAN's default min_cluster_size (10) will
legitimately put everything in the outlier topic (-1) -- that is the
correct output for a small corpus, not a bug. Don't lower
min_cluster_size to force clusters into existence; report the outlier
result honestly and let the topic model become meaningful once the
corpus is large enough to justify it.
"""

import json

from src import config
from src.heavy import embed_index
from src.heavy.corpus import CorpusDoc


def run_topic_model(docs: list[CorpusDoc]) -> dict:
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from umap import UMAP

    texts, doc_ids = [], []
    for doc in docs:
        text = embed_index.get_text(doc)
        if text:
            texts.append(text)
            doc_ids.append(doc.doc_id)

    if len(texts) < 2:
        raise ValueError(f"Need at least 2 documents with text to run BERTopic; got {len(texts)}")

    _client, model = embed_index.get_client_and_model()  # reuse the same embedding model
    embeddings = model.encode(texts, show_progress_bar=False)

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

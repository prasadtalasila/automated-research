"""src/heavy/topic_model.py: BERTopic clustering, with the small-corpus
UMAP/HDBSCAN scaling formula this module relies on to not crash outright
on a handful of documents.

bertopic/umap/hdbscan are mocked via sys.modules so these stay fast and
verify the *scaling arithmetic* precisely, rather than depending on a
real (slow) clustering run to happen to produce a particular topic
count. A slow, real end-to-end run was already verified by hand during
installation smoke-testing (see the Task-1 conversation) -- not
duplicated here.
"""

import json
import sys
import types
from pathlib import Path

import pytest

from src import config
from src.heavy import embed_index, topic_model
from src.heavy.corpus import CorpusDoc


class FakeUMAP:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeUMAP.last_kwargs = kwargs


class FakeHDBSCAN:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeHDBSCAN.last_kwargs = kwargs


class FakeTopicInfo:
    def to_json(self, orient):
        return json.dumps([{"Topic": -1, "Count": 2, "Name": "-1_outlier"}])


class FakeBERTopic:
    last_kwargs = None

    def __init__(self, **kwargs):
        FakeBERTopic.last_kwargs = kwargs

    def fit_transform(self, texts, embeddings):
        return [-1 for _ in texts], None

    def get_topic_info(self):
        return FakeTopicInfo()


class FakeArray(list):
    def tolist(self):
        return list(self)


class FakeModel:
    encode_call_texts = []  # records each call's input, for cache-hit assertions

    def encode(self, texts, show_progress_bar=False):
        FakeModel.encode_call_texts.append(list(texts))
        return FakeArray([FakeArray([float(len(t))]) for t in texts])


@pytest.fixture
def fake_bertopic_stack(monkeypatch):
    FakeUMAP.last_kwargs = None
    FakeHDBSCAN.last_kwargs = None
    FakeBERTopic.last_kwargs = None
    FakeModel.encode_call_texts = []

    umap_module = types.ModuleType("umap")
    umap_module.UMAP = FakeUMAP
    monkeypatch.setitem(sys.modules, "umap", umap_module)

    hdbscan_module = types.ModuleType("hdbscan")
    hdbscan_module.HDBSCAN = FakeHDBSCAN
    monkeypatch.setitem(sys.modules, "hdbscan", hdbscan_module)

    bertopic_module = types.ModuleType("bertopic")
    bertopic_module.BERTopic = FakeBERTopic
    monkeypatch.setitem(sys.modules, "bertopic", bertopic_module)

    monkeypatch.setattr(embed_index, "get_client_and_model", lambda: (None, FakeModel()))
    return types.SimpleNamespace(umap=FakeUMAP, hdbscan=FakeHDBSCAN, bertopic=FakeBERTopic)


def make_docs_with_text(n, tmp_path):
    docs = []
    for i in range(n):
        path = tmp_path / f"doc{i}.txt"
        path.write_text(f"document number {i} " * 5)
        docs.append(CorpusDoc(
            doc_id=f"doc{i}", citekey=f"doc{i}", source="bib", title=f"T{i}",
            pdf_path=None, text_path=str(path),
        ))
    return docs


class TestRunTopicModel:
    def test_raises_below_minimum_doc_count(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(1, tmp_path)
        with pytest.raises(ValueError, match="Need at least 2 documents"):
            topic_model.run_topic_model(docs)

    def test_scaling_formula_for_small_corpus(self, isolated_config, fake_bertopic_stack, tmp_path):
        # n_docs=6: n_neighbors=min(15,5)=5, n_components=min(5,max(2,4))=4,
        # min_cluster_size=max(2,min(10,3))=3.
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)

        assert FakeUMAP.last_kwargs["n_neighbors"] == 5
        assert FakeUMAP.last_kwargs["n_components"] == 4
        assert FakeHDBSCAN.last_kwargs["min_cluster_size"] == 3

    @pytest.mark.parametrize("n_docs,expected_n_neighbors,expected_n_components,expected_min_cluster_size", [
        # n_docs=2: n_neighbors computes to 1 -- real UMAP's
        # `_validate_parameters` rejects this outright ("n_neighbors
        # must be greater than 1"), found by hand while installation
        # smoke-testing with a 2-document toy corpus (Task-1). Flagged
        # there as "worth a unit test in Task-2" rather than silently
        # patched, since it's a pre-existing edge case unrelated to
        # installation and real corpora are never this small. Pinned
        # here as documented, known-bad behavior against the *fake*
        # UMAP/HDBSCAN (which don't validate), not fixed.
        (2, 1, 2, 2),
        # n_docs=3: n_neighbors=2 clears UMAP's own n_neighbors>1 check,
        # but real UMAP's spectral initialization separately needs
        # n_components + 1 < n_samples (2+1 !< 3) and fails at a
        # different step (scipy.sparse.linalg.eigsh) -- also verified by
        # hand in Task-1, also not fixed here for the same reason.
        (3, 2, 2, 2),
    ])
    def test_small_corpus_boundary_values_are_pinned_not_fixed(
        self, isolated_config, fake_bertopic_stack, tmp_path,
        n_docs, expected_n_neighbors, expected_n_components, expected_min_cluster_size,
    ):
        docs = make_docs_with_text(n_docs, tmp_path)
        topic_model.run_topic_model(docs)  # doesn't raise -- fakes don't validate like real UMAP would

        assert FakeUMAP.last_kwargs["n_neighbors"] == expected_n_neighbors
        assert FakeUMAP.last_kwargs["n_components"] == expected_n_components
        assert FakeHDBSCAN.last_kwargs["min_cluster_size"] == expected_min_cluster_size

    def test_scaling_formula_for_large_corpus_caps_at_defaults(self, isolated_config, fake_bertopic_stack, tmp_path):
        # n_docs=30: n_neighbors capped at 15, n_components capped at 5,
        # min_cluster_size capped at 10.
        docs = make_docs_with_text(30, tmp_path)
        topic_model.run_topic_model(docs)

        assert FakeUMAP.last_kwargs["n_neighbors"] == 15
        assert FakeUMAP.last_kwargs["n_components"] == 5
        assert FakeHDBSCAN.last_kwargs["min_cluster_size"] == 10

    def test_writes_result_and_returns_assignments(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(6, tmp_path)
        result = topic_model.run_topic_model(docs)

        assert result["n_docs"] == 6
        assert set(result["assignments"]) == {d.doc_id for d in docs}
        assert all(v == -1 for v in result["assignments"].values())
        assert isolated_config.TOPICS_PATH.exists()
        saved = json.loads(isolated_config.TOPICS_PATH.read_text())
        assert saved["n_docs"] == 6

    def test_skips_docs_with_no_text(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(6, tmp_path)
        docs.append(CorpusDoc(doc_id="no_text", citekey="no_text", source="bib", title="t", pdf_path=None))

        result = topic_model.run_topic_model(docs)
        assert "no_text" not in result["assignments"]
        assert result["n_docs"] == 6


class TestEmbeddingCache:
    def test_second_run_with_unchanged_docs_encodes_nothing(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)
        assert len(FakeModel.encode_call_texts) == 1  # one batch call for all 6 new docs

        topic_model.run_topic_model(docs)
        assert len(FakeModel.encode_call_texts) == 1  # no second call -- every doc was cache-hit

    def test_changed_doc_triggers_encode_for_only_that_doc(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)

        Path(docs[0].text_path).write_text("completely different content now")
        topic_model.run_topic_model(docs)

        assert len(FakeModel.encode_call_texts) == 2
        assert FakeModel.encode_call_texts[1] == ["completely different content now"]

    def test_cache_persisted_to_disk_between_calls(self, isolated_config, fake_bertopic_stack, tmp_path):
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)

        assert isolated_config.TOPIC_EMBED_CACHE_PATH.exists()
        cache = json.loads(isolated_config.TOPIC_EMBED_CACHE_PATH.read_text())
        assert set(cache) == {d.doc_id for d in docs}
        assert all("hash" in v and "embedding" in v for v in cache.values())

    def test_model_change_re_embeds_every_cached_doc(
        self, isolated_config, fake_bertopic_stack, tmp_path, monkeypatch
    ):
        # Regression test: the cache previously keyed staleness only off the
        # doc's text hash, so swapping config.toml's embedding_model (e.g.
        # MiniLM-L6-v2 -> mpnet-base-v2, a real change made in this repo)
        # would keep serving cached vectors from the old model -- silently
        # mixing dimensions in the `embeddings` array BERTopic is fit on.
        monkeypatch.setattr(config, "EMBEDDING_MODEL", "model-a")
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)
        assert len(FakeModel.encode_call_texts) == 1
        assert len(FakeModel.encode_call_texts[0]) == 6

        monkeypatch.setattr(config, "EMBEDDING_MODEL", "model-b")
        topic_model.run_topic_model(docs)

        assert len(FakeModel.encode_call_texts) == 2
        assert len(FakeModel.encode_call_texts[1]) == 6  # every doc re-embedded, not just changed ones

        cache = json.loads(isolated_config.TOPIC_EMBED_CACHE_PATH.read_text())
        assert all(v["model"] == "model-b" for v in cache.values())

    def test_new_doc_added_to_existing_corpus_only_encodes_the_new_one(
        self, isolated_config, fake_bertopic_stack, tmp_path
    ):
        docs = make_docs_with_text(6, tmp_path)
        topic_model.run_topic_model(docs)

        new_doc_path = tmp_path / "doc_new.txt"
        new_doc_path.write_text("a brand new document")
        new_doc = CorpusDoc(
            doc_id="doc_new", citekey="doc_new", source="bib", title="New",
            pdf_path=None, text_path=str(new_doc_path),
        )
        topic_model.run_topic_model(docs + [new_doc])

        assert len(FakeModel.encode_call_texts) == 2
        assert FakeModel.encode_call_texts[1] == ["a brand new document"]

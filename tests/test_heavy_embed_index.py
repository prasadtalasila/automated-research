"""src/heavy/embed_index.py: sentence-transformers + Chroma, the
embeddings-based retrieval upgrade path for src/retrieval.py.

chromadb/sentence_transformers are mocked via sys.modules for fast,
deterministic unit tests -- they're imported lazily inside functions
(not at module top), so patching sys.modules before calling those
functions shadows the real packages for the duration of the test
without needing them uninstalled.
"""

import subprocess
import sys
import types

import pytest

from src.heavy import embed_index
from src.heavy.corpus import CorpusDoc


class FakeArray(list):
    def tolist(self):
        return list(self)


class FakeSentenceTransformer:
    instances = []

    def __init__(self, model_name):
        self.model_name = model_name
        FakeSentenceTransformer.instances.append(self)

    def encode(self, texts, show_progress_bar=False):
        # Deterministic "embedding": length of each text as a 1-d vector.
        return FakeArray([FakeArray([float(len(t))]) for t in texts])


class FakeCollection:
    def __init__(self):
        self.upserted = []
        self.query_response = None

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upserted.append({
            "ids": ids, "documents": documents, "embeddings": embeddings, "metadatas": metadatas,
        })

    def query(self, query_embeddings, n_results):
        return self.query_response


class FakeChromaClient:
    """Models chromadb.PersistentClient's actual persistence semantics:
    two client instances constructed with the same `path` see the same
    collections (backed by files on disk, in the real thing) -- which
    matters here because build_index()/search() each call
    get_client_and_model() independently, so a test that pre-seeds a
    collection via one client instance needs a later instance (same
    path) to see it too."""

    instances = []
    _stores_by_path = {}

    def __init__(self, path):
        self.path = path
        self.collections = FakeChromaClient._stores_by_path.setdefault(path, {})
        FakeChromaClient.instances.append(self)

    def get_or_create_collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture
def fake_heavy_deps(monkeypatch):
    FakeSentenceTransformer.instances.clear()
    FakeChromaClient.instances.clear()
    FakeChromaClient._stores_by_path.clear()

    fake_st_module = types.ModuleType("sentence_transformers")
    fake_st_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st_module)

    fake_chromadb_module = types.ModuleType("chromadb")
    fake_chromadb_module.PersistentClient = FakeChromaClient
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb_module)

    return types.SimpleNamespace(client_cls=FakeChromaClient, model_cls=FakeSentenceTransformer)


class TestChunkText:
    def test_empty_text_returns_no_chunks(self):
        assert embed_index.chunk_text("") == []

    def test_short_text_single_chunk(self):
        assert embed_index.chunk_text("one two three", chunk_words=200, overlap_words=40) == ["one two three"]

    def test_overlap_arithmetic(self):
        text = " ".join(str(i) for i in range(10))  # "0 1 2 ... 9"
        chunks = embed_index.chunk_text(text, chunk_words=4, overlap_words=1)
        assert chunks == ["0 1 2 3", "3 4 5 6", "6 7 8 9", "9"]


class TestGetText:
    def test_prefers_docling_output(self, isolated_config):
        isolated_config.DOCLING_DIR.mkdir(parents=True)
        (isolated_config.DOCLING_DIR / "doc_x.md").write_text("docling content")
        doc = CorpusDoc(doc_id="doc:x", citekey=None, source="source-pdfs", title="t", pdf_path=None, text_path="ignored.txt")
        assert embed_index.get_text(doc) == "docling content"

    def test_falls_back_to_text_path(self, isolated_config, tmp_path):
        parsed = tmp_path / "parsed.txt"
        parsed.write_text("parsed text content")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=None, text_path=str(parsed))
        assert embed_index.get_text(doc) == "parsed text content"

    def test_falls_back_to_pdftotext_subprocess(self, isolated_config, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            out_path = cmd[-1]
            with open(out_path, "w") as f:
                f.write("pdftotext output")
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(tmp_path / "a.pdf"))
        assert embed_index.get_text(doc) == "pdftotext output"

    def test_returns_none_when_nothing_available(self, isolated_config):
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=None)
        assert embed_index.get_text(doc) is None


class TestGetClientAndModel:
    def test_creates_persistent_client_and_model(self, isolated_config, fake_heavy_deps):
        client, model = embed_index.get_client_and_model()
        assert isinstance(client, FakeChromaClient)
        assert client.path == str(isolated_config.CHROMA_DIR)
        assert isolated_config.CHROMA_DIR.exists()
        assert model.model_name == isolated_config.EMBEDDING_MODEL


class TestBuildIndex:
    def test_indexes_docs_with_text_and_counts_chunks(self, isolated_config, fake_heavy_deps, tmp_path):
        parsed = tmp_path / "a.txt"
        parsed.write_text(" ".join(["word"] * 10))
        doc_with_text = CorpusDoc(
            doc_id="a2024", citekey="a2024", source="bib", title="A", pdf_path=None, text_path=str(parsed)
        )
        doc_without_text = CorpusDoc(
            doc_id="b2024", citekey="b2024", source="bib", title="B", pdf_path=None
        )

        counts = embed_index.build_index([doc_with_text, doc_without_text])

        assert counts["a2024"] == 1
        assert counts["b2024"] == 0

        client = FakeChromaClient.instances[-1]
        collection = client.collections["corpus"]
        assert len(collection.upserted) == 1
        upsert_call = collection.upserted[0]
        assert upsert_call["ids"] == ["a2024::0"]
        assert upsert_call["metadatas"][0] == {
            "doc_id": "a2024", "citekey": "a2024", "source": "bib", "title": "A",
        }

    def test_empty_chunks_from_whitespace_only_text(self, isolated_config, fake_heavy_deps, tmp_path):
        parsed = tmp_path / "empty.txt"
        parsed.write_text("   ")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="A", pdf_path=None, text_path=str(parsed))
        counts = embed_index.build_index([doc])
        assert counts["a2024"] == 0


class TestSearch:
    def test_combines_metadata_snippet_and_distance(self, isolated_config, fake_heavy_deps):
        client, _ = embed_index.get_client_and_model()
        collection = client.get_or_create_collection("corpus")
        collection.query_response = {
            "documents": [["some long document text " * 5]],
            "metadatas": [[{"doc_id": "a2024", "citekey": "a2024", "source": "bib", "title": "A"}]],
            "distances": [[0.123]],
        }

        results = embed_index.search("query", k=3, snippet_chars=10)
        assert len(results) == 1
        assert results[0]["citekey"] == "a2024"
        assert results[0]["distance"] == 0.123
        assert len(results[0]["snippet"]) == 10

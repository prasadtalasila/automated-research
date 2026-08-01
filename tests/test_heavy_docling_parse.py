"""src/heavy/docling_parse.py: layout-aware PDF parsing via Docling.

Docling is mocked via sys.modules (imported lazily inside parse_doc, not
at module top), so these stay fast and don't need real model weights.
"""

import json
import sys
import types

import pytest

from src.heavy import docling_parse
from src.heavy.corpus import CorpusDoc


class FakeDocument:
    def __init__(self, markdown):
        self._markdown = markdown

    def export_to_markdown(self):
        return self._markdown


class FakeConversionResult:
    def __init__(self, markdown):
        self.document = FakeDocument(markdown)


class FakeDocumentConverter:
    last_convert_path = None
    call_count = 0

    def convert(self, pdf_path):
        FakeDocumentConverter.last_convert_path = pdf_path
        FakeDocumentConverter.call_count += 1
        if "explode" in str(pdf_path):
            raise RuntimeError("simulated docling failure")
        return FakeConversionResult(f"# Parsed content of {pdf_path}")


@pytest.fixture
def fake_docling(monkeypatch):
    FakeDocumentConverter.last_convert_path = None
    FakeDocumentConverter.call_count = 0
    fake_module = types.ModuleType("docling.document_converter")
    fake_module.DocumentConverter = FakeDocumentConverter
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_module)
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    return FakeDocumentConverter


class TestParseDoc:
    def test_no_pdf_path_raises(self, isolated_config):
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=None)
        with pytest.raises(ValueError, match="no PDF to parse"):
            docling_parse.parse_doc(doc)

    def test_writes_markdown_output(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        out_path = docling_parse.parse_doc(doc)

        assert out_path == isolated_config.DOCLING_DIR / "a2024.md"
        assert "Parsed content" in out_path.read_text()
        assert FakeDocumentConverter.last_convert_path == str(pdf)

    def test_source_pdfs_doc_id_gets_safe_filename(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="doc:extra", citekey=None, source="source-pdfs", title="t", pdf_path=str(pdf))
        out_path = docling_parse.parse_doc(doc)
        assert out_path == isolated_config.DOCLING_DIR / "doc_extra.md"


class TestIncrementalSkip:
    def test_second_call_with_unchanged_pdf_skips_docling(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 v1")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        first = docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 1

        second = docling_parse.parse_doc(doc)
        assert second == first
        assert FakeDocumentConverter.call_count == 1  # not called again

    def test_changed_pdf_content_triggers_reparse(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 v1")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 1

        # A different size (and, on filesystems with coarse mtime
        # resolution, possibly the same mtime) must still be detected.
        pdf.write_bytes(b"%PDF-1.4 v1 -- now with more bytes")
        docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 2

    def test_deleted_output_forces_reparse_even_if_cache_matches(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 v1")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        out_path = docling_parse.parse_doc(doc)
        out_path.unlink()

        docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 2
        assert out_path.exists()

    def test_failed_parse_does_not_poison_the_cache(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "explode.pdf"
        pdf.write_bytes(b"%PDF-1.4 broken")
        doc = CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=str(pdf))

        with pytest.raises(RuntimeError, match="simulated docling failure"):
            docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 1

        with pytest.raises(RuntimeError, match="simulated docling failure"):
            docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 2  # retried, not skipped

    def test_parse_corpus_shares_one_cache_load_and_save_across_docs(
        self, isolated_config, fake_docling, tmp_path
    ):
        pdf_a = tmp_path / "a.pdf"
        pdf_a.write_bytes(b"%PDF a")
        pdf_b = tmp_path / "b.pdf"
        pdf_b.write_bytes(b"%PDF b")
        docs = [
            CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf_a)),
            CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=str(pdf_b)),
        ]

        docling_parse.parse_corpus(docs)
        assert FakeDocumentConverter.call_count == 2
        assert isolated_config.DOCLING_CACHE_PATH.exists()

        # A fresh parse_corpus call (simulating the next `full_pipeline.py`
        # run) must read that persisted cache and skip both docs.
        docling_parse.parse_corpus(docs)
        assert FakeDocumentConverter.call_count == 2


class TestCacheLoading:
    def test_missing_cache_file_is_empty(self, isolated_config):
        assert docling_parse._load_cache() == {}

    def test_corrupt_json_is_treated_as_empty(self, isolated_config):
        isolated_config.CONTENT_DIR.mkdir(parents=True)
        isolated_config.DOCLING_CACHE_PATH.write_text("{not valid json")
        assert docling_parse._load_cache() == {}

    def test_non_dict_top_level_is_treated_as_empty(self, isolated_config):
        isolated_config.CONTENT_DIR.mkdir(parents=True)
        isolated_config.DOCLING_CACHE_PATH.write_text("[1, 2, 3]")
        assert docling_parse._load_cache() == {}

    def test_malformed_entries_are_dropped_not_raised(self, isolated_config):
        isolated_config.CONTENT_DIR.mkdir(parents=True)
        isolated_config.DOCLING_CACHE_PATH.write_text(json.dumps({
            "good2024": [123, 456],
            "bad_not_a_list": "oops",
            "bad_wrong_length": [1, 2, 3],
            "bad_non_int": [1, "two"],
        }))
        assert docling_parse._load_cache() == {"good2024": [123, 456]}

    def test_corrupt_cache_does_not_abort_the_batch(self, isolated_config, fake_docling, tmp_path):
        isolated_config.CONTENT_DIR.mkdir(parents=True)
        isolated_config.DOCLING_CACHE_PATH.write_text("{not valid json")
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        status = docling_parse.parse_corpus([doc])

        assert status["a2024"].startswith("ok:")


class TestSaveCacheFailureIsNonFatal:
    def test_save_cache_warns_and_does_not_raise(self, isolated_config, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(docling_parse.os, "replace", boom)

        docling_parse._save_cache({"a2024": [1, 2]})

        assert "WARNING" in capsys.readouterr().out

    def test_parse_doc_still_returns_output_when_cache_save_fails(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(docling_parse.os, "replace", boom)

        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        out_path = docling_parse.parse_doc(doc)

        assert out_path.exists()
        assert "Parsed content" in out_path.read_text()


class TestParseCorpus:
    def test_reports_per_doc_status_without_aborting_batch(self, isolated_config, fake_docling, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"%PDF a")
        (tmp_path / "explode.pdf").write_bytes(b"%PDF explode")
        good = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(tmp_path / "a.pdf"))
        bad = CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=str(tmp_path / "explode.pdf"))
        no_pdf = CorpusDoc(doc_id="c2024", citekey="c2024", source="bib", title="t", pdf_path=None)

        status = docling_parse.parse_corpus([good, bad, no_pdf])

        assert status["a2024"].startswith("ok:")
        assert status["b2024"].startswith("error:")
        assert "simulated docling failure" in status["b2024"]
        assert status["c2024"] == "error: c2024: no PDF to parse"

"""src/heavy/docling_parse.py: layout-aware PDF parsing via Docling.

Docling is mocked via sys.modules (imported lazily inside parse_doc, not
at module top), so these stay fast and don't need real model weights.
"""

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

    def convert(self, pdf_path):
        FakeDocumentConverter.last_convert_path = pdf_path
        if "explode" in str(pdf_path):
            raise RuntimeError("simulated docling failure")
        return FakeConversionResult(f"# Parsed content of {pdf_path}")


@pytest.fixture
def fake_docling(monkeypatch):
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
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        out_path = docling_parse.parse_doc(doc)

        assert out_path == isolated_config.DOCLING_DIR / "a2024.md"
        assert "Parsed content" in out_path.read_text()
        assert FakeDocumentConverter.last_convert_path == str(pdf)

    def test_source_pdfs_doc_id_gets_safe_filename(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        doc = CorpusDoc(doc_id="doc:extra", citekey=None, source="source-pdfs", title="t", pdf_path=str(pdf))
        out_path = docling_parse.parse_doc(doc)
        assert out_path == isolated_config.DOCLING_DIR / "doc_extra.md"


class TestParseCorpus:
    def test_reports_per_doc_status_without_aborting_batch(self, isolated_config, fake_docling, tmp_path):
        good = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(tmp_path / "a.pdf"))
        bad = CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=str(tmp_path / "explode.pdf"))
        no_pdf = CorpusDoc(doc_id="c2024", citekey="c2024", source="bib", title="t", pdf_path=None)

        status = docling_parse.parse_corpus([good, bad, no_pdf])

        assert status["a2024"].startswith("ok:")
        assert status["b2024"].startswith("error:")
        assert "simulated docling failure" in status["b2024"]
        assert status["c2024"] == "error: c2024: no PDF to parse"

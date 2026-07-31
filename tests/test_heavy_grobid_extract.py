"""src/heavy/grobid_extract.py: bibliographic-quality extraction via a
running GROBID REST service. `requests` is a real top-level import here
(unlike docling/sentence-transformers' lazy ones), so only the network
calls need mocking, not the import itself."""

import pytest
import requests

from src.heavy import grobid_extract
from src.heavy.corpus import CorpusDoc


class FakeResponse:
    def __init__(self, status_code=200, text="<TEI>fake</TEI>"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


class TestIsAvailable:
    def test_true_on_200(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse(200))
        assert grobid_extract.is_available() is True

    def test_false_on_non_200(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse(503))
        assert grobid_extract.is_available() is False

    def test_false_on_connection_error(self, monkeypatch):
        def raise_connection_error(url, timeout):
            raise requests.exceptions.ConnectionError("no route to host")
        monkeypatch.setattr(requests, "get", raise_connection_error)
        assert grobid_extract.is_available() is False


class TestExtractHeader:
    def test_raises_when_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: False)
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(tmp_path / "a.pdf"))
        with pytest.raises(grobid_extract.GrobidUnavailable):
            grobid_extract.extract_header(doc)

    def test_raises_when_no_pdf(self, monkeypatch):
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: True)
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=None)
        with pytest.raises(ValueError, match="no PDF to send to GROBID"):
            grobid_extract.extract_header(doc)

    def test_success_posts_pdf_and_returns_tei(self, monkeypatch, tmp_path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: True)

        posted = {}

        def fake_post(url, files, timeout):
            posted["url"] = url
            posted["filename"] = files["input"].name
            return FakeResponse(200, text="<TEI>real header</TEI>")

        monkeypatch.setattr(requests, "post", fake_post)
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        result = grobid_extract.extract_header(doc)
        assert result == "<TEI>real header</TEI>"
        assert posted["url"].endswith("/api/processHeaderDocument")
        assert posted["filename"] == str(pdf)

    def test_http_error_status_raises(self, monkeypatch, tmp_path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: True)
        monkeypatch.setattr(requests, "post", lambda url, files, timeout: FakeResponse(500))

        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))
        with pytest.raises(requests.exceptions.HTTPError):
            grobid_extract.extract_header(doc)


class TestExtractCorpus:
    def test_unavailable_marks_every_doc(self, monkeypatch):
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: False)
        docs = [
            CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=None),
            CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=None),
        ]
        status = grobid_extract.extract_corpus(docs)
        assert status == {"a2024": "unavailable", "b2024": "unavailable"}

    def test_mixed_success_and_failure_writes_tei_files(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(grobid_extract, "is_available", lambda timeout=None: True)

        good_pdf = tmp_path / "good.pdf"
        good_pdf.write_bytes(b"%PDF-1.4")

        def fake_post(url, files, timeout):
            if "good" not in files["input"].name:
                raise requests.exceptions.ConnectionError("simulated failure")
            return FakeResponse(200, text="<TEI>ok</TEI>")

        monkeypatch.setattr(requests, "post", fake_post)

        bad_pdf = tmp_path / "bad.pdf"
        bad_pdf.write_bytes(b"%PDF-1.4")

        good = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(good_pdf))
        bad = CorpusDoc(doc_id="b2024", citekey="b2024", source="bib", title="t", pdf_path=str(bad_pdf))

        status = grobid_extract.extract_corpus([good, bad])

        assert status["a2024"].startswith("ok:")
        out_file = isolated_config.GROBID_DIR / "a2024.tei.xml"
        assert out_file.read_text() == "<TEI>ok</TEI>"
        assert status["b2024"].startswith("error:")

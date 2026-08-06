"""src/heavy/docling_parse.py: layout-aware PDF parsing via Docling.

Docling is mocked via sys.modules (imported lazily inside parse_doc, not
at module top), so these stay fast and don't need real model weights.
"""

import contextlib
import json
import multiprocessing
import re
import sys
import types
from pathlib import Path

import pytest

from src import config, pdf_text
from src.heavy import docling_parse
from src.heavy.corpus import CorpusDoc


# Docling names artifacts image_<index>_<sha256>.png. The digest is
# irrelevant to these tests, so a constant stand-in keeps the expected
# filename readable in both the fake and the assertions.
FAKE_IMAGE_NAME = "image_{i:06d}_" + "a" * 64 + ".png"


class FakePicture:
    """Enough of a docling PictureItem for _figure_records: a caption and
    a page provenance.

    `image.uri.path` deliberately carries a base64 `data:` payload, which
    is what real Docling holds -- `save_as_markdown` does NOT rewrite it
    to the written filename. An earlier fake returned a tidy path here,
    which let a bug through: the records inlined ~17MB of base64 across
    one real corpus instead of naming the files.
    """

    def __init__(self, caption="", page=1):
        self._caption = caption
        self.prov = [types.SimpleNamespace(page_no=page)] if page is not None else []
        self.image = types.SimpleNamespace(
            uri=types.SimpleNamespace(path="image/png;base64,iVBORw0KGgoAAAANSUhEUg" + "A" * 200)
        )

    def caption_text(self, _doc):
        return self._caption


class FakeTextItem:
    """A docling TextItem: label, text, and prov[0] with page + bbox."""

    def __init__(self, text, label="text", page=1):
        self.text = text
        self.label = f"DocItemLabel.{label.upper()}"
        if page is None:
            self.prov = []
        else:
            bbox = types.SimpleNamespace(l=1.0, t=2.0, r=3.0, b=4.0)
            self.prov = [types.SimpleNamespace(page_no=page, bbox=bbox)]


class FakeDocument:
    last_image_mode = None

    def __init__(self, markdown, pictures=None, texts=None):
        self._markdown = markdown
        self.pictures = pictures if pictures is not None else []
        self.texts = texts if texts is not None else []

    def export_to_markdown(self):
        return self._markdown

    def save_as_markdown(self, path, image_mode=None):
        """Mirrors the real behaviour: writes ABSOLUTE artifact paths."""
        FakeDocument.last_image_mode = image_mode
        out = Path(path)
        artifacts = out.parent / f"{out.stem}_artifacts"
        body = self._markdown
        for i in range(len(self.pictures)):
            # Built in two steps rather than as a nested f-string: PEP 701
            # makes the nested form legal on this project's Python (^3.12),
            # but it reads badly and is a syntax error on 3.11 and older.
            filename = FAKE_IMAGE_NAME.format(i=i)
            body += f"\n\n![Image]({artifacts / filename})"
        # NB: on Windows that join yields backslashes, which is exactly
        # the real behaviour the relativiser has to normalise.
        out.write_text(body)


class FakeConversionResult:
    def __init__(self, markdown, pictures=None, texts=None):
        self.document = FakeDocument(markdown, pictures, texts)


class FakeDocumentConverter:
    last_convert_path = None
    last_format_options = None
    call_count = 0
    # Constructions, as distinct from call_count's conversions: every
    # construction re-initialises Docling's real layout/table/OCR models,
    # which is the cost parse_corpus's hoisted converter exists to pay once.
    build_count = 0
    pictures = []
    texts = []

    def __init__(self, format_options=None):
        FakeDocumentConverter.last_format_options = format_options
        FakeDocumentConverter.build_count += 1

    def convert(self, pdf_path):
        FakeDocumentConverter.last_convert_path = pdf_path
        FakeDocumentConverter.call_count += 1
        if "explode" in str(pdf_path):
            raise RuntimeError("simulated docling failure")
        return FakeConversionResult(
            f"# Parsed content of {pdf_path}", FakeDocumentConverter.pictures,
            FakeDocumentConverter.texts,
        )


@pytest.fixture
def fake_docling(monkeypatch):
    FakeDocumentConverter.last_convert_path = None
    FakeDocumentConverter.last_format_options = None
    FakeDocumentConverter.call_count = 0
    FakeDocumentConverter.build_count = 0
    docling_parse._reset_worker_converter()
    FakeDocumentConverter.pictures = []
    FakeDocumentConverter.texts = []
    FakeDocument.last_image_mode = None

    converter_mod = types.ModuleType("docling.document_converter")
    converter_mod.DocumentConverter = FakeDocumentConverter
    converter_mod.PdfFormatOption = lambda pipeline_options=None: types.SimpleNamespace(
        pipeline_options=pipeline_options
    )
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = types.SimpleNamespace(PDF="pdf")
    pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.PdfPipelineOptions = lambda: types.SimpleNamespace(
        generate_picture_images=False, images_scale=1.0, do_ocr=True,
        accelerator_options=None, document_timeout=None,
    )
    accelerator = types.ModuleType("docling.datamodel.accelerator_options")
    accelerator.AcceleratorOptions = lambda num_threads=None, device=None: types.SimpleNamespace(
        num_threads=num_threads, device=device
    )
    core_doc = types.ModuleType("docling_core.types.doc")
    core_doc.ImageRefMode = types.SimpleNamespace(REFERENCED="referenced")

    for name, mod in [
        ("docling", types.ModuleType("docling")),
        ("docling.document_converter", converter_mod),
        ("docling.datamodel", types.ModuleType("docling.datamodel")),
        ("docling.datamodel.base_models", base_models),
        ("docling.datamodel.pipeline_options", pipeline_options),
        ("docling.datamodel.accelerator_options", accelerator),
        ("docling_core", types.ModuleType("docling_core")),
        ("docling_core.types", types.ModuleType("docling_core.types")),
        ("docling_core.types.doc", core_doc),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
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


class TestImageExtraction:
    @pytest.fixture
    def images_on(self, isolated_config, monkeypatch):
        monkeypatch.setattr(isolated_config, "DOCLING_IMAGES", True)
        return isolated_config

    def _doc(self, tmp_path, citekey="richstein_characterizing_2024"):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        return CorpusDoc(doc_id=citekey, citekey=citekey, source="bib", title="t", pdf_path=str(pdf))

    def test_images_off_uses_export_and_writes_no_figure_index(
        self, isolated_config, fake_docling, tmp_path
    ):
        FakeDocumentConverter.pictures = [FakePicture("Figure 1. A plot", page=3)]
        docling_parse.parse_doc(self._doc(tmp_path))

        assert FakeDocument.last_image_mode is None  # save_as_markdown never called
        # Pipeline options are always passed now (they carry do_ocr), so
        # "images off" is asserted on the option itself rather than on the
        # converter having been built bare.
        assert FakeDocumentConverter.last_format_options["pdf"].pipeline_options.generate_picture_images is False
        assert not (isolated_config.DOCLING_DIR / "richstein_characterizing_2024.figures.json").exists()

    def test_ocr_is_off_by_default(self, isolated_config, fake_docling, tmp_path):
        docling_parse.parse_doc(self._doc(tmp_path))
        assert FakeDocumentConverter.last_format_options["pdf"].pipeline_options.do_ocr is False

    def test_ocr_can_be_turned_back_on(self, isolated_config, fake_docling, monkeypatch, tmp_path):
        monkeypatch.setattr(isolated_config, "PARSER_OCR", True)
        docling_parse.parse_doc(self._doc(tmp_path))
        assert FakeDocumentConverter.last_format_options["pdf"].pipeline_options.do_ocr is True

    def test_images_on_requests_bitmaps_and_referenced_mode(self, images_on, fake_docling, tmp_path):
        FakeDocumentConverter.pictures = [FakePicture("Figure 1. A plot", page=3)]
        docling_parse.parse_doc(self._doc(tmp_path))

        opts = FakeDocumentConverter.last_format_options["pdf"].pipeline_options
        assert opts.generate_picture_images is True
        assert opts.images_scale == images_on.DOCLING_IMAGE_SCALE
        assert FakeDocument.last_image_mode == "referenced"

    def test_figure_index_cites_by_the_papers_own_number(self, images_on, fake_docling, tmp_path):
        FakeDocumentConverter.pictures = [
            FakePicture("Figure 3. Sensor placement", page=7),
        ]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads(
            (images_on.DOCLING_DIR / "richstein_characterizing_2024.figures.json").read_text()
        )
        assert records[0]["cite"] == "Figure 3 of [@richstein_characterizing_2024], p.7"
        assert records[0]["page"] == 7
        assert records[0]["caption"] == "Figure 3. Sensor placement"

    @pytest.mark.parametrize("caption,expected", [
        # Chapter-scoped numbering. Real captions from
        # larsen_engineering_2024, which first exposed this: matching only
        # the leading integer collapsed all four onto "Fig 1".
        ("Fig. 1.1: A CPS composed of Physical and Computational parts", "Figure 1.1"),
        ("Fig. 1.2: Overview of a DT-Enabled System concept.", "Figure 1.2"),
        ("Fig. 1.4: Fields related to Digital Twins.", "Figure 1.4"),
        # Plain, sub-figure, deeper nesting, and the other label words.
        ("Figure 3. Sensor placement", "Figure 3"),
        ("Figure 2a. Detail view", "Figure 2a"),
        ("Fig 10.2.3 Something nested", "Figure 10.2.3"),
        ("Table 2: Comparison of approaches", "Table 2"),
        ("Scheme 4 - reaction pathway", "Scheme 4"),
    ])
    def test_caption_number_is_captured_whole(self, images_on, fake_docling, tmp_path, caption, expected):
        FakeDocumentConverter.pictures = [FakePicture(caption, page=3)]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads(
            (images_on.DOCLING_DIR / "richstein_characterizing_2024.figures.json").read_text()
        )
        assert records[0]["cite"] == f"{expected} of [@richstein_characterizing_2024], p.3"

    def test_distinct_subfigures_do_not_collapse_onto_one_number(self, images_on, fake_docling, tmp_path):
        """The actual regression: four figures, four distinct citations."""
        FakeDocumentConverter.pictures = [
            FakePicture(f"Fig. 1.{n}: caption {n}", page=n + 2) for n in (1, 2, 3, 4)
        ]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads(
            (images_on.DOCLING_DIR / "richstein_characterizing_2024.figures.json").read_text()
        )
        cites = [r["cite"] for r in records]
        assert len(set(cites)) == 4, cites
        assert "Figure 1.3 of [@richstein_characterizing_2024], p.5" in cites


    def test_image_field_names_the_file_and_never_inlines_base64(self, images_on, fake_docling, tmp_path):
        """The regression: pic.image.uri is a base64 data: URI that
        save_as_markdown does not rewrite, so reading it put the whole
        PNG in the JSON (~17MB across one real corpus)."""
        FakeDocumentConverter.pictures = [FakePicture("Figure 1. A plot", page=2)]
        docling_parse.parse_doc(self._doc(tmp_path))

        raw = (images_on.DOCLING_DIR / "richstein_characterizing_2024.figures.json").read_text()
        assert "base64" not in raw
        records = json.loads(raw)
        assert records[0]["image"] == f"richstein_characterizing_2024_artifacts/{FAKE_IMAGE_NAME.format(i=0)}"

    def test_markdown_image_refs_are_relative_to_the_md(self, images_on, fake_docling, tmp_path):
        """Docling writes absolute paths, which bake this host's layout
        into content/docling/ and break if the folder moves."""
        FakeDocumentConverter.pictures = [FakePicture("Figure 1", page=1), FakePicture("Figure 2", page=2)]
        out_path = docling_parse.parse_doc(self._doc(tmp_path))

        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", out_path.read_text())
        assert len(refs) == 2
        assert all(not r.startswith("/") for r in refs), refs
        assert all(r.startswith("richstein_characterizing_2024_artifacts/") for r in refs), refs

    def test_already_relative_ref_is_passed_through_unchanged(self, images_on, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("text\n\n![Image](doc_artifacts/img.png)\n")

        names = docling_parse._relativise_image_refs(md)

        assert names == ["doc_artifacts/img.png"]
        assert "![Image](doc_artifacts/img.png)" in md.read_text()

    def test_relative_refs_use_forward_slashes_on_every_platform(self, images_on, tmp_path):
        """A Markdown image reference is URL-ish and must use forward
        slashes. Path.relative_to() renders backslashes on Windows, which
        would make content/docling/ readable only on the box that wrote
        it -- caught by this repo's windows-latest CI leg."""
        md = tmp_path / "doc.md"
        nested = md.parent / "doc_artifacts" / "sub" / "img.png"
        md.write_text(f"text\n\n![Image]({nested})\n")

        names = docling_parse._relativise_image_refs(md)

        assert names == ["doc_artifacts/sub/img.png"]
        assert "\\" not in md.read_text()

    def test_image_ref_outside_the_md_tree_is_left_alone(self, images_on, tmp_path):
        """An absolute path pointing somewhere else entirely stays put,
        rather than becoming a fragile chain of `../`."""
        md = tmp_path / "doc.md"
        outside = tmp_path.parent / "elsewhere" / "img.png"
        md.write_text(f"text\n\n![Image]({outside})\n")

        names = docling_parse._relativise_image_refs(md)

        assert names == [str(outside)]
        assert str(outside) in md.read_text()

    def test_image_is_dropped_when_ref_count_disagrees_with_picture_count(self, images_on, fake_docling, tmp_path):
        """Rather than pair a figure with someone else's image."""
        names = ["only_one.png"]
        pics = [FakePicture("Figure 1", page=1), FakePicture("Figure 2", page=2)]
        doc = self._doc(tmp_path)
        records = docling_parse._figure_records(doc, types.SimpleNamespace(pictures=pics), names)
        assert [r["image"] for r in records] == [None, None]
        assert records[0]["cite"].startswith("Figure 1 of")  # citation still works

    def test_uncaptioned_picture_is_cited_by_page_not_an_invented_number(
        self, images_on, fake_docling, tmp_path
    ):
        """Publisher logos and licence badges are pictures too, so the Nth
        picture is routinely not the paper's Figure N -- never guess."""
        FakeDocumentConverter.pictures = [FakePicture("", page=1)]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads(
            (images_on.DOCLING_DIR / "richstein_characterizing_2024.figures.json").read_text()
        )
        assert records[0]["cite"] == "the figure on p.1 of [@richstein_characterizing_2024]"
        assert records[0]["caption"] is None

    def test_source_pdfs_figure_is_marked_not_citable(self, images_on, fake_docling, tmp_path):
        """A doc: prefixed id can never be a citekey (AGENTS.md), so its
        figures must not render as a citable [@key]."""
        FakeDocumentConverter.pictures = [FakePicture("Figure 1. A plot", page=2)]
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="doc:extra", citekey=None, source="source-pdfs", title="t", pdf_path=str(pdf))

        docling_parse.parse_doc(doc)

        records = json.loads((images_on.DOCLING_DIR / "doc_extra.figures.json").read_text())
        assert "[@" not in records[0]["cite"]
        assert "not citable" in records[0]["cite"]


class TestPassageSidecar:
    def _doc(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        return CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

    def test_written_for_every_doc_with_page_and_bbox(self, isolated_config, fake_docling, tmp_path):
        FakeDocumentConverter.texts = [
            FakeTextItem("Body paragraph one.", label="text", page=2),
            FakeTextItem("2 Related Work", label="section_header", page=3),
        ]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads((isolated_config.DOCLING_DIR / "a2024.passages.json").read_text())
        assert [r["text"] for r in records] == ["Body paragraph one.", "2 Related Work"]
        assert records[0]["page"] == 2
        assert records[0]["bbox"] == [1.0, 2.0, 3.0, 4.0]

    def test_excludes_running_heads_and_captions(self, isolated_config, fake_docling, tmp_path):
        """A journal name repeated on every page would otherwise let a
        claim 'match' seventeen times over."""
        FakeDocumentConverter.texts = [
            FakeTextItem("Designs 2024, 8, 8", label="page_header", page=1),
            FakeTextItem("Figure 1. A plot", label="caption", page=1),
            FakeTextItem("17", label="page_footer", page=1),
            FakeTextItem("Real prose.", label="text", page=1),
        ]
        docling_parse.parse_doc(self._doc(tmp_path))

        records = json.loads((isolated_config.DOCLING_DIR / "a2024.passages.json").read_text())
        assert [r["text"] for r in records] == ["Real prose."]

    def test_written_even_with_images_off(self, isolated_config, fake_docling, tmp_path):
        assert isolated_config.DOCLING_IMAGES is False
        FakeDocumentConverter.texts = [FakeTextItem("Prose.", label="text", page=1)]
        docling_parse.parse_doc(self._doc(tmp_path))
        assert (isolated_config.DOCLING_DIR / "a2024.passages.json").exists()

    def test_item_without_provenance_still_recorded(self, isolated_config, fake_docling, tmp_path):
        FakeDocumentConverter.texts = [FakeTextItem("Prose.", label="text", page=None)]
        docling_parse.parse_doc(self._doc(tmp_path))
        records = json.loads((isolated_config.DOCLING_DIR / "a2024.passages.json").read_text())
        assert records[0]["page"] is None
        assert "bbox" not in records[0]


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

    def test_deleted_passages_sidecar_forces_reparse(self, isolated_config, fake_docling, tmp_path):
        """The .md alone isn't proof the run's outputs are intact -- a
        deleted sidecar would otherwise stay missing forever, since the
        fingerprint only says the input PDF is unchanged."""
        FakeDocumentConverter.texts = [FakeTextItem("Prose.", label="text", page=1)]
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 1
        sidecar = isolated_config.DOCLING_DIR / "a2024.passages.json"
        sidecar.unlink()

        docling_parse.parse_doc(doc)

        assert FakeDocumentConverter.call_count == 2
        assert sidecar.exists()

    def test_deleted_figures_sidecar_forces_reparse_when_images_on(
        self, isolated_config, fake_docling, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(isolated_config, "DOCLING_IMAGES", True)
        FakeDocumentConverter.pictures = [FakePicture("Figure 1", page=1)]
        FakeDocumentConverter.texts = [FakeTextItem("Prose.", label="text", page=1)]
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        docling_parse.parse_doc(doc)
        assert FakeDocumentConverter.call_count == 1
        (isolated_config.DOCLING_DIR / "a2024.figures.json").unlink()

        docling_parse.parse_doc(doc)

        assert FakeDocumentConverter.call_count == 2

    def test_figures_sidecar_not_required_when_images_off(self, isolated_config, fake_docling, tmp_path):
        """Images off never writes figures.json, so requiring it would
        re-parse the whole corpus on every run."""
        FakeDocumentConverter.texts = [FakeTextItem("Prose.", label="text", page=1)]
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        doc = CorpusDoc(doc_id="a2024", citekey="a2024", source="bib", title="t", pdf_path=str(pdf))

        docling_parse.parse_doc(doc)
        docling_parse.parse_doc(doc)

        assert FakeDocumentConverter.call_count == 1

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
        # One converter for the whole corpus, not one per document: each
        # construction re-initialises the layout/table/OCR models, 16.5s
        # of measured cold start that a 501-PDF corpus would otherwise
        # pay 501 times.
        assert FakeDocumentConverter.build_count == 1
        assert isolated_config.DOCLING_CACHE_PATH.exists()

        # A fresh parse_corpus call (simulating the next `full_pipeline.py`
        # run) must read that persisted cache and skip both docs.
        FakeDocumentConverter.build_count = 0
        docling_parse.parse_corpus(docs)
        assert FakeDocumentConverter.call_count == 2
        # ...and must not stand the models up at all to discover that.
        # The converter is built on first *use*, not on entry, so the
        # common case -- re-running a corpus that hasn't changed -- costs
        # no model load whatsoever.
        assert FakeDocumentConverter.build_count == 0


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

    def _write_cache(self, isolated_config, items, **overrides):
        payload = {
            "version": docling_parse._CACHE_VERSION,
            "images": isolated_config.DOCLING_IMAGES,
            "ocr": isolated_config.PARSER_OCR,
            "items": items,
        }
        payload.update(overrides)
        isolated_config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        isolated_config.DOCLING_CACHE_PATH.write_text(json.dumps(payload))

    def test_malformed_entries_are_dropped_not_raised(self, isolated_config):
        self._write_cache(isolated_config, {
            "good2024": [123, 456],
            "bad_not_a_list": "oops",
            "bad_wrong_length": [1, 2, 3],
            "bad_non_int": [1, "two"],
        })
        assert docling_parse._load_cache() == {"good2024": [123, 456]}

    def test_stale_schema_version_invalidates_whole_cache(self, isolated_config):
        self._write_cache(
            isolated_config, {"good2024": [123, 456]},
            version=docling_parse._CACHE_VERSION + 1,
        )
        assert docling_parse._load_cache() == {}

    def test_non_dict_items_is_treated_as_empty(self, isolated_config):
        self._write_cache(isolated_config, ["not", "a", "dict"])
        assert docling_parse._load_cache() == {}

    def test_unversioned_legacy_cache_is_invalidated(self, isolated_config):
        """Pre-versioning caches were a bare {doc_id: fingerprint} dict."""
        isolated_config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        isolated_config.DOCLING_CACHE_PATH.write_text(json.dumps({"good2024": [123, 456]}))
        assert docling_parse._load_cache() == {}

    def test_toggling_images_invalidates_whole_cache(self, isolated_config, monkeypatch):
        """The trap this guards: DOCLING_IMAGES changes what every .md
        should contain, but the (size, mtime_ns) fingerprint only sees
        the PDF -- so without this the old image-less output is served
        forever."""
        self._write_cache(isolated_config, {"good2024": [123, 456]})
        assert docling_parse._load_cache() == {"good2024": [123, 456]}

        monkeypatch.setattr(isolated_config, "DOCLING_IMAGES", not isolated_config.DOCLING_IMAGES)
        assert docling_parse._load_cache() == {}

    def test_toggling_ocr_invalidates_whole_cache(self, isolated_config, monkeypatch):
        """Same trap as DOCLING_IMAGES above, on a second axis: OCR
        changes what every .md should contain (it is the difference
        between reading a scan and not), while the (size, mtime_ns)
        fingerprint still only sees the PDF."""
        self._write_cache(isolated_config, {"good2024": [123, 456]})
        assert docling_parse._load_cache() == {"good2024": [123, 456]}

        monkeypatch.setattr(isolated_config, "PARSER_OCR", not isolated_config.PARSER_OCR)
        assert docling_parse._load_cache() == {}

    def test_save_then_load_round_trips(self, isolated_config):
        isolated_config.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        docling_parse._save_cache({"a2024": [1, 2]})
        assert docling_parse._load_cache() == {"a2024": [1, 2]}

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


def _thread_executor(workers):
    """A real ProcessPoolExecutor would run parse_one in a child
    interpreter, where this process's sys.modules fakes don't exist -- the
    fake docling would silently not be used. Swapping the executor keeps
    the concurrency real while leaving the fakes visible."""
    from concurrent.futures import ThreadPoolExecutor

    return ThreadPoolExecutor(max_workers=workers)


class TestParseCorpusParallel:
    @pytest.fixture(autouse=True)
    def _four_workers(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(docling_parse, "_executor_for", _thread_executor)

    def _docs(self, tmp_path, n=5):
        docs = []
        for i in range(n):
            pdf = tmp_path / f"p{i}.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * (50 * i))
            docs.append(CorpusDoc(doc_id=f"d{i}", citekey=f"d{i}", source="bib",
                                  title="t", pdf_path=str(pdf)))
        return docs

    def test_every_document_is_parsed(self, isolated_config, fake_docling, tmp_path):
        status = docling_parse.parse_corpus(self._docs(tmp_path))
        assert len(status) == 5
        assert all(v.startswith("ok:") for v in status.values())
        assert FakeDocumentConverter.call_count == 5

    def test_the_parent_owns_every_cache_write(self, isolated_config, fake_docling, tmp_path):
        """Workers can't share the parent's cache dict, so they hand back
        a fingerprint and the parent records it -- the same shape as
        src/sync.py keeping every ledger write on the main process."""
        docs = self._docs(tmp_path)
        docling_parse.parse_corpus(docs)

        cache = json.loads(isolated_config.DOCLING_CACHE_PATH.read_text())
        assert set(cache["items"]) == {d.doc_id for d in docs}

        # ...and a second run therefore skips all of them.
        FakeDocumentConverter.call_count = 0
        docling_parse.parse_corpus(docs)
        assert FakeDocumentConverter.call_count == 0

    def test_a_cached_document_is_never_sent_to_a_worker(
        self, isolated_config, fake_docling, tmp_path
    ):
        """Dispatching a cached doc would cost a process and a model load
        to discover there was nothing to do."""
        docs = self._docs(tmp_path)
        docling_parse.parse_corpus(docs)

        submitted = []
        real_parse_one = docling_parse.parse_one
        docling_parse.parse_one = lambda job: submitted.append(job[0].doc_id) or real_parse_one(job)
        try:
            status = docling_parse.parse_corpus(docs)
        finally:
            docling_parse.parse_one = real_parse_one
        assert submitted == []
        assert all(v.startswith("ok:") for v in status.values())

    def test_one_failure_does_not_abort_the_batch(self, isolated_config, fake_docling, tmp_path):
        docs = self._docs(tmp_path)
        bad = tmp_path / "explode.pdf"
        bad.write_bytes(b"%PDF")
        docs.append(CorpusDoc(doc_id="bad", citekey="bad", source="bib",
                              title="t", pdf_path=str(bad)))

        status = docling_parse.parse_corpus(docs)
        assert status["bad"].startswith("error:")
        assert all(status[f"d{i}"].startswith("ok:") for i in range(5))

        # A failed doc must not be cached, or it would never be retried.
        cache = json.loads(isolated_config.DOCLING_CACHE_PATH.read_text())
        assert "bad" not in cache["items"]

    def test_workers_get_a_thread_budget(self, isolated_config, fake_docling, tmp_path):
        docling_parse.parse_corpus(self._docs(tmp_path, n=2))
        opts = FakeDocumentConverter.last_format_options["pdf"].pipeline_options
        assert opts.accelerator_options.num_threads == pdf_text.docling_threads(4)

    def test_biggest_document_is_submitted_first(self, isolated_config, fake_docling, tmp_path):
        submitted = []
        real_parse_one = docling_parse.parse_one
        docling_parse.parse_one = lambda job: submitted.append(job[0].doc_id) or real_parse_one(job)
        try:
            docling_parse.parse_corpus(self._docs(tmp_path))
        finally:
            docling_parse.parse_one = real_parse_one
        assert submitted == ["d4", "d3", "d2", "d1", "d0"]


class TestParallelHelpers:
    def test_is_cached_is_false_for_an_unreadable_pdf(self, isolated_config, tmp_path):
        """A PDF that vanished can't be fingerprinted. Treat it as
        not-cached so the parse runs and reports the real error, rather
        than crashing the dispatch loop."""
        doc = CorpusDoc(doc_id="gone", citekey="gone", source="bib", title="t",
                        pdf_path=str(tmp_path / "gone.pdf"))
        assert docling_parse._is_cached(doc, {"gone": [1, 2]}) is False

    def test_pdf_size_of_a_missing_file_sorts_last(self, tmp_path):
        assert docling_parse._pdf_size(str(tmp_path / "gone.pdf")) == 0

    def test_pdf_size_of_none_sorts_last(self):
        """corpus docs without a PDF never reach the pool, but the sort
        key must not raise if one does."""
        assert docling_parse._pdf_size(None) == 0

    def test_executor_claims_a_gpu_per_worker(self, monkeypatch):
        """Asserted through a recording stub rather than the executor's
        private _initializer/_initargs/_mp_context, which are CPython
        implementation details that could be renamed."""
        captured = {}

        def record(**kwargs):
            captured.update(kwargs)
            return contextlib.nullcontext()

        monkeypatch.setattr(
            pdf_text, "usable_devices", lambda: ([0, 1, 2, 3], None))
        monkeypatch.setattr(docling_parse, "ProcessPoolExecutor", record)
        with docling_parse._executor_for(2):
            pass

        assert captured["max_workers"] == 2
        assert captured["initializer"] is pdf_text.init_worker
        assert captured["initargs"][2] == [0, 1, 2, 3]
        # Whichever start method pdf_text picked -- asserted against that
        # rather than a literal, so this stays one source of truth with
        # src/sync.py's pool rather than two that can drift apart.
        assert captured["mp_context"].get_start_method() == pdf_text.start_method()[0]

    def test_a_full_card_is_kept_out_of_this_pool_too(self, monkeypatch):
        """Two pool builders, one contract. This one skipped the check
        until PR #40 review caught it."""
        captured = {}
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([1, 2], "  WARNING"))
        monkeypatch.setattr(
            docling_parse, "ProcessPoolExecutor",
            lambda **kwargs: captured.update(kwargs) or contextlib.nullcontext())
        with docling_parse._executor_for(2):
            pass

        assert captured["initargs"][2] == [1, 2]

    def test_the_initargs_actually_work_as_init_worker_arguments(self, monkeypatch):
        """The regression this pool actually had: it passed
        pdf_text.gpu_count() -- an int -- where init_worker wants a list
        of cards, so every worker would have died with "'int' object is
        not iterable" at startup. Invisible to a test that only compares
        initargs to a literal, because the initializer is never run."""
        captured = {}
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([2, 3], None))
        monkeypatch.setattr(
            docling_parse, "ProcessPoolExecutor",
            lambda **kwargs: captured.update(kwargs) or contextlib.nullcontext())
        with docling_parse._executor_for(2):
            pass

        pdf_text._reset_worker_device()
        try:
            pdf_text.init_worker(*captured["initargs"])
            assert pdf_text.worker_device() == "cuda:2"
        finally:
            pdf_text._reset_worker_device()

    def test_a_skipped_card_is_reported_not_swallowed(self, monkeypatch, capsys):
        monkeypatch.setattr(
            pdf_text, "usable_devices",
            lambda: ([1], "  WARNING skipping cuda:0"))
        monkeypatch.setattr(docling_parse, "ProcessPoolExecutor",
                            lambda **kwargs: contextlib.nullcontext())
        with docling_parse._executor_for(2):
            pass

        assert "WARNING skipping cuda:0" in capsys.readouterr().out

    def test_a_start_method_complaint_is_printed_not_swallowed(
        self, monkeypatch, capsys
    ):
        """A pool that quietly falls back to spawn looks identical to one
        that got what was configured, and is ~1.5s slower to start."""
        monkeypatch.setattr(pdf_text, "usable_devices", lambda: ([], None))
        monkeypatch.setattr(
            pdf_text, "process_pool_context",
            lambda: (multiprocessing.get_context("spawn"), "  NOTE fell back"))
        monkeypatch.setattr(docling_parse, "ProcessPoolExecutor",
                            lambda **kwargs: contextlib.nullcontext())
        with docling_parse._executor_for(2):
            pass

        assert "NOTE fell back" in capsys.readouterr().out

    def test_accelerator_options_are_left_alone_without_a_budget(
        self, isolated_config, fake_docling, tmp_path
    ):
        """A single-worker run must reach Docling with its own defaults."""
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        docling_parse.parse_doc(CorpusDoc(doc_id="a", citekey="a", source="bib",
                                          title="t", pdf_path=str(pdf)))
        opts = FakeDocumentConverter.last_format_options["pdf"].pipeline_options
        assert opts.accelerator_options is None

    def test_worker_device_reaches_the_pipeline(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(pdf_text, "_WORKER_DEVICE", "cuda:3")
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        docling_parse.parse_doc(CorpusDoc(doc_id="a", citekey="a", source="bib",
                                          title="t", pdf_path=str(pdf)))
        opts = FakeDocumentConverter.last_format_options["pdf"].pipeline_options
        assert opts.accelerator_options.device == "cuda:3"


class TestParseCorpusParallelEdges:
    @pytest.fixture(autouse=True)
    def _pool(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(docling_parse, "_executor_for", _thread_executor)

    def test_already_cached_docs_are_still_reported_in_a_parallel_run(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        """A mixed run -- some cached, some not -- must report every
        document, not just the ones that went through the pool."""
        monkeypatch.setattr(config, "PARSER_WORKERS", 1)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        docs = []
        for i in range(5):
            pdf = tmp_path / f"p{i}.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * i)
            docs.append(CorpusDoc(doc_id=f"d{i}", citekey=f"d{i}", source="bib",
                                  title="t", pdf_path=str(pdf)))
        docling_parse.parse_corpus(docs[:2])  # warm the cache for d0, d1

        # Three still to parse, so the resolved worker count stays above
        # 1 and the parallel branch is genuinely exercised -- with two
        # cached documents alongside it.
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        FakeDocumentConverter.call_count = 0
        status = docling_parse.parse_corpus(docs)

        assert set(status) == {f"d{i}" for i in range(5)}
        assert all(v.startswith("ok:") for v in status.values())
        assert FakeDocumentConverter.call_count == 3  # only d2, d3, d4 re-parsed

    def test_an_oversized_worker_request_is_reported(
        self, isolated_config, fake_docling, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr(config, "PARSER_WORKERS", 64)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 8)
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        docling_parse.parse_corpus([
            CorpusDoc(doc_id="a", citekey="a", source="bib", title="t", pdf_path=str(pdf)),
            CorpusDoc(doc_id="b", citekey="b", source="bib", title="t", pdf_path=str(pdf)),
        ])
        assert "[parser].workers=64" in capsys.readouterr().out

    def test_a_doc_with_no_pdf_is_reported_not_raised_in_a_parallel_run(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        """A doc with no PDF never reaches the pool -- it has nothing to
        parse -- so it falls into the same branch as the cached ones and
        must be reported there rather than taking down the batch."""
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        docs = [CorpusDoc(doc_id="nopdf", citekey="nopdf", source="bib",
                          title="t", pdf_path=None)]
        for i in range(3):
            pdf = tmp_path / f"p{i}.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * i)
            docs.append(CorpusDoc(doc_id=f"d{i}", citekey=f"d{i}", source="bib",
                                  title="t", pdf_path=str(pdf)))

        status = docling_parse.parse_corpus(docs)
        assert status["nopdf"].startswith("error:")
        assert "no PDF to parse" in status["nopdf"]
        assert all(status[f"d{i}"].startswith("ok:") for i in range(3))


class TestWorkerConverterReuse:
    """A pool worker handles many documents over its life, and
    DocumentConverter keeps its initialized_pipelines cache on the
    *instance* -- so a converter per document reloads every model per
    document, which is exactly the cost the serial path stopped paying in
    v0.12.0. This is the parallel path's version of that guarantee."""

    @pytest.fixture(autouse=True)
    def _reset(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        docling_parse._reset_worker_converter()
        yield
        docling_parse._reset_worker_converter()

    def _doc(self, tmp_path, name):
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(b"%PDF")
        return CorpusDoc(doc_id=name, citekey=name, source="bib", title="t",
                         pdf_path=str(pdf))

    def test_one_converter_serves_every_document_a_worker_handles(
        self, isolated_config, fake_docling, tmp_path
    ):
        for i in range(5):
            docling_parse.parse_one((self._doc(tmp_path, f"d{i}"), 4))
        assert FakeDocumentConverter.build_count == 1
        assert FakeDocumentConverter.call_count == 5

    def test_a_changed_thread_budget_rebuilds_it(self, isolated_config, fake_docling, tmp_path):
        docling_parse.parse_one((self._doc(tmp_path, "a"), 4))
        docling_parse.parse_one((self._doc(tmp_path, "b"), 2))
        assert FakeDocumentConverter.build_count == 2

    def test_a_changed_device_rebuilds_it(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        """Caching on "was one built already" alone would leave a worker
        using a converter pinned to another worker's GPU."""
        monkeypatch.setattr(pdf_text, "_WORKER_DEVICE", "cuda:0")
        docling_parse.parse_one((self._doc(tmp_path, "a"), 4))
        monkeypatch.setattr(pdf_text, "_WORKER_DEVICE", "cuda:1")
        docling_parse.parse_one((self._doc(tmp_path, "b"), 4))
        assert FakeDocumentConverter.build_count == 2

    def test_a_changed_image_setting_rebuilds_it(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        docling_parse.parse_one((self._doc(tmp_path, "a"), 4))
        monkeypatch.setattr(isolated_config, "DOCLING_IMAGES", True)
        docling_parse.parse_one((self._doc(tmp_path, "b"), 4))
        assert FakeDocumentConverter.build_count == 2


class TestParseCorpusInterrupt:
    """Same fix as src/sync.py's: `with executor` waits for every queued
    job, so Ctrl+C over a real corpus drained the whole backlog first."""

    @pytest.fixture(autouse=True)
    def _pool(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(docling_parse, "_executor_for", _thread_executor)

    def _docs(self, tmp_path, n=6):
        docs = []
        for i in range(n):
            pdf = tmp_path / f"p{i}.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * (50 * i))
            docs.append(CorpusDoc(doc_id=f"d{i}", citekey=f"d{i}", source="bib",
                                  title="t", pdf_path=str(pdf)))
        return docs

    def test_interrupt_keeps_finished_work_and_says_so(
        self, isolated_config, fake_docling, monkeypatch, tmp_path, capsys
    ):
        seen = []
        real = docling_parse.parse_one

        def interrupt_after_two(job):
            seen.append(job[0].doc_id)
            if len(seen) == 3:
                raise KeyboardInterrupt
            return real(job)

        monkeypatch.setattr(docling_parse, "parse_one", interrupt_after_two)
        with pytest.raises(KeyboardInterrupt):
            docling_parse.parse_corpus(self._docs(tmp_path))

        out = capsys.readouterr().out
        assert "interrupted after" in out
        # The cache is persisted on the way out, so the documents that did
        # finish are not re-parsed on the next run.
        cache = json.loads(isolated_config.DOCLING_CACHE_PATH.read_text())
        assert len(cache["items"]) >= 1

    def test_progress_is_reported_as_documents_land(
        self, isolated_config, fake_docling, tmp_path, capsys
    ):
        docling_parse.parse_corpus(self._docs(tmp_path))
        out = capsys.readouterr().out
        assert "[1/6]" in out and "[6/6]" in out


class TestHeavyPartialSuccess:
    """The enrichment stage had the same silent-truncation hole src/pdf_text.py
    closed in v1.2.0: convert(raises_on_error=True) raises only on
    FAILURE, so a PARTIAL_SUCCESS returned a document that stops early
    and parse_doc wrote it to content/docling/<doc>.md as if complete.

    That output feeds embeddings, topic modelling and citation
    provenance, so a truncated .md is a source a claim can be checked
    against and silently pass.
    """

    def _doc(self, tmp_path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        return CorpusDoc(doc_id="a", citekey="a", source="bib", title="t",
                         pdf_path=str(pdf))

    def test_partial_success_is_rejected(self, isolated_config, fake_docling, monkeypatch, tmp_path):
        monkeypatch.setattr(
            FakeDocumentConverter, "convert",
            lambda self, p: _PartialResult("PARTIAL_SUCCESS", ["timeout after 10s"]),
        )
        with pytest.raises(RuntimeError, match="PARTIAL_SUCCESS"):
            docling_parse.parse_doc(self._doc(tmp_path))

    def test_no_markdown_is_written_for_a_partial_parse(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            FakeDocumentConverter, "convert",
            lambda self, p: _PartialResult("PARTIAL_SUCCESS", []),
        )
        with pytest.raises(RuntimeError):
            docling_parse.parse_doc(self._doc(tmp_path))
        assert not (isolated_config.DOCLING_DIR / "a.md").exists()

    def test_a_partial_parse_is_reported_and_not_cached_by_parse_corpus(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        """It must come back for retry on the next run, not be recorded
        as done."""
        monkeypatch.setattr(
            FakeDocumentConverter, "convert",
            lambda self, p: _PartialResult("PARTIAL_SUCCESS", ["bad page 3"]),
        )
        status = docling_parse.parse_corpus([self._doc(tmp_path)])
        assert status["a"].startswith("error:")
        cache = json.loads(isolated_config.DOCLING_CACHE_PATH.read_text())
        assert "a" not in cache["items"]

    def test_success_still_parses(self, isolated_config, fake_docling, tmp_path):
        out = docling_parse.parse_doc(self._doc(tmp_path))
        assert out.exists()


class _PartialResult:
    def __init__(self, status_name, messages):
        self.status = types.SimpleNamespace(name=status_name)
        self.errors = [types.SimpleNamespace(error_message=m) for m in messages]
        self.document = FakeDocument("# partial")

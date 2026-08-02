"""src/heavy/docling_parse.py: layout-aware PDF parsing via Docling.

Docling is mocked via sys.modules (imported lazily inside parse_doc, not
at module top), so these stay fast and don't need real model weights.
"""

import json
import re
import sys
import types
from pathlib import Path

import pytest

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
    pictures = []
    texts = []

    def __init__(self, format_options=None):
        FakeDocumentConverter.last_format_options = format_options

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
        generate_picture_images=False, images_scale=1.0
    )
    core_doc = types.ModuleType("docling_core.types.doc")
    core_doc.ImageRefMode = types.SimpleNamespace(REFERENCED="referenced")

    for name, mod in [
        ("docling", types.ModuleType("docling")),
        ("docling.document_converter", converter_mod),
        ("docling.datamodel", types.ModuleType("docling.datamodel")),
        ("docling.datamodel.base_models", base_models),
        ("docling.datamodel.pipeline_options", pipeline_options),
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
        assert FakeDocumentConverter.last_format_options is None  # bare converter
        assert not (isolated_config.DOCLING_DIR / "richstein_characterizing_2024.figures.json").exists()

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

    def _write_cache(self, isolated_config, items, **overrides):
        payload = {
            "version": docling_parse._CACHE_VERSION,
            "images": isolated_config.DOCLING_IMAGES,
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

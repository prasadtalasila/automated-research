"""src/pdf_text.py: dispatches PDF text extraction to whichever backend
config.PARSER names (pdftotext/docling).

docling is mocked via sys.modules (it is imported
lazily inside its _extract_* function, not at module top), matching
tests/test_heavy_docling_parse.py's pattern -- fast, deterministic, and
doesn't need the real package installed.
"""

import importlib.machinery
import shutil
import subprocess
import sys
import types

import pytest

from src import config, pdf_text


class TestExtractTextPdftotext:
    """Fast, deterministic: doesn't require pdftotext on PATH.

    extract_text() calls is_available() (shutil.which("pdftotext"))
    before dispatching to _extract_pdftotext, so without stubbing that
    too, every test below would actually depend on the real binary being
    on PATH regardless of the subprocess.run mock -- true on this repo's
    Linux CI (poppler-utils via os-deps), not guaranteed on every host
    these tests might run on (PR #11 review)."""

    @pytest.fixture(autouse=True)
    def _pdftotext_present(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftotext")

    def test_calls_pdftotext_with_layout_flag(self, isolated_config, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # pdftotext writes the output file itself; simulate that.
            out_path = cmd[-1]
            with open(out_path, "w") as f:
                f.write("extracted text")
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pdf_text.extract_text(str(tmp_path / "in.pdf"), "smith_2024")

        assert calls[0][0] == "pdftotext"
        assert "-layout" in calls[0]
        assert result == isolated_config.PARSED_DIR / "smith_2024.txt"
        assert result.read_text() == "extracted text"

    def test_creates_parsed_dir(self, isolated_config, monkeypatch, tmp_path):
        assert not isolated_config.PARSED_DIR.exists()

        def fake_run(cmd, **kwargs):
            Path_out = cmd[-1]
            with open(Path_out, "w"):
                pass
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")
        assert isolated_config.PARSED_DIR.exists()

    def test_called_process_error_becomes_extraction_error(self, isolated_config, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr="pdftotext: bad PDF")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(pdf_text.ExtractionError, match="bad PDF"):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")


class FakeDoclingDocument:
    def __init__(self, markdown):
        self._markdown = markdown

    def export_to_markdown(self):
        return self._markdown


class FakeDoclingResult:
    def __init__(self, markdown):
        self.document = FakeDoclingDocument(markdown)


class FakeDoclingConverter:
    last_convert_path = None
    # How many times DocumentConverter(...) was *constructed*, which is
    # the thing the converter cache exists to keep at 1: every
    # construction re-initialises Docling's real layout/table/OCR models.
    build_count = 0
    last_format_options = None

    def __init__(self, format_options=None):
        FakeDoclingConverter.build_count += 1
        FakeDoclingConverter.last_format_options = format_options

    def convert(self, pdf_path):
        FakeDoclingConverter.last_convert_path = pdf_path
        if "explode" in str(pdf_path):
            raise RuntimeError("simulated docling failure")
        return FakeDoclingResult(f"# Parsed content of {pdf_path}")

    @staticmethod
    def pipeline_options():
        """The PdfPipelineOptions the last-built converter was handed."""
        return FakeDoclingConverter.last_format_options["pdf"].pipeline_options


@pytest.fixture
def fake_docling(monkeypatch):
    FakeDoclingConverter.last_convert_path = None
    FakeDoclingConverter.build_count = 0
    FakeDoclingConverter.last_format_options = None
    # The cache is module state, so it survives between tests and would
    # otherwise serve one test's converter to the next.
    pdf_text._reset_docling_converter()

    fake_submodule = types.ModuleType("docling.document_converter")
    fake_submodule.DocumentConverter = FakeDoclingConverter
    fake_submodule.PdfFormatOption = lambda pipeline_options=None: types.SimpleNamespace(
        pipeline_options=pipeline_options
    )
    fake_submodule.__spec__ = importlib.machinery.ModuleSpec("docling.document_converter", loader=None)
    fake_package = types.ModuleType("docling")
    # importlib.util.find_spec("docling") (is_available()'s probe) raises
    # ValueError if the name is already in sys.modules with no __spec__
    # set -- a bare types.ModuleType() has none, unlike a normally-
    # imported package.
    fake_package.__spec__ = importlib.machinery.ModuleSpec("docling", loader=None)
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.InputFormat = types.SimpleNamespace(PDF="pdf")
    pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    pipeline_options.PdfPipelineOptions = lambda: types.SimpleNamespace(do_ocr=True)

    for name, mod in [
        ("docling", fake_package),
        ("docling.document_converter", fake_submodule),
        ("docling.datamodel", types.ModuleType("docling.datamodel")),
        ("docling.datamodel.base_models", base_models),
        ("docling.datamodel.pipeline_options", pipeline_options),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(config, "PARSER", "docling")
    yield FakeDoclingConverter
    pdf_text._reset_docling_converter()


class TestDoclingOcrSetting:
    def test_ocr_is_off_by_default(self, isolated_config, fake_docling, tmp_path):
        """Docling's own default is do_ocr=True. This corpus is
        born-digital papers with real text layers, and its OCR runs on
        the CPU -- measured at 2.33x the total parse time for output that
        was byte-identical on 6 of 7 sampled documents."""
        pdf_text.extract_text(str(tmp_path / "paper.pdf"), "smith_2024")
        assert fake_docling.pipeline_options().do_ocr is False

    def test_ocr_can_be_turned_back_on(self, isolated_config, fake_docling, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "PARSER_OCR", True)
        pdf_text.extract_text(str(tmp_path / "paper.pdf"), "smith_2024")
        assert fake_docling.pipeline_options().do_ocr is True


class TestDoclingConverterReuse:
    def test_converter_is_built_once_across_calls(self, isolated_config, fake_docling, tmp_path):
        """DocumentConverter.initialized_pipelines is an *instance*
        attribute, so a converter per PDF reloads every model per PDF --
        16.5s of cold start, measured, against a corpus of 501 files."""
        for i in range(3):
            pdf_text.extract_text(str(tmp_path / f"paper{i}.pdf"), f"key_{i}")
        assert fake_docling.build_count == 1

    def test_changing_the_ocr_setting_rebuilds_the_converter(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        """Caching on nothing but "was one built already" would silently
        serve an OCR-enabled converter after the setting was turned off."""
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert fake_docling.build_count == 1

        monkeypatch.setattr(config, "PARSER_OCR", True)
        pdf_text.extract_text(str(tmp_path / "b.pdf"), "b")
        assert fake_docling.build_count == 2
        assert fake_docling.pipeline_options().do_ocr is True

    def test_a_failed_convert_does_not_discard_the_converter(
        self, isolated_config, fake_docling, tmp_path
    ):
        """One unparseable PDF must not cost the next document a full
        model reload -- the failure is in the file, not the converter."""
        with pytest.raises(pdf_text.ExtractionError):
            pdf_text.extract_text(str(tmp_path / "explode.pdf"), "bad")
        pdf_text.extract_text(str(tmp_path / "fine.pdf"), "good")
        assert fake_docling.build_count == 1


class TestExtractTextDocling:
    def test_writes_markdown_output(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "paper.pdf"
        result = pdf_text.extract_text(str(pdf), "smith_2024")

        assert result == isolated_config.PARSED_DIR / "smith_2024.txt"
        assert "Parsed content" in result.read_text()
        assert FakeDoclingConverter.last_convert_path == str(pdf)

    def test_backend_exception_becomes_extraction_error(self, isolated_config, fake_docling, tmp_path):
        pdf = tmp_path / "explode.pdf"
        with pytest.raises(pdf_text.ExtractionError, match="simulated docling failure"):
            pdf_text.extract_text(str(pdf), "key")

    def test_broken_transitive_dependency_becomes_missing_dependency(
        self, isolated_config, monkeypatch, tmp_path
    ):
        """The package is findable (is_available()'s find_spec probe
        passes) but a broken transitive dependency makes the actual
        `from docling.document_converter import DocumentConverter` fail
        anyway (PR #11 review)."""
        monkeypatch.setattr(config, "PARSER", "docling")
        fake_package = types.ModuleType("docling")
        fake_package.__spec__ = importlib.machinery.ModuleSpec("docling", loader=None)
        broken_submodule = types.ModuleType("docling.document_converter")  # no DocumentConverter attribute
        monkeypatch.setitem(sys.modules, "docling", fake_package)
        monkeypatch.setitem(sys.modules, "docling.document_converter", broken_submodule)

        with pytest.raises(pdf_text.MissingDependency, match="docling"):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")


class TestUnknownParser:
    def test_is_available_raises_on_unknown_backend(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "ocrmypdf")
        with pytest.raises(ValueError, match="Unknown parser backend 'ocrmypdf'"):
            pdf_text.is_available()

    def test_extract_text_raises_on_unknown_backend(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "PARSER", "ocrmypdf")
        with pytest.raises(ValueError, match="Unknown parser backend"):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")

    def test_unavailable_reason_raises_on_unknown_backend(self, isolated_config, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "ocrmypdf")
        with pytest.raises(ValueError, match="Unknown parser backend"):
            pdf_text.unavailable_reason()


class TestIsAvailable:
    def test_true_when_pdftotext_on_path(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftotext")
        assert pdf_text.is_available() is True

    def test_false_when_pdftotext_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert pdf_text.is_available() is False

    def test_true_when_docling_importable(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text.importlib.util, "find_spec", lambda name: object())
        assert pdf_text.is_available() is True

    def test_false_when_docling_not_importable(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text.importlib.util, "find_spec", lambda name: None)
        assert pdf_text.is_available() is False


class TestUnavailableReason:
    def test_pdftotext_mentions_poppler(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        assert "poppler-utils" in pdf_text.unavailable_reason()

    def test_docling_mentions_heavy_group(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        assert "poetry install --with heavy" in pdf_text.unavailable_reason()


class TestExtractTextMissingBinary:
    """Regression coverage: a host without poppler-utils installed used to
    surface this as an uncaught FileNotFoundError traceback out of
    subprocess.run (src/sync.py caught only CalledProcessError) instead of
    a reported, honest result -- the same probe-and-report shape every
    src/heavy/* stage already follows (e.g. src/heavy/render_output.py's
    MissingBinary)."""

    def test_raises_missing_binary_instead_of_file_not_found(
        self, isolated_config, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(pdf_text.MissingBinary, match="pdftotext"):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")

    def test_does_not_invoke_subprocess_when_binary_missing(
        self, isolated_config, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        def fail_if_called(cmd, **kwargs):
            raise AssertionError("subprocess.run should not be called when pdftotext is missing")

        monkeypatch.setattr(subprocess, "run", fail_if_called)
        with pytest.raises(pdf_text.MissingBinary):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")


class TestExtractTextMissingDependency:
    def test_raises_missing_dependency_for_docling(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text.importlib.util, "find_spec", lambda name: None)
        with pytest.raises(pdf_text.MissingDependency, match="docling"):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")

    def test_missing_dependency_is_a_backend_unavailable(self, isolated_config, monkeypatch, tmp_path):
        """sync.py catches the BackendUnavailable base, not the specific
        subclass -- MissingBinary and MissingDependency must both be
        instances of it."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text.importlib.util, "find_spec", lambda name: None)
        with pytest.raises(pdf_text.BackendUnavailable):
            pdf_text.extract_text(str(tmp_path / "in.pdf"), "key")


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
class TestExtractTextReal:
    def test_real_pdftotext_on_a_real_pdf(self, isolated_config, tmp_path):
        pandoc = shutil.which("pandoc")
        pdflatex = shutil.which("pdflatex")
        if not pandoc or not pdflatex:
            pytest.skip("pandoc/pdflatex not installed -- can't generate a fixture PDF")

        md = tmp_path / "doc.md"
        md.write_text("# Hello\n\nThis is a real test PDF.\n")
        pdf = tmp_path / "doc.pdf"
        subprocess.run(
            ["pandoc", str(md), "-o", str(pdf), "--pdf-engine=pdflatex"],
            check=True, capture_output=True,
        )

        result = pdf_text.extract_text(str(pdf), "real_key")
        assert result.exists()
        assert "real test PDF" in result.read_text()




class TestParseQualityGuard:
    """The guard exists because a backend that fuses words together is
    invisible in a spot check but silently breaks retrieval: BM25
    tokenizes on whitespace, so a query term inside a fused run can
    never match. Ratios below come from this repo's own corpus."""

    HEALTHY = " ".join(["the quick brown fox jumps over a lazy dog"] * 40)

    def test_clean_text_produces_no_warning(self, isolated_config):
        assert pdf_text.quality_warning(self.HEALTHY) is None

    def test_fused_words_produce_a_warning(self, isolated_config):
        fused = self.HEALTHY + " " + " ".join(["isaninputtooranoutputfromafunction"] * 30)
        warning = pdf_text.quality_warning(fused)
        assert warning is not None
        assert "losing spaces" in warning

    def test_short_documents_are_not_judged(self, isolated_config):
        """Below min_tokens the ratio is noise -- a cover page or a scan
        that yielded almost nothing shouldn't be reported as broken."""
        assert pdf_text.quality_warning("averyverylongfusedtokenindeedyes short") is None

    def test_empty_text_is_not_a_crash(self, isolated_config):
        assert pdf_text.run_together_ratio("") == (0.0, 0)
        assert pdf_text.quality_warning("") is None

    def test_ratio_counts_only_alphabetic_runs(self, isolated_config):
        """DOIs, URLs and long digit strings are legitimately long and
        must not be mistaken for fused words."""
        digits = " ".join(["10.1000/abcd1234567890123456789"] * 60)
        ratio, total = pdf_text.run_together_ratio(digits)
        assert ratio == 0.0
        assert total > 0

    def test_counts_non_ascii_letters_as_letters(self, isolated_config):
        """This corpus is full of names like Schroder-with-an-umlaut and
        Greek in formulae. An ASCII-only pattern splits those into short
        pieces, which both hides real fusion and shrinks the token count
        toward min_tokens until the guard stops judging the document."""
        text = " ".join(["Schr\u00f6der", "W\u00fcllnerstra\u00dfe", "\u03b1\u03b2\u03b3\u03b4"] * 100)
        ratio, total = pdf_text.run_together_ratio(text)

        assert total == 300, "accented and Greek words must count as single tokens"
        assert ratio == 0.0

    def test_fusion_is_still_detected_in_non_ascii_text(self, isolated_config):
        fused = " ".join(["\u00fcbersetzungsfehlerbeispielwortkette"] * 250)
        assert pdf_text.quality_warning(fused) is not None

    def test_threshold_is_configurable(self, isolated_config, monkeypatch):
        fused = " ".join(["averylongfusedtokenhere"] * 5 + ["ok"] * 295)
        monkeypatch.setattr(isolated_config, "PARSE_LONG_WORD_RATIO", 0.5)
        assert pdf_text.quality_warning(fused) is None
        monkeypatch.setattr(isolated_config, "PARSE_LONG_WORD_RATIO", 0.001)
        assert pdf_text.quality_warning(fused) is not None

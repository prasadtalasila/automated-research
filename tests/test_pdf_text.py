"""src/pdf_text.py: dispatches PDF text extraction to whichever backend
config.PARSER names (pdftotext/docling).

docling is mocked via sys.modules (it is imported
lazily inside its _extract_* function, not at module top), matching
tests/test_heavy_docling_parse.py's pattern -- fast, deterministic, and
doesn't need the real package installed.
"""

import importlib.machinery
import importlib.util
import multiprocessing
import shutil
import signal
import subprocess
import sys
import types
from pathlib import Path

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
        options = getattr((format_options or {}).get("pdf"), "pipeline_options", None)
        accelerator = getattr(options, "accelerator_options", None)
        self.device = getattr(accelerator, "device", None)

    def convert(self, pdf_path):
        FakeDoclingConverter.last_convert_path = pdf_path
        if "explode" in str(pdf_path):
            raise RuntimeError("simulated docling failure")
        # A card with no memory left. Raised on a CUDA device -- and on
        # None, which is docling's own AUTO resolution, i.e. cuda:0 --
        # but not on the CPU, so a test can watch the fallback actually
        # produce a parse rather than just change a string.
        if "cudaoom" in str(pdf_path) and self.device != "cpu":
            raise RuntimeError("CUDA error: out of memory")
        # The other half of the pair: an allocation torch made itself,
        # which fails everywhere, so the CPU fallback runs out of road.
        if "alwaysoom" in str(pdf_path):
            raise RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB")
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
    pipeline_options.PdfPipelineOptions = lambda: types.SimpleNamespace(
        do_ocr=True, accelerator_options=None, document_timeout=None
    )
    accelerator = types.ModuleType("docling.datamodel.accelerator_options")
    accelerator.AcceleratorOptions = lambda num_threads=None, device=None: types.SimpleNamespace(
        num_threads=num_threads, device=device
    )

    for name, mod in [
        ("docling", fake_package),
        ("docling.document_converter", fake_submodule),
        ("docling.datamodel", types.ModuleType("docling.datamodel")),
        ("docling.datamodel.base_models", base_models),
        ("docling.datamodel.pipeline_options", pipeline_options),
        ("docling.datamodel.accelerator_options", accelerator),
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


class TestAllowedCpus:
    def test_uses_affinity_when_available(self, monkeypatch):
        """os.cpu_count() reports the machine's CPUs; sched_getaffinity
        reports the ones this process may actually run on. On a shared or
        containerised host those differ a lot -- 96 vs 48 on the machine
        this was developed on -- and sizing a pool off the larger number
        spawns workers that only descheduling each other."""
        monkeypatch.setattr(pdf_text.os, "cpu_count", lambda: 96)
        monkeypatch.setattr(pdf_text.os, "sched_getaffinity", lambda pid: set(range(48)),
                            raising=False)
        assert pdf_text.allowed_cpus() == 48

    def test_falls_back_to_cpu_count_without_affinity(self, monkeypatch):
        """sched_getaffinity is Linux-only -- it does not exist on Windows
        or macOS, and this project's CI has a windows-latest leg."""
        monkeypatch.delattr(pdf_text.os, "sched_getaffinity", raising=False)
        monkeypatch.setattr(pdf_text.os, "cpu_count", lambda: 8)
        assert pdf_text.allowed_cpus() == 8

    def test_falls_back_to_one_when_cpu_count_is_unknown(self, monkeypatch):
        monkeypatch.delattr(pdf_text.os, "sched_getaffinity", raising=False)
        monkeypatch.setattr(pdf_text.os, "cpu_count", lambda: None)
        assert pdf_text.allowed_cpus() == 1


class TestWorkerCeiling:
    """The one ceiling that doesn't depend on how many documents there
    are -- which is why it can be asked before the bibliography is read."""

    def test_docling_charges_four_cpus_per_worker(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        assert pdf_text.worker_ceiling() == 12

    def test_pdftotext_gets_one_per_cpu(self, monkeypatch):
        """A short single-threaded subprocess, so charging it a docling
        worker's 4 CPUs would under-use the machine."""
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        assert pdf_text.worker_ceiling() == 48

    @pytest.mark.parametrize("cpus,expected", [(1, 1), (4, 1), (8, 2), (16, 4), (48, 12)])
    def test_the_table_the_docs_promise(self, monkeypatch, cpus, expected):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: cpus)
        assert pdf_text.worker_ceiling() == expected

    def test_never_below_one(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 1)
        assert pdf_text.worker_ceiling() == 1


class TestResolveWorkers:
    @pytest.fixture(autouse=True)
    def _host(self, monkeypatch):
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(config, "PARSER", "docling")

    def test_default_of_one_stays_one(self, monkeypatch):
        """The default must reproduce the historical behaviour exactly --
        no pool, no subprocesses -- however many CPUs are lying around."""
        monkeypatch.setattr(config, "PARSER_WORKERS", 1)
        assert pdf_text.resolve_workers(500) == (1, None)

    def test_auto_divides_cpus_by_the_cost_of_a_docling_worker(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        assert pdf_text.resolve_workers(500) == (12, None)

    @pytest.mark.parametrize("cpus,expected", [(4, 1), (8, 2), (16, 4), (48, 12)])
    def test_auto_on_small_hosts(self, monkeypatch, cpus, expected):
        """A four-core/eight-thread desktop must not be handed 12 workers
        just because a 48-CPU host would be."""
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: cpus)
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        assert pdf_text.resolve_workers(500)[0] == expected

    def test_oversized_request_is_clamped_and_explained(self, monkeypatch):
        """Silently obeying thrashes the host; silently ignoring hides the
        clamp. Say it."""
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 8)
        monkeypatch.setattr(config, "PARSER_WORKERS", 15)
        workers, note = pdf_text.resolve_workers(500)
        assert workers == 2
        assert "15" in note and "2" in note and "8" in note

    def test_request_within_the_ceiling_is_not_explained(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        assert pdf_text.resolve_workers(500) == (4, None)

    def test_never_more_workers_than_documents(self, monkeypatch):
        """Standing up 12 docling workers to parse 3 documents costs 12
        model loads to save two documents' worth of work."""
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        assert pdf_text.resolve_workers(3)[0] == 3

    def test_no_documents_still_resolves_to_one(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        assert pdf_text.resolve_workers(0)[0] == 1

    def test_pdftotext_worker_is_not_charged_four_cpus(self, monkeypatch):
        """Each pdftotext is a short single-threaded subprocess, so the
        docling divisor would under-use the host by 4x here."""
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        assert pdf_text.resolve_workers(500)[0] == 48


class TestDoclingThreads:
    def test_one_worker_keeps_doclings_own_default(self, monkeypatch):
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        assert pdf_text.docling_threads(1) == 4

    def test_threads_divide_down_so_the_product_fits_the_host(self, monkeypatch):
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 8)
        assert pdf_text.docling_threads(4) == 2

    def test_never_below_one(self, monkeypatch):
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 4)
        assert pdf_text.docling_threads(12) == 1


class TestDoclingThreadBudget:
    def test_no_thread_budget_leaves_doclings_own_accelerator_settings(
        self, isolated_config, fake_docling, tmp_path
    ):
        """The single-worker default must reach Docling untouched, so a
        default run is exactly what Docling would have done alone."""
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert fake_docling.pipeline_options().accelerator_options is None

    def test_thread_budget_is_applied_to_the_pipeline(
        self, isolated_config, fake_docling, tmp_path
    ):
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a", threads=2)
        assert fake_docling.pipeline_options().accelerator_options.num_threads == 2

    def test_a_different_budget_rebuilds_the_converter(
        self, isolated_config, fake_docling, tmp_path
    ):
        """The thread count is baked into the converter, so it belongs in
        the cache key alongside the OCR setting."""
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a", threads=2)
        pdf_text.extract_text(str(tmp_path / "b.pdf"), "b", threads=4)
        assert fake_docling.build_count == 2


class TestExtractOne:
    """The pool's entry point. Returns its error instead of raising so
    that both the value and the exception survive pickling back to the
    parent -- and returns the exception *object*, since src/sync.py
    reports ExtractionError and BackendUnavailable differently."""

    def test_success_returns_the_output_path(self, isolated_config, fake_docling, tmp_path):
        citekey, out_path, exc = pdf_text.extract_one((str(tmp_path / "a.pdf"), "a", None))
        assert citekey == "a"
        assert exc is None
        assert Path(out_path).read_text().startswith("# Parsed content")

    def test_failure_returns_the_exception_with_its_type_intact(
        self, isolated_config, fake_docling, tmp_path
    ):
        citekey, out_path, exc = pdf_text.extract_one((str(tmp_path / "explode.pdf"), "bad", None))
        assert citekey == "bad"
        assert out_path is None
        assert isinstance(exc, pdf_text.ExtractionError)

    def test_backend_unavailable_keeps_its_own_type(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(pdf_text, "is_available", lambda: False)
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        _, _, exc = pdf_text.extract_one((str(tmp_path / "a.pdf"), "a", None))
        assert isinstance(exc, pdf_text.BackendUnavailable)

    def test_the_returned_exception_survives_pickling(self, isolated_config, fake_docling, tmp_path):
        """The whole reason for returning rather than raising: this triple
        has to cross a process boundary."""
        import pickle

        _, _, exc = pdf_text.extract_one((str(tmp_path / "explode.pdf"), "bad", None))
        assert isinstance(pickle.loads(pickle.dumps(exc)), pdf_text.ExtractionError)


def _fake_nvidia_smi(monkeypatch, n_gpus=None, returncode=0, raises=None, found=True):
    """Stand in for the real nvidia-smi, which this development host
    genuinely has -- without this every "no GPUs" case below would count
    the four A40s in the room and fail."""
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/nvidia-smi" if found else None)

    def fake_run(cmd, **kwargs):
        if raises is not None:
            raise raises
        lines = "".join(f"GPU {i}: NVIDIA A40 (UUID: GPU-{i})\n" for i in range(n_gpus or 0))
        return subprocess.CompletedProcess(cmd, returncode, stdout=lines, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


class TestGpuCount:
    def test_zero_when_backend_is_not_docling(self, monkeypatch):
        """pdftotext has no GPU path at all, so there is nothing to
        spread across devices."""
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        assert pdf_text.gpu_count() == 0

    def test_counts_the_gpus_nvidia_smi_lists(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        _fake_nvidia_smi(monkeypatch, n_gpus=4)
        assert pdf_text.gpu_count() == 4

    def test_counting_does_not_import_torch(self, monkeypatch):
        """The point of asking nvidia-smi: a parent that has imported
        torch pays 1.2s and ~200MB for a question it can answer without
        either, and a parent that has *initialised CUDA* cannot fork."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.delitem(sys.modules, "torch", raising=False)
        _fake_nvidia_smi(monkeypatch, n_gpus=2)

        pdf_text.gpu_count()

        assert "torch" not in sys.modules

    def test_falls_back_to_torch_when_nvidia_smi_is_absent(self, monkeypatch):
        """A slim container can pass /dev/nvidia* through without the
        driver's CLI tools. Returning 0 there would silently put every
        worker back on cuda:0."""
        monkeypatch.setattr(config, "PARSER", "docling")
        _fake_nvidia_smi(monkeypatch, found=False)
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda: 3)))
        assert pdf_text.gpu_count() == 3

    def test_a_failing_nvidia_smi_falls_back_too(self, monkeypatch):
        """Present but unhappy -- a driver/library mismatch makes it exit
        non-zero rather than print an empty list."""
        monkeypatch.setattr(config, "PARSER", "docling")
        _fake_nvidia_smi(monkeypatch, n_gpus=0, returncode=9)
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda: 1)))
        assert pdf_text.gpu_count() == 1

    def test_a_hanging_nvidia_smi_does_not_hang_the_sync(self, monkeypatch):
        """A wedged driver makes nvidia-smi block forever. That must cost
        _NVIDIA_SMI_TIMEOUT and a fallback, not the whole run."""
        monkeypatch.setattr(config, "PARSER", "docling")
        _fake_nvidia_smi(
            monkeypatch, raises=subprocess.TimeoutExpired(["nvidia-smi"], 10))
        monkeypatch.setitem(sys.modules, "torch", None)
        assert pdf_text.gpu_count() == 0

    def test_zero_when_neither_nvidia_smi_nor_torch_can_answer(self, monkeypatch):
        """The heavy group may be installed without a working torch, and
        a missing GPU is not an error -- it just means one device."""
        monkeypatch.setattr(config, "PARSER", "docling")
        _fake_nvidia_smi(monkeypatch, found=False)
        monkeypatch.setitem(sys.modules, "torch", None)
        assert pdf_text.gpu_count() == 0

    def test_a_broken_cuda_runtime_counts_as_no_gpus(self, monkeypatch):
        """torch imports fine but the driver is missing or mismatched --
        reported as CPU-only rather than taking down the whole sync."""
        def explode():
            raise RuntimeError("CUDA driver version is insufficient")

        monkeypatch.setattr(config, "PARSER", "docling")
        _fake_nvidia_smi(monkeypatch, found=False)
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=explode)),
        )
        assert pdf_text.gpu_count() == 0


class TestVisibleDevices:
    """nvidia-smi ignores CUDA_VISIBLE_DEVICES; every CUDA process obeys
    it. Counting without applying it would hand worker 3 a `cuda:3` that
    does not exist in its view -- and README documents that variable as
    the way to confine a run to one card."""

    def test_unset_means_every_device(self, monkeypatch):
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        assert pdf_text._visible_devices(4) == 4

    def test_a_single_device_narrows_the_count_to_one(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2")
        assert pdf_text._visible_devices(4) == 1

    def test_a_subset_is_counted_not_maxed(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,2")
        assert pdf_text._visible_devices(4) == 2

    def test_empty_means_no_devices_at_all(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        assert pdf_text._visible_devices(4) == 0

    def test_minus_one_means_no_devices(self, monkeypatch):
        """The conventional way to say "hide every GPU"."""
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
        assert pdf_text._visible_devices(4) == 0

    def test_enumeration_stops_at_the_first_invalid_entry(self, monkeypatch):
        """CUDA's own documented behaviour, and the reason this is a loop
        with a break rather than a length."""
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,nonsense,1")
        assert pdf_text._visible_devices(4) == 1

    def test_an_out_of_range_index_stops_enumeration(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,9")
        assert pdf_text._visible_devices(4) == 1

    def test_uuids_are_counted_as_devices(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-790508c0,GPU-d720f633")
        assert pdf_text._visible_devices(4) == 2

    def test_more_uuids_than_cards_is_clamped(self, monkeypatch):
        """A UUID can't be checked against anything, so the count has to
        be -- otherwise a worker gets handed a cuda:N with no N."""
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", ",".join(f"GPU-{i}" for i in range(6)))
        assert pdf_text._visible_devices(4) == 4

    def test_whitespace_around_entries_is_tolerated(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", " 0 , 1 ")
        assert pdf_text._visible_devices(4) == 2


def _fake_gpus(monkeypatch, free, listed=None, returncode=0, found=True, raises=None):
    """nvidia-smi answering both questions this module puts to it:
    `--list-gpus` for the count, `--query-gpu=index,memory.free` for how
    much room each card has.

    `free` maps *physical* device index to the free-memory field as a
    string, so a test can hand back "[N/A]" the way a card in a bad state
    really does. `listed` decouples the card count from that mapping,
    which is the only way to reach the case where nvidia-smi reports
    memory for fewer cards than it lists.
    """
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/nvidia-smi" if found else None)
    n_listed = len(free) if listed is None else listed

    def fake_run(cmd, **kwargs):
        if any("--query-gpu" in part for part in cmd):
            if raises is not None:
                raise raises
            body = "".join(f"{i}, {mib}\n" for i, mib in free.items())
            return subprocess.CompletedProcess(cmd, returncode, stdout=body, stderr="")
        body = "".join(
            f"GPU {i}: NVIDIA A40 (UUID: GPU-{i})\n" for i in range(n_listed))
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


_FULL = 46068     # an A40, in MiB -- the card this was measured on
_ROOMY = 45000    # comfortably over _GPU_MIN_FREE_MIB
_CRAMPED = 600    # what the 44.4 GiB-occupied GPU 0 actually had left


class TestUsableDevices:
    """A card another process has filled must not be handed to a worker.

    The run this comes from found GPU 0 holding 44.4 GiB of a previous
    run's orphaned workers. Four of 24 workers were assigned to it, could
    not load a model, and -- being ~19s per failure against minutes per
    success -- were fed 334 of the corpus's 456 documents by a pool that
    hands work to whoever is free first."""

    @pytest.fixture(autouse=True)
    def _docling_and_no_device_mask(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    def test_no_gpus_means_no_devices_and_nothing_to_say(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        assert pdf_text.usable_devices() == ([], None)

    def test_every_card_free_is_every_card_used(self, monkeypatch):
        _fake_gpus(monkeypatch, {i: _ROOMY for i in range(4)})
        assert pdf_text.usable_devices() == ([0, 1, 2, 3], None)

    def test_a_full_card_is_skipped_and_named(self, monkeypatch):
        _fake_gpus(monkeypatch, {0: _CRAMPED, 1: _ROOMY, 2: _ROOMY, 3: _ROOMY})
        devices, complaint = pdf_text.usable_devices()
        assert devices == [1, 2, 3]
        assert "cuda:0 (0.6 GiB free)" in complaint
        # The survivors are named too: "which card is it using?" is the
        # question a user asks next, and the answer is otherwise invisible.
        assert "cuda:1,2,3" in complaint

    def test_a_card_exactly_at_the_threshold_is_kept(self, monkeypatch):
        """The boundary is >=, so a card with precisely enough room is
        used rather than left idle."""
        _fake_gpus(monkeypatch, {0: pdf_text._GPU_MIN_FREE_MIB, 1: _ROOMY})
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_a_card_one_MiB_short_is_skipped(self, monkeypatch):
        _fake_gpus(monkeypatch, {0: pdf_text._GPU_MIN_FREE_MIB - 1, 1: _ROOMY})
        devices, complaint = pdf_text.usable_devices()
        assert devices == [1]
        assert "cuda:0" in complaint

    def test_every_card_full_falls_back_to_the_cpu(self, monkeypatch):
        """Slower -- measured 4.7x with OCR off, 1.8x with it on -- but
        a run that finishes, which beats 456 failures."""
        _fake_gpus(monkeypatch, {0: _CRAMPED, 1: _CRAMPED})
        devices, complaint = pdf_text.usable_devices()
        assert devices == []
        assert "every GPU is busy" in complaint
        assert "parsing on the CPU" in complaint

    def test_no_memory_reading_assumes_every_card_is_usable(self, monkeypatch):
        """Forgiving in the same way gpu_count is: refusing a GPU on the
        strength of a measurement we don't have is the worse mistake, and
        _demote_to_cpu recovers from the assignment if it was wrong."""
        _fake_gpus(monkeypatch, {i: _ROOMY for i in range(2)}, returncode=9)
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_an_unreadable_card_is_assumed_usable(self, monkeypatch):
        """A driver that can't report on one card prints "[N/A]" for it."""
        _fake_gpus(monkeypatch, {0: "[N/A]", 1: _ROOMY})
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_every_card_unreadable_is_no_reading_at_all(self, monkeypatch):
        _fake_gpus(monkeypatch, {0: "[N/A]", 1: "[N/A]"})
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_a_card_nvidia_smi_did_not_report_on_is_kept(self, monkeypatch):
        """nvidia-smi listed four cards but gave memory for three, so the
        physical mapping runs out before the device list does."""
        _fake_gpus(monkeypatch, {0: _ROOMY, 1: _ROOMY, 2: _ROOMY}, listed=4)
        assert pdf_text.usable_devices() == ([0, 1, 2, 3], None)

    def test_cuda_visible_devices_are_checked_by_physical_card(self, monkeypatch):
        """The trap this mapping exists for: with CUDA_VISIBLE_DEVICES=3,1
        the process's cuda:0 *is* physical card 3, so reading free memory
        at index 0 would check the wrong card and skip the wrong one."""
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3,1")
        _fake_gpus(
            monkeypatch,
            {0: _ROOMY, 1: _ROOMY, 2: _ROOMY, 3: _CRAMPED})
        devices, complaint = pdf_text.usable_devices()
        assert devices == [1]
        assert "cuda:0" in complaint

    def test_a_uuid_device_list_is_not_filtered(self, monkeypatch):
        """A UUID can't be resolved to nvidia-smi's index without torch,
        and guessing which card is which would skip an arbitrary one."""
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-abc,GPU-def")
        _fake_gpus(monkeypatch, {0: _CRAMPED, 1: _CRAMPED, 2: _ROOMY, 3: _ROOMY})
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_a_hanging_nvidia_smi_does_not_hang_the_sync(self, monkeypatch):
        """A wedged driver makes nvidia-smi block rather than answer.
        Costing the run its GPUs would be bad; costing it the whole sync
        would be worse, so this falls through to "assume usable"."""
        _fake_gpus(
            monkeypatch, {0: _ROOMY, 1: _ROOMY},
            raises=subprocess.TimeoutExpired(["nvidia-smi"], 10))
        assert pdf_text.usable_devices() == ([0, 1], None)

    def test_no_nvidia_smi_means_no_filtering(self, monkeypatch):
        """torch answered the count; nothing can answer the memory."""
        _fake_gpus(monkeypatch, {}, found=False)
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda: 2)))
        assert pdf_text.usable_devices() == ([0, 1], None)


class TestParseVisibleDevices:
    """_visible_devices' counting is covered above; this is the mapping
    back to physical cards that per-device memory readings need."""

    def test_unset_maps_each_device_to_itself(self, monkeypatch):
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        assert pdf_text._parse_visible_devices(3) == (3, [0, 1, 2])

    def test_a_reordered_subset_keeps_its_order(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3,1")
        assert pdf_text._parse_visible_devices(4) == (2, [3, 1])

    def test_a_uuid_makes_the_mapping_unknowable(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,GPU-abc")
        assert pdf_text._parse_visible_devices(4) == (2, None)

    def test_enumeration_still_stops_at_the_first_invalid_entry(self, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,nonsense,1")
        assert pdf_text._parse_visible_devices(4) == (1, [2])


class TestCudaOomRecovery:
    """A worker that can't get device memory fails a document in ~19s
    where a working one takes minutes, so a ProcessPoolExecutor -- which
    hands the next document to whoever is free first -- feeds the broken
    one preferentially. Four such workers out of 24 took 334 of 456
    documents down with them."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        pdf_text._reset_worker_device()
        yield
        pdf_text._reset_worker_device()

    def test_recognises_the_driver_level_message(self):
        """240 of the 334 failures in the run this comes from. A bare
        RuntimeError -- there is no dedicated type to catch."""
        assert pdf_text.is_cuda_oom(RuntimeError("CUDA error: out of memory")) is True

    def test_recognises_the_torch_allocator_message(self):
        """The other 94. torch.OutOfMemoryError, in the real thing."""
        assert pdf_text.is_cuda_oom(
            RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB")) is True

    def test_an_ordinary_failure_is_not_an_oom(self):
        assert pdf_text.is_cuda_oom(RuntimeError("simulated docling failure")) is False

    def test_an_oom_falls_back_to_the_cpu_and_the_document_parses(
        self, isolated_config, fake_docling, tmp_path, capsys
    ):
        """The whole point: the document that triggered the fallback is
        still parsed, not counted as a failure."""
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [0, 1])
        out = pdf_text.extract_text(str(tmp_path / "cudaoom.pdf"), "key")
        assert "Parsed content" in out.read_text()
        assert pdf_text.worker_device() == "cpu"
        assert "fallen back" in capsys.readouterr().err

    def test_the_fallback_sticks_for_the_rest_of_the_run(
        self, isolated_config, fake_docling, tmp_path
    ):
        """Demoting per document would put the worker back on the full
        card for the next one, which is the failure loop this replaces."""
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [0])
        pdf_text.extract_text(str(tmp_path / "cudaoom.pdf"), "one")
        pdf_text.extract_text(str(tmp_path / "b.pdf"), "two")
        assert fake_docling.pipeline_options().accelerator_options.device == "cpu"

    def test_a_serial_run_falls_back_too(
        self, isolated_config, fake_docling, tmp_path
    ):
        """No pool means no assigned device, which means docling's own
        AUTO -- and that resolves to cuda:0, the same card."""
        assert pdf_text.worker_device() is None
        out = pdf_text.extract_text(str(tmp_path / "cudaoom.pdf"), "key")
        assert "Parsed content" in out.read_text()
        assert pdf_text.worker_device() == "cpu"

    def test_an_oom_the_cpu_cannot_escape_is_reported_as_transient(
        self, isolated_config, fake_docling, tmp_path
    ):
        """Caused by the machine at this moment rather than by the PDF,
        so the ledger must retry it next run instead of writing it off."""
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [0])
        with pytest.raises(pdf_text.ExtractionError) as caught:
            pdf_text.extract_text(str(tmp_path / "alwaysoom.pdf"), "key")
        assert getattr(caught.value, "transient", False) is True

    def test_an_ordinary_backend_failure_stays_deterministic(
        self, isolated_config, fake_docling, tmp_path
    ):
        """A PDF docling genuinely cannot read is the same next run, and
        retrying it forever would be the bug this guards against."""
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [0])
        with pytest.raises(pdf_text.ExtractionError) as caught:
            pdf_text.extract_text(str(tmp_path / "explode.pdf"), "key")
        assert getattr(caught.value, "transient", False) is False


class TestCudaIsInitialised:
    def test_false_when_torch_was_never_imported(self, monkeypatch):
        """Asking the question must never be what makes the answer true,
        so this reads sys.modules rather than importing torch."""
        monkeypatch.delitem(sys.modules, "torch", raising=False)
        assert pdf_text.cuda_is_initialised() is False
        assert "torch" not in sys.modules

    def test_false_when_torch_is_imported_but_cuda_is_cold(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(is_initialized=lambda: False)))
        assert pdf_text.cuda_is_initialised() is False

    def test_true_once_something_has_used_a_gpu(self, monkeypatch):
        """src/heavy/embed_index runs sentence-transformers, and a
        library caller may have done anything before calling in."""
        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(is_initialized=lambda: True)))
        assert pdf_text.cuda_is_initialised() is True

    def test_an_unanswerable_torch_is_assumed_initialised(self, monkeypatch):
        """Guessing wrong towards "cold" hands every worker a broken CUDA
        context; guessing wrong towards "hot" costs ~1.5s of startup."""
        def explode():
            raise RuntimeError("no CUDA-capable device is detected")

        monkeypatch.setitem(
            sys.modules, "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(is_initialized=explode)))
        assert pdf_text.cuda_is_initialised() is True


class TestStartMethod:
    def test_auto_prefers_forkserver(self, monkeypatch):
        """The whole point: torch and docling are imported once in the
        forkserver process and inherited, rather than once per worker.
        Measured at four workers: 9.6s to first parse against spawn's
        11.3s."""
        monkeypatch.setattr(config, "PARSER_START_METHOD", "auto")
        monkeypatch.setattr(
            multiprocessing, "get_all_start_methods",
            lambda: ["fork", "spawn", "forkserver"])
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: False)
        assert pdf_text.start_method() == ("forkserver", None)

    def test_auto_falls_back_to_spawn_silently(self, monkeypatch):
        """Windows has spawn and nothing else, and this project's CI has
        a windows-latest leg. Picking what the platform has is what
        "auto" was asked to do, so there is nothing to report."""
        monkeypatch.setattr(config, "PARSER_START_METHOD", "auto")
        monkeypatch.setattr(multiprocessing, "get_all_start_methods", lambda: ["spawn"])
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: False)
        assert pdf_text.start_method() == ("spawn", None)

    def test_an_explicit_forkserver_that_cannot_be_honoured_says_so(self, monkeypatch):
        """Silence here would leave a config key that reads as honoured
        and isn't."""
        monkeypatch.setattr(config, "PARSER_START_METHOD", "forkserver")
        monkeypatch.setattr(multiprocessing, "get_all_start_methods", lambda: ["spawn"])
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: False)
        method, complaint = pdf_text.start_method()
        assert method == "spawn"
        assert "not available on this platform" in complaint

    def test_an_initialised_cuda_forces_spawn(self, monkeypatch):
        """A forkserver started from a process holding a CUDA context
        hands every worker a broken one."""
        monkeypatch.setattr(config, "PARSER_START_METHOD", "auto")
        monkeypatch.setattr(
            multiprocessing, "get_all_start_methods",
            lambda: ["fork", "spawn", "forkserver"])
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: True)
        method, complaint = pdf_text.start_method()
        assert method == "spawn"
        assert "CUDA is already initialised" in complaint

    def test_an_explicit_spawn_is_honoured_without_complaint(self, monkeypatch):
        monkeypatch.setattr(config, "PARSER_START_METHOD", "spawn")
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: False)
        assert pdf_text.start_method() == ("spawn", None)

    def test_spawn_does_not_need_the_cuda_check(self, monkeypatch):
        """Nothing is inherited under spawn, so a hot CUDA context in
        this process is simply not spawn's problem."""
        monkeypatch.setattr(config, "PARSER_START_METHOD", "spawn")
        monkeypatch.setattr(pdf_text, "cuda_is_initialised", lambda: True)
        assert pdf_text.start_method() == ("spawn", None)

    def test_fork_is_not_a_configurable_value(self):
        """Not an oversight: this process holds the run lock and the
        ledger open as live sqlite connections, and SQLite says not to
        carry an open connection across fork(). It also measured no
        faster than forkserver, so there is nothing being given up."""
        assert "fork" not in config.PARSER_START_METHODS


class TestPreloadModules:
    def test_lists_the_modules_a_worker_would_import(self, monkeypatch):
        monkeypatch.setattr(
            importlib.util, "find_spec",
            lambda name: importlib.machinery.ModuleSpec(name, None))
        assert pdf_text.preload_modules() == list(pdf_text._PRELOAD_MODULES)

    def test_drops_what_this_host_does_not_have(self, monkeypatch):
        """forkserver.main() swallows ImportError per module but nothing
        else -- a torch whose native library fails to load raises
        OSError, and that would take the forkserver down before a single
        worker existed."""
        monkeypatch.setattr(
            importlib.util, "find_spec",
            lambda name: None if name == "torch"
            else importlib.machinery.ModuleSpec(name, None))
        assert "torch" not in pdf_text.preload_modules()

    def test_an_unimportable_parent_package_is_skipped_not_raised(self, monkeypatch):
        def explode(name):
            raise ModuleNotFoundError(f"No module named {name!r}")

        monkeypatch.setattr(importlib.util, "find_spec", explode)
        assert pdf_text.preload_modules() == []


@pytest.mark.skipif(
    "forkserver" not in multiprocessing.get_all_start_methods(),
    reason="no forkserver to prestart on this platform (Windows has spawn only)",
)
class TestPrestartPool:
    """Starting the forkserver early is where the saving actually is:
    workers already import torch concurrently, so what forkserver removes
    from them it adds to pool construction -- unless that import is
    overlapped with the parent's own pre-pool work."""

    @pytest.fixture(autouse=True)
    def _a_machine_with_room(self, monkeypatch):
        """Pin the CPU count, because prestart_pool now asks
        worker_ceiling() and every test here depends on the answer.

        Without this these tests read the *developer's* core count and
        mean different things on different machines: at 48 cores the
        ceiling is 12 and a pool is coming, at 2 it is 1 and
        prestart_pool correctly declines. That is exactly how this class
        passed locally and failed CI -- on the change whose whole point
        was to make the pool decision machine-dependent.

        The small-machine tests below override it; monkeypatch is
        last-write-wins within a test.
        """
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)

    @pytest.fixture
    def started(self, monkeypatch):
        """Records whether the forkserver was asked to start."""
        from multiprocessing import forkserver

        calls = []
        monkeypatch.setattr(forkserver, "ensure_running", lambda: calls.append("started"))
        monkeypatch.setattr(
            multiprocessing, "get_context",
            lambda method: types.SimpleNamespace(set_forkserver_preload=lambda names: None))
        return calls

    def test_the_fixture_above_means_a_pool_really_is_coming(self):
        """Guards the guard: if this stops being >1, every "starts
        nothing" test below would pass for the wrong reason."""
        assert pdf_text.worker_ceiling() > 1

    def test_starts_the_forkserver_when_a_pool_is_coming(self, monkeypatch, started):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))

        pdf_text.prestart_pool()

        assert started == ["started"]

    def test_a_default_serial_run_starts_nothing(self, monkeypatch, started):
        """[parser].workers = 1 takes the serial path, which has no pool
        -- starting a torch-importing process for it would be pure cost."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 1)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))

        pdf_text.prestart_pool()

        assert started == []

    def test_auto_on_a_small_machine_starts_nothing(self, monkeypatch, started):
        """`workers = "auto"` is not the same as "a pool is coming".
        Four available CPUs put the docling ceiling at 1, so the run goes
        serial no matter how many documents there are -- and without this
        check every sync on a four-core laptop would launch a forkserver
        and import torch to then not use it."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 4)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))

        pdf_text.prestart_pool()

        assert pdf_text.worker_ceiling() == 1
        assert started == []

    def test_auto_on_a_large_machine_does_start(self, monkeypatch, started):
        """The other side of the same check -- 48 CPUs is a ceiling of 12,
        so a pool really is coming."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", "auto")
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 48)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))

        pdf_text.prestart_pool()

        assert started == ["started"]

    def test_an_explicit_count_above_a_ceiling_of_one_starts_nothing(
        self, monkeypatch, started
    ):
        """Asking for 8 on a four-core machine still resolves to 1 --
        resolve_workers clamps it -- so there is still no pool to warm."""
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 8)
        monkeypatch.setattr(pdf_text, "allowed_cpus", lambda: 4)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))

        pdf_text.prestart_pool()

        assert pdf_text.resolve_workers(100)[0] == 1
        assert started == []

    def test_the_pdftotext_backend_starts_nothing(self, monkeypatch, started):
        """It gets a thread pool, and has no use for torch at all.

        Note the machine here has plenty of room -- the autouse fixture
        pins 48 CPUs -- so this really is the backend check declining,
        not the ceiling check."""
        monkeypatch.setattr(config, "PARSER", "pdftotext")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)

        pdf_text.prestart_pool()

        assert started == []

    def test_nothing_is_started_when_spawn_was_chosen(self, monkeypatch, started):
        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("spawn", None))

        pdf_text.prestart_pool()

        assert started == []

    def test_a_failure_to_prestart_is_swallowed(self, monkeypatch):
        """An optimisation that could not be applied is not a problem to
        report -- the pool will start its own forkserver a moment later."""
        from multiprocessing import forkserver

        monkeypatch.setattr(config, "PARSER", "docling")
        monkeypatch.setattr(config, "PARSER_WORKERS", 4)
        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))
        monkeypatch.setattr(
            multiprocessing, "get_context",
            lambda method: types.SimpleNamespace(set_forkserver_preload=lambda names: None))

        def explode():
            raise OSError("fork: resource temporarily unavailable")

        monkeypatch.setattr(forkserver, "ensure_running", explode)

        pdf_text.prestart_pool()  # must not raise


class TestProcessPoolContext:
    def test_the_forkserver_is_told_what_to_preload(self, monkeypatch):
        """The preload list is the entire reason for preferring
        forkserver, and it has to be set before the first Process is
        created -- the server is started lazily by that call and imports
        its list exactly once."""
        recorded = []

        class FakeContext:
            def set_forkserver_preload(self, names):
                recorded.append(names)

        monkeypatch.setattr(pdf_text, "start_method", lambda: ("forkserver", None))
        monkeypatch.setattr(multiprocessing, "get_context", lambda method: FakeContext())
        monkeypatch.setattr(pdf_text, "preload_modules", lambda: ["torch"])

        ctx, complaint = pdf_text.process_pool_context()

        assert recorded == [["torch"]]
        assert complaint is None

    def test_spawn_gets_no_preload_list(self, monkeypatch):
        """It has nowhere to put one -- spawn's children import
        everything themselves."""
        class FakeContext:
            def set_forkserver_preload(self, names):  # pragma: no cover
                raise AssertionError("spawn has no forkserver to preload")

        monkeypatch.setattr(pdf_text, "start_method", lambda: ("spawn", "  NOTE why"))
        monkeypatch.setattr(multiprocessing, "get_context", lambda method: FakeContext())

        ctx, complaint = pdf_text.process_pool_context()

        assert complaint == "  NOTE why"


class TestWorkerDevice:
    """Docling's AcceleratorDevice.AUTO resolves to cuda:0 in *every*
    process, so without this every worker piles onto one card. Measured
    before this existed: at 12 workers GPU 0 ran pinned at 100% while
    GPUs 1-3 sat at 0%."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        pdf_text._reset_worker_device()
        yield
        pdf_text._reset_worker_device()

    def test_workers_are_assigned_round_robin(self):
        counter, lock = _FakeCounter(), _FakeLock()
        seen = []
        for _ in range(6):
            pdf_text.init_worker(counter, lock, [0, 1, 2, 3])
            seen.append(pdf_text._WORKER_DEVICE)
        assert seen == ["cuda:0", "cuda:1", "cuda:2", "cuda:3", "cuda:0", "cuda:1"]

    def test_the_round_robin_walks_only_the_usable_cards(self):
        """A device *list* rather than a count, so a card usable_devices
        skipped is never handed out -- not even to worker 0."""
        counter, lock = _FakeCounter(), _FakeLock()
        seen = []
        for _ in range(4):
            pdf_text.init_worker(counter, lock, [1, 2, 4])
            seen.append(pdf_text._WORKER_DEVICE)
        assert seen == ["cuda:1", "cuda:2", "cuda:4", "cuda:1"]

    def test_no_gpus_means_no_device_override(self):
        """Leave docling to its own AUTO resolution rather than forcing
        a device that doesn't exist."""
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [])
        assert pdf_text._WORKER_DEVICE is None

    def test_the_assigned_device_reaches_the_pipeline(
        self, isolated_config, fake_docling, tmp_path
    ):
        pdf_text.init_worker(_FakeCounter(), _FakeLock(), [0, 1, 2, 3])
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a", threads=2)
        opts = fake_docling.pipeline_options().accelerator_options
        assert opts.device == "cuda:0"
        assert opts.num_threads == 2

    def test_device_is_part_of_the_converter_cache_key(
        self, isolated_config, fake_docling, tmp_path
    ):
        """Two workers in one process (the thread-pool path, and tests)
        must not share a converter pinned to someone else's GPU."""
        counter, lock = _FakeCounter(), _FakeLock()
        pdf_text.init_worker(counter, lock, [0, 1, 2, 3])
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        pdf_text.init_worker(counter, lock, [0, 1, 2, 3])
        pdf_text.extract_text(str(tmp_path / "b.pdf"), "b")
        assert fake_docling.build_count == 2
        assert fake_docling.pipeline_options().accelerator_options.device == "cuda:1"


class _FakeCounter:
    """Stands in for a multiprocessing.Value."""

    def __init__(self):
        self.value = 0


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestDoclingPartialSuccess:
    """docling's convert(raises_on_error=True) raises only on FAILURE.
    PARTIAL_SUCCESS returns quietly with a document that stops early --
    a bad page, or document_timeout expiring. Writing that to
    content/parsed/<citekey>.txt and marking it parsed would hand the
    citation gate a source that silently ends at page k of n."""

    def test_partial_success_is_rejected(self, isolated_config, fake_docling, monkeypatch, tmp_path):
        monkeypatch.setattr(
            fake_docling, "convert",
            lambda self, p: _FakeResult("PARTIAL_SUCCESS", ["timeout after 10s"]),
            raising=False,
        )
        with pytest.raises(pdf_text.ExtractionError, match="PARTIAL_SUCCESS"):
            pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")

    def test_the_reason_docling_gave_is_carried_through(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            fake_docling, "convert",
            lambda self, p: _FakeResult("PARTIAL_SUCCESS", ["Document processing timeout"]),
            raising=False,
        )
        with pytest.raises(pdf_text.ExtractionError, match="timeout"):
            pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")

    def test_no_file_is_written_for_a_partial_parse(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            fake_docling, "convert",
            lambda self, p: _FakeResult("PARTIAL_SUCCESS", []), raising=False,
        )
        with pytest.raises(pdf_text.ExtractionError):
            pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert not (isolated_config.PARSED_DIR / "a.txt").exists()

    def test_success_passes_through(self, isolated_config, fake_docling, tmp_path):
        out = pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert out.read_text().startswith("# Parsed content")

    def test_a_backend_without_a_status_attribute_is_not_rejected(self):
        """Defensive: don't make the check itself a new failure mode if a
        docling version stops exposing status."""
        pdf_text.check_docling_status(types.SimpleNamespace())


class _FakeResult:
    def __init__(self, status_name, messages):
        self.status = types.SimpleNamespace(name=status_name)
        self.errors = [types.SimpleNamespace(error_message=m) for m in messages]
        self.document = FakeDoclingDocument("# partial")


class _FakeProcess:
    def __init__(self, alive_after_terminate=False, raises=None):
        self.terminated = False
        self.killed = False
        self.joined = None
        self._alive = alive_after_terminate
        self._raises = raises

    def terminate(self):
        if self._raises:
            raise self._raises
        self.terminated = True

    def join(self, timeout=None):
        self.joined = timeout

    def is_alive(self):
        return self._alive

    def kill(self):
        self.killed = True
        self._alive = False


class TestTerminateWorkers:
    """Ctrl+C has to leave nothing behind holding a GPU."""

    def test_workers_are_asked_to_stop(self):
        procs = {0: _FakeProcess(), 1: _FakeProcess()}
        pdf_text.terminate_workers(types.SimpleNamespace(_processes=procs))
        assert all(p.terminated for p in procs.values())
        assert not any(p.killed for p in procs.values())

    def test_a_worker_ignoring_sigterm_is_killed(self):
        """Measured for real: 21 processes survived terminate() alone,
        because onnxruntime/torch native code doesn't honour it promptly."""
        stubborn = _FakeProcess(alive_after_terminate=True)
        pdf_text.terminate_workers(types.SimpleNamespace(_processes={0: stubborn}))
        assert stubborn.terminated and stubborn.killed
        assert stubborn.joined == pdf_text._TERMINATE_GRACE_SECONDS

    def test_an_already_reaped_worker_is_not_an_error(self):
        gone = _FakeProcess(raises=ProcessLookupError("no such process"))
        pdf_text.terminate_workers(types.SimpleNamespace(_processes={0: gone}))

    def test_a_worker_that_dies_between_terminate_and_join_is_not_an_error(self):
        """The race this guards: the process exits on its own between the
        two loops, so join/kill find nothing. Ctrl+C must not turn into a
        traceback because a worker was helpful."""
        class VanishingProcess(_FakeProcess):
            def join(self, timeout=None):
                raise ProcessLookupError("reaped between terminate and join")

        vanishing = VanishingProcess()
        pdf_text.terminate_workers(types.SimpleNamespace(_processes={0: vanishing}))
        assert vanishing.terminated

    def test_a_thread_pool_has_nothing_to_terminate(self):
        """The pdftotext backend uses threads; there are no processes."""
        pdf_text.terminate_workers(types.SimpleNamespace())


class TestInterruptGuard:
    def test_it_installs_and_restores_the_handler(self):
        before = signal.getsignal(signal.SIGINT)
        with pdf_text.interrupt_guard(types.SimpleNamespace(), lambda: "0/0"):
            assert signal.getsignal(signal.SIGINT) is not before
        assert signal.getsignal(signal.SIGINT) is before

    def test_off_the_main_thread_it_degrades_instead_of_raising(self, monkeypatch):
        """signal.signal raises ValueError off the main thread. The pool
        still works there; it just can't catch Ctrl+C."""
        def refuse(*args):
            raise ValueError("signal only works in main thread")

        monkeypatch.setattr(pdf_text.signal, "signal", refuse)
        with pdf_text.interrupt_guard(types.SimpleNamespace(), lambda: "0/0") as guard:
            assert guard._previous is None

    def test_the_handler_reports_progress_terminates_and_exits(self, monkeypatch, capsys):
        procs = {0: _FakeProcess()}
        exits = []
        monkeypatch.setattr(pdf_text.os, "_exit", lambda code: exits.append(code))

        guard = pdf_text.interrupt_guard(
            types.SimpleNamespace(_processes=procs), lambda: "7/24 document(s) parsed"
        )
        guard._on_sigint(signal.SIGINT, None)

        err = capsys.readouterr().err
        assert "7/24" in err and "re-run to continue" in err
        assert procs[0].terminated
        # 130 = 128 + SIGINT, the conventional shell exit code.
        assert exits == [130]


class TestDocumentTimeout:
    """One setting, both backends, by whichever mechanism each has."""

    def test_pdftotext_gets_a_subprocess_timeout(self, isolated_config, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 30.0)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftotext")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            open(cmd[-1], "w").close()
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert captured["timeout"] == 30.0

    def test_pdftotext_without_a_timeout_waits_forever(
        self, isolated_config, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", None)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftotext")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            open(cmd[-1], "w").close()
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert captured["timeout"] is None

    def test_a_timed_out_pdftotext_is_an_extraction_error(
        self, isolated_config, monkeypatch, tmp_path
    ):
        """A hard kill, unlike docling's, which is cooperative -- so this
        is the one backend where a hang really can be stopped."""
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 5.0)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftotext")

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5.0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(pdf_text.ExtractionError, match="5.0s"):
            pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")

    def test_docling_gets_its_own_document_timeout(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 120.0)
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        assert fake_docling.pipeline_options().document_timeout == 120.0

    def test_docling_timeout_is_part_of_the_converter_cache_key(
        self, isolated_config, fake_docling, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 120.0)
        pdf_text.extract_text(str(tmp_path / "a.pdf"), "a")
        monkeypatch.setattr(config, "PARSER_DOCUMENT_TIMEOUT", 60.0)
        pdf_text.extract_text(str(tmp_path / "b.pdf"), "b")
        assert fake_docling.build_count == 2


class TestDoclingErrorMessage:
    def test_repeated_reasons_are_collapsed(self):
        """docling appends one error per failed page: a timeout on the
        675-page book in this corpus produced 675 identical copies in a
        single line, burying the summary that followed them."""
        result = _FakeResult("PARTIAL_SUCCESS", ["document timeout exceeded"] * 675)
        with pytest.raises(pdf_text.ExtractionError) as excinfo:
            pdf_text.check_docling_status(result)
        assert str(excinfo.value).count("document timeout exceeded") == 1

    def test_distinct_reasons_are_kept_and_the_rest_counted(self):
        result = _FakeResult("PARTIAL_SUCCESS", [f"reason {i}" for i in range(10)])
        with pytest.raises(pdf_text.ExtractionError) as excinfo:
            pdf_text.check_docling_status(result)
        message = str(excinfo.value)
        assert "reason 0" in message and "reason 2" in message
        assert "(+7 more)" in message

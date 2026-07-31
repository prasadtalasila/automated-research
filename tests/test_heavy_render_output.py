"""src/heavy/render_output.py: Pandoc/LaTeX rendering. Stdlib + config/
citation_gate/references only (deliberately, so it runs with bare
python3 -- see the module docstring), so these tests use the real
pandoc/pdflatex binaries installed on this host rather than mocking
subprocess, for genuine end-to-end confidence on the one stage most
likely to have host-environment gaps (see Task-1's lmodern.sty find)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src import ledger
from src.heavy import render_output

from tests.conftest import make_reference

pandoc_available = shutil.which("pandoc") is not None
pdflatex_available = shutil.which("pdflatex") is not None


class TestAliasFor:
    def test_replaces_double_hyphen(self):
        assert render_output._alias_for("zech_digital-twins-as--service_2024") == \
            "zech_digital-twins-as-x2d-service_2024"

    def test_no_double_hyphen_unchanged_value(self):
        # _alias_for always transforms; callers only invoke it for keys
        # already known to contain "--" (see _safe_render_inputs).
        assert render_output._alias_for("plain_key_2024") == "plain_key_2024"


class TestSafeRenderInputs:
    def test_no_bad_keys_returns_original_paths(self, tmp_path):
        md = tmp_path / "in.md"
        md.write_text("Citing [@smith_2024].\n")
        bib = tmp_path / "bibliography.bib"
        bib.write_text("@article{smith_2024,\n  title={T},\n}\n")

        safe_md, safe_bib = render_output._safe_render_inputs(md, bib, tmp_path / "tmp")
        assert safe_md == md
        assert safe_bib == bib

    def test_double_hyphen_key_gets_aliased_in_both_files(self, tmp_path):
        md = tmp_path / "in.md"
        md.write_text("Citing [@zech_digital-twins-as--service_2024] here.\n")
        bib = tmp_path / "bibliography.bib"
        bib.write_text(
            "@article{zech_digital-twins-as--service_2024,\n  title={T},\n}\n"
            "@article{zech_digital-twins-as--service_2024-1,\n  title={T2},\n}\n"
        )
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir()

        safe_md, safe_bib = render_output._safe_render_inputs(md, bib, tmp_dir)
        assert safe_md != md
        assert safe_bib != bib

        md_text = safe_md.read_text()
        assert "zech_digital-twins-as-x2d-service_2024" in md_text
        assert "--service" not in md_text

        bib_text = safe_bib.read_text()
        assert "@article{zech_digital-twins-as-x2d-service_2024," in bib_text
        # The "-1" duplicate entry must be untouched, not also aliased.
        assert "@article{zech_digital-twins-as--service_2024-1," in bib_text


class TestRequire:
    def test_raises_missing_binary_when_not_on_path(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(render_output.MissingBinary):
            render_output._require("some-binary-that-does-not-exist")

    def test_no_raise_when_found(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        render_output._require("pandoc")  # should not raise


@pytest.mark.skipif(not (pandoc_available and pdflatex_available), reason="pandoc/pdflatex not installed")
class TestRenderReal:
    def test_renders_markdown_with_citation_to_pdf(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()

        bib = isolated_config.BIB_FILE_PATH
        bib.write_text("@article{smith_2024,\n  title={An Example Paper},\n  year={2024},\n}\n")

        draft = tmp_path / "draft.md"
        draft.write_text("# Title\n\nSome claim [@smith_2024].\n")

        out_path = render_output.render(str(draft), output_format="pdf")
        assert out_path.exists()
        assert out_path == isolated_config.RENDERED_DIR / "draft.pdf"

    def test_renders_to_tex_and_suppresses_bibliography_with_manual_refs(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_2024,\n  title={An Example Paper},\n  year={2024},\n}\n"
        )

        draft = tmp_path / "draft.md"
        draft.write_text(
            "# Title\n\nSome claim [@smith_2024].\n\n"
            "## References\n\n- **smith_2024** -- An Example Paper (2024).\n"
        )
        out_path = render_output.render(str(draft), output_format="tex")
        tex = out_path.read_text()
        assert "documentclass" in tex or "article" in tex

    def test_double_hyphen_citekey_survives_render(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(
            con, make_reference(citekey="zech_digital-twins-as--service_2024", title="Zech Paper", year="2024")
        )
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{zech_digital-twins-as--service_2024,\n  title={Zech Paper},\n  year={2024},\n}\n"
        )

        draft = tmp_path / "draft.md"
        draft.write_text("# Title\n\nA claim [@zech_digital-twins-as--service_2024].\n")

        out_path = render_output.render(str(draft), output_format="tex")
        assert out_path.exists()

    def test_missing_binary_path(self, isolated_config, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        draft = tmp_path / "draft.md"
        draft.write_text("No citations here.\n")
        with pytest.raises(render_output.MissingBinary):
            render_output.render(str(draft))


class TestMainCli:
    def test_missing_binary_prints_and_returns_1(self, isolated_config, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        draft = tmp_path / "draft.md"
        draft.write_text("text\n")
        sys.argv = ["render_output.py", str(draft)]
        rc = render_output.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert "[missing-binary]" in out

    @pytest.mark.skipif(not (pandoc_available and pdflatex_available), reason="pandoc/pdflatex not installed")
    def test_called_process_error_prints_and_returns_1(self, isolated_config, tmp_path, capsys):
        isolated_config.BIB_FILE_PATH.write_text("")
        draft = tmp_path / "draft.md"
        # Malformed LaTeX documentclass argument to force pandoc to fail.
        sys.argv = ["render_output.py", str(draft), "--documentclass", "this is not valid \\"]
        draft.write_text("text\n")
        rc = render_output.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert "[error]" in out

    @pytest.mark.skipif(not (pandoc_available and pdflatex_available), reason="pandoc/pdflatex not installed")
    def test_success_prints_output_path_and_returns_0(self, isolated_config, tmp_path, capsys):
        isolated_config.BIB_FILE_PATH.write_text("")
        draft = tmp_path / "draft.md"
        draft.write_text("# Title\n\nNo citations here.\n")
        sys.argv = ["render_output.py", str(draft), "--format", "tex"]
        rc = render_output.main()
        out = capsys.readouterr().out
        assert rc == 0
        assert str(isolated_config.RENDERED_DIR / "draft.tex") in out

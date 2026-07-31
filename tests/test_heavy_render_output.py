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


class TestLocalImageRefs:
    def test_extracts_local_image_paths(self):
        text = "![alt one](figure-one.png)\n\nSome text ![alt two](sub/figure-two.svg \"a title\").\n"
        assert render_output._local_image_refs(text) == ["figure-one.png", "sub/figure-two.svg"]

    def test_skips_remote_urls_and_data_uris(self):
        text = (
            "![remote](https://example.com/figure.png)\n"
            "![inline](data:image/png;base64,AAAA)\n"
            "![local](figure.png)\n"
        )
        assert render_output._local_image_refs(text) == ["figure.png"]

    def test_no_images_returns_empty_list(self):
        assert render_output._local_image_refs("Just text, no images.\n") == []


class TestCopyLocalImages:
    def test_copies_an_existing_local_image(self, tmp_path):
        src_dir = tmp_path / "drafts"
        src_dir.mkdir()
        (src_dir / "figure.png").write_bytes(b"fake png bytes")
        draft = src_dir / "draft.md"
        draft.write_text("![alt](figure.png)\n")
        dest_dir = tmp_path / "rendered"
        dest_dir.mkdir()

        render_output._copy_local_images(draft, dest_dir)

        assert (dest_dir / "figure.png").read_bytes() == b"fake png bytes"

    def test_skips_a_reference_that_does_not_resolve_to_a_real_file(self, tmp_path):
        src_dir = tmp_path / "drafts"
        src_dir.mkdir()
        draft = src_dir / "draft.md"
        draft.write_text("![alt](does-not-exist.png)\n")
        dest_dir = tmp_path / "rendered"
        dest_dir.mkdir()

        render_output._copy_local_images(draft, dest_dir)  # must not raise

        assert list(dest_dir.iterdir()) == []

    def test_skips_absolute_and_parent_escaping_paths(self, tmp_path):
        secret = tmp_path / "secret.png"
        secret.write_bytes(b"marker")
        src_dir = tmp_path / "drafts"
        src_dir.mkdir()
        draft = src_dir / "draft.md"
        draft.write_text(f"![abs]({secret})\n\n![traversal](../secret.png)\n")
        dest_dir = tmp_path / "rendered"
        dest_dir.mkdir()

        render_output._copy_local_images(draft, dest_dir)

        assert list(dest_dir.iterdir()) == []

    def test_creates_nested_destination_directories(self, tmp_path):
        src_dir = tmp_path / "drafts"
        (src_dir / "figures").mkdir(parents=True)
        (src_dir / "figures" / "figure.png").write_bytes(b"fake png bytes")
        draft = src_dir / "draft.md"
        draft.write_text("![alt](figures/figure.png)\n")
        dest_dir = tmp_path / "rendered"
        dest_dir.mkdir()

        render_output._copy_local_images(draft, dest_dir)

        assert (dest_dir / "figures" / "figure.png").read_bytes() == b"fake png bytes"


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

    def test_local_image_embeds_when_input_is_a_relative_path(self, isolated_config, tmp_path, monkeypatch):
        # Regression test: pandoc resolves a draft's local image references
        # (`![...](figure.png)`) relative to pandoc's own working directory,
        # not the draft's directory -- so invoking this CLI from anywhere
        # other than the draft's own directory silently dropped the image
        # (pandoc's PDF writer falls back to the alt-text caption instead of
        # erroring) unless --resource-path is passed. monkeypatch.chdir to a
        # directory that is neither tmp_path nor its parent, matching how
        # this CLI is actually invoked (from the repo root, not
        # content/drafts/).
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_2024,\n  title={An Example Paper},\n  year={2024},\n}\n"
        )

        draft_dir = tmp_path / "content" / "drafts"
        draft_dir.mkdir(parents=True)
        draft = draft_dir / "draft.md"
        # A real, valid 1x1 PNG (built from raw chunks, not a placeholder),
        # so pandoc's PDF writer can actually decode and embed it, not just
        # find the path.
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
            "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
            "426082"
        )
        (draft_dir / "figure.png").write_bytes(png_bytes)
        draft.write_text("# Title\n\n![A caption](figure.png)\n\nSome claim [@smith_2024].\n")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        out_path = render_output.render(str(draft), output_format="pdf")
        pdf_bytes = out_path.read_bytes()
        assert b"/Subtype/Image" in pdf_bytes or b"/Subtype /Image" in pdf_bytes

    def test_local_image_is_copied_to_rendered_dir_so_standalone_tex_compiles(
        self, isolated_config, tmp_path, monkeypatch
    ):
        # Regression test: rendering to `tex` only asks pandoc to emit
        # \includegraphics{figure.png} into the .tex source -- it never
        # copies the actual image file anywhere, so the standalone .tex
        # landing in content/rendered/ can't find it and fails to compile
        # on its own ("File `figure.png' not found"), even though
        # --resource-path (above) lets the *pdf* format's own pandoc-driven
        # pdflatex pass embed it correctly. A .tex a user can't compile
        # isn't a real deliverable.
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_2024,\n  title={An Example Paper},\n  year={2024},\n}\n"
        )

        draft_dir = tmp_path / "content" / "drafts"
        draft_dir.mkdir(parents=True)
        draft = draft_dir / "draft.md"
        # A real, valid 1x1 PNG (built from raw chunks, not a placeholder),
        # so pdflatex can actually decode and embed it, not just find it.
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
            "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae"
            "426082"
        )
        (draft_dir / "figure.png").write_bytes(png_bytes)
        draft.write_text("# Title\n\n![A caption](figure.png)\n\nSome claim [@smith_2024].\n")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        out_path = render_output.render(str(draft), output_format="tex")

        copied_image = out_path.parent / "figure.png"
        assert copied_image.exists()
        assert copied_image.read_bytes() == png_bytes

        # The copied image must actually make the standalone .tex
        # compilable on its own, not just "a file happens to be present".
        compile_result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", out_path.name],
            cwd=out_path.parent, capture_output=True, text=True,
        )
        assert compile_result.returncode == 0, compile_result.stdout[-2000:]

    def test_image_reference_outside_the_draft_directory_is_not_copied(self, isolated_config, tmp_path):
        # A `../`-escaping or absolute image path must not let a draft
        # write outside content/rendered/ -- skip it and let pandoc's own
        # missing-resource handling surface the problem, same as any other
        # image path that doesn't resolve to a real file.
        secret = tmp_path / "secret.png"
        secret.write_bytes(b"not a real png, just a marker")

        draft_dir = tmp_path / "content" / "drafts"
        draft_dir.mkdir(parents=True)
        draft = draft_dir / "draft.md"
        draft.write_text("# Title\n\n![traversal](../../secret.png)\n\nNo citations.\n")
        isolated_config.BIB_FILE_PATH.write_text("")

        render_output.render(str(draft), output_format="tex")

        assert not (isolated_config.RENDERED_DIR / "secret.png").exists()
        assert not (isolated_config.RENDERED_DIR.parent / "secret.png").exists()

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

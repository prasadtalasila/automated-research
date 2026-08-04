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


class TestResolveCsl:
    def test_absolute_path_is_used_as_given(self, tmp_path):
        style = tmp_path / "house.csl"
        style.write_text("<style/>")
        assert render_output._resolve_csl(str(style)) == style

    def test_relative_path_resolves_against_the_working_directory(self, tmp_path, monkeypatch):
        style = tmp_path / "house.csl"
        style.write_text("<style/>")
        monkeypatch.chdir(tmp_path)
        assert render_output._resolve_csl("house.csl").resolve() == style.resolve()

    def test_repo_relative_path_works_from_another_directory(self, tmp_path, monkeypatch):
        # config.toml's `[render] csl` is documented repo-root-relative and
        # --help prints the default in that form, so the same string has to
        # work when the command is run from outside the repo.
        monkeypatch.chdir(tmp_path)
        resolved = render_output._resolve_csl("assets/csl/ieee.csl")
        assert resolved.is_file()
        assert resolved == render_output.config.REPO_ROOT / "assets" / "csl" / "ieee.csl"

    def test_the_working_directory_wins_when_both_exist(self, tmp_path, monkeypatch):
        # A local file the user actually typed a path to is never shadowed
        # by a same-named one in the repo.
        local = tmp_path / "assets" / "csl"
        local.mkdir(parents=True)
        (local / "ieee.csl").write_text("<style>local</style>")
        monkeypatch.chdir(tmp_path)
        assert render_output._resolve_csl("assets/csl/ieee.csl").read_text() == "<style>local</style>"

    def test_unresolvable_path_is_returned_as_typed(self, tmp_path, monkeypatch):
        # So the error message names what the user wrote, not a repo-root
        # path they never mentioned.
        monkeypatch.chdir(tmp_path)
        assert render_output._resolve_csl("nope.csl") == Path("nope.csl")


class TestCollapsedCsl:
    def test_adds_the_collapse_attribute_to_a_temp_copy(self, tmp_path):
        csl = tmp_path / "style.csl"
        csl.write_text('<style>\n  <citation>\n    <layout/>\n  </citation>\n</style>\n')
        out_dir = tmp_path / "render-tmp"
        out_dir.mkdir()
        out = render_output._collapsed_csl(csl, out_dir)

        assert out != csl, "must not edit the vendored style in place"
        assert '<citation collapse="citation-number">' in out.read_text()
        assert 'collapse=' not in csl.read_text(), "original left untouched"

    def test_keeps_existing_attributes_on_the_citation_tag(self, tmp_path):
        csl = tmp_path / "style.csl"
        csl.write_text('<style><citation et-al-min="3"><layout/></citation></style>')
        text = render_output._collapsed_csl(csl, tmp_path).read_text()
        assert 'collapse="citation-number"' in text and 'et-al-min="3"' in text

    def test_a_style_that_already_collapses_is_returned_unchanged(self, tmp_path):
        csl = tmp_path / "style.csl"
        csl.write_text('<style><citation collapse="year"><layout/></citation></style>')
        # Overriding the style author's own choice would silently change
        # how someone's style renders.
        assert render_output._collapsed_csl(csl, tmp_path) == csl

    def test_a_style_with_no_citation_element_is_returned_unchanged(self, tmp_path):
        csl = tmp_path / "style.csl"
        csl.write_text("<style><bibliography><layout/></bibliography></style>")
        assert render_output._collapsed_csl(csl, tmp_path) == csl


class TestVendoredIeeeStyle:
    def test_is_present_and_is_the_configured_default(self):
        from src import config

        style = Path(__file__).resolve().parent.parent / "assets" / "csl" / "ieee.csl"
        assert style.is_file()
        assert config.CSL_STYLE_PATH.name == "ieee.csl"

    def test_is_unmodified_upstream(self):
        # assets/csl/README.md promises this file is byte-identical to the
        # CSL project's own ieee.csl, so it can be re-fetched and diffed.
        # The one deviation this project needs lives in _collapsed_csl, so
        # an edit here would mean that promise had quietly been broken.
        style = Path(__file__).resolve().parent.parent / "assets" / "csl" / "ieee.csl"
        assert 'collapse=' not in style.read_text()


class TestSwapManualRefsForCiteproc:
    def test_keeps_the_heading_and_swaps_the_entries_for_the_anchor(self):
        text = "# Title\n\nA claim [@k].\n\n## References\n\n[1] A Paper, 2024. `k`\n"
        assert render_output._swap_manual_refs_for_citeproc(text) == (
            "# Title\n\nA claim [@k].\n\n## References\n\n::: {#refs}\n:::\n"
        )

    def test_preserves_a_draft_s_own_numbered_heading(self):
        # textbook-chapter-writer passes --heading "6. References" to match
        # its other headings; citeproc emits no heading of its own, so
        # dropping this one left the rendered bibliography untitled.
        text = "A claim [@k].\n\n## 6. References\n\n[1] A Paper, 2024. `k`\n"
        assert "## 6. References" in render_output._swap_manual_refs_for_citeproc(text)

    def test_preserves_the_heading_level(self):
        text = "A claim [@k].\n\n#### References\n\n[1] A Paper, 2024. `k`\n"
        assert "#### References" in render_output._swap_manual_refs_for_citeproc(text)

    def test_handles_a_heading_on_the_final_line_without_a_newline(self):
        text = "A claim [@k].\n\n## References"
        out = render_output._swap_manual_refs_for_citeproc(text)
        assert out.endswith("## References\n\n::: {#refs}\n:::\n")

    def test_leaves_a_draft_without_one_alone(self):
        text = "# Title\n\nA claim [@k].\n"
        assert render_output._swap_manual_refs_for_citeproc(text) == text

    def test_leaves_a_latex_fragment_alone(self):
        # thesis-chapter-writer's .tex fragment has no Markdown heading and
        # defers to the user's own thesis-wide bibliography.
        text = "A claim \\citep{k}.\n\n\\section{References}\n"
        assert render_output._swap_manual_refs_for_citeproc(text) == text


class TestAliasFor:
    def test_replaces_double_hyphen(self):
        assert render_output._alias_for("zech_digital-twins-as--service_2024") == \
            "zech_digital-twins-as-x2d-service_2024"

    @pytest.mark.parametrize("citekey", [
        "zech_digital-twins-as--service_2024",
        # This project's own corpus has a 3-hyphen key. A single
        # replace("--", "-x2d-") leaves "state-x2d--art" -- still
        # truncating, so the citation resolves to nothing and the source
        # silently disappears from the rendered bibliography.
        "tygesen_state---art_2019",
        "a----b",
    ])
    def test_alias_never_leaves_a_double_hyphen_behind(self, citekey):
        assert "--" not in render_output._alias_for(citekey)

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


class TestRenderMarkdown:
    """`--format md` on a Markdown draft, which is a citation-numbering
    job rather than a format conversion and so never calls pandoc."""

    def test_produces_plain_numbered_markdown(self, isolated_config, tmp_path, monkeypatch):
        con = ledger.connect()
        for key, title in [("b_2024", "B Paper"), ("a_2023", "A Paper")]:
            ledger.upsert_reference(con, make_reference(
                citekey=key, title=title, year="2024",
                fields={"author": "Doe, Jane", "journal": "J. Things"}))
        con.close()

        draft = tmp_path / "draft.md"
        draft.write_text("# T\n\nOne [@b_2024]. Two [@a_2023].\n")

        # Deliberately hidden: this path must not need pandoc, so a host
        # without it still gets a readable numbered draft.
        monkeypatch.setattr(render_output.shutil, "which", lambda _: None)
        out_path = render_output.render(str(draft), output_format="md")
        text = out_path.read_text()

        assert out_path == isolated_config.RENDERED_DIR / "draft.md"
        assert "One [1]. Two [2]." in text
        # None of pandoc's Markdown-writer artifacts.
        assert "\\[" not in text, "brackets must not be escaped"
        assert ":::" not in text, "no fenced-div wrappers"
        assert "csl-" not in text, "no citeproc span classes"
        assert "[@" not in text, "no citekeys left inline"

    def test_leaves_the_draft_untouched(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="a_2024", title="A Paper", year="2024"))
        con.close()

        draft = tmp_path / "draft.md"
        original = "Body [@a_2024].\n"
        draft.write_text(original)
        render_output.render(str(draft), output_format="md")

        # The draft is the gated source; only content/rendered/ is derived.
        assert draft.read_text() == original

    @pytest.mark.skipif(not pandoc_available, reason="pandoc not installed")
    def test_a_latex_input_still_goes_through_pandoc(self, isolated_config, tmp_path):
        # thesis-chapter-writer renders its .tex fragment to .md as a
        # preview: that is a real conversion, and the fragment carries
        # \citep{...} rather than [@key] and no reference list of its own.
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="a_2024", title="A Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{a_2024,\n  author={Doe, Jane},\n  title={A Paper},\n  year={2024},\n}\n"
        )

        fragment = tmp_path / "chapter.tex"
        fragment.write_text("\\section{Intro}\nA claim \\citep{a_2024}.\n")
        out_path = render_output.render(str(fragment), output_format="md")

        assert out_path == isolated_config.RENDERED_DIR / "chapter.md"
        assert "Intro" in out_path.read_text()


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

    def test_renders_to_tex_replacing_a_manual_refs_section_with_citeprocs(self, isolated_config, tmp_path):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_2024,\n  author={Smith, Jane},\n  title={An Example Paper},\n"
            "  journal={J. Examples},\n  year={2024},\n}\n"
        )

        draft = tmp_path / "draft.md"
        draft.write_text(
            "# Title\n\nSome claim [@smith_2024].\n\n"
            "## References\n\n[1] J. Smith, \"An Example Paper,\" *J. Examples*, 2024. `smith_2024`\n"
        )
        out_path = render_output.render(str(draft), output_format="tex")
        tex = out_path.read_text()

        assert "documentclass" in tex or "article" in tex
        # The draft's own section is stripped and citeproc's numbered one
        # takes its place: exactly one bibliography, with the author and
        # journal in it, and no citekey label reaching the reader (the
        # draft's `smith_2024` code span would come through as \texttt).
        # Case-insensitively: IEEE style sentence-cases titles, so
        # citeproc's own entry reads "An example paper".
        assert tex.lower().count("an example paper") == 1, "exactly one bibliography, not two"
        assert "J. Smith" in " ".join(tex.split())
        assert "J. Examples" in " ".join(tex.split())
        assert "\\texttt" not in tex
        # The draft's own heading survives, and citeproc's bibliography
        # lands under it rather than at the end of an untitled document.
        assert "References" in tex
        assert tex.index("References") < tex.index("CSLReferences}{")

    def test_the_drafts_own_reference_heading_titles_the_rendered_bibliography(
        self, isolated_config, tmp_path
    ):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith_2024", title="An Example Paper", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_2024,\n  author={Smith, Jane},\n  title={An Example Paper},\n"
            "  journal={J. Examples},\n  year={2024},\n}\n"
        )

        draft = tmp_path / "draft.md"
        draft.write_text(
            "# Title\n\nSome claim [@smith_2024].\n\n"
            "## 6. References\n\n[1] J. Smith, \"An Example Paper,\" 2024. `smith_2024`\n"
        )
        out_path = render_output.render(str(draft), output_format="html")
        text = " ".join(out_path.read_text().split())

        # The numbered heading a genre skill chose, not a generic one
        # pandoc invented, and the entries sit under it. Anchored on the
        # entry's own div id rather than "csl-entry", which also appears
        # in the standalone template's stylesheet up in <head>.
        assert "6. References" in text
        assert text.index("6. References") < text.index('id="ref-smith_2024"')

    def test_ieee_numbering_and_collapsed_runs_reach_the_output(self, isolated_config, tmp_path):
        con = ledger.connect()
        keys = [f"k{i}_2024" for i in range(1, 6)]
        for key in keys:
            ledger.upsert_reference(con, make_reference(citekey=key, title=f"Paper {key}", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text("\n".join(
            f"@article{{{key},\n  author={{Doe, Jane}},\n  title={{Paper {key}}},\n"
            f"  journal={{J. Examples}},\n  year={{2024}},\n}}" for key in keys
        ))

        draft = tmp_path / "draft.md"
        draft.write_text(
            "# Title\n\nOne [@k1_2024]. Another [@k2_2024].\n\n"
            "A run of four [@k2_2024; @k3_2024; @k4_2024; @k5_2024].\n"
        )
        # html rather than tex/pdf so the assertions can read the markers
        # as a reader sees them, without LaTeX's {[}1{]} escaping in the way.
        out_path = render_output.render(str(draft), output_format="html")
        # Pandoc hard-wraps its output, so a name can arrive split across
        # two lines ("J.\nDoe") -- collapse whitespace before matching.
        text = " ".join(out_path.read_text().split())

        # Numbered by first appearance, not by citekey order.
        assert 'data-cites="k1_2024">[1]</span>' in text
        assert 'data-cites="k2_2024">[2]</span>' in text
        # The collapsed form is the whole reason _collapsed_csl exists:
        # upstream ieee.csl alone renders this run as "[2], [3], [4], [5]".
        assert "[2]–[5]" in text
        assert "J. Doe" in text and "J. Examples" in text

    def test_no_collapse_leaves_the_style_as_published(self, isolated_config, tmp_path):
        con = ledger.connect()
        keys = [f"k{i}_2024" for i in range(1, 4)]
        for key in keys:
            ledger.upsert_reference(con, make_reference(citekey=key, title=f"Paper {key}", year="2024"))
        con.close()
        isolated_config.BIB_FILE_PATH.write_text("\n".join(
            f"@article{{{key},\n  author={{Doe, Jane}},\n  title={{Paper {key}}},\n"
            f"  journal={{J. Examples}},\n  year={{2024}},\n}}" for key in keys
        ))

        draft = tmp_path / "draft.md"
        draft.write_text("# Title\n\nA run [@k1_2024; @k2_2024; @k3_2024].\n")
        out_path = render_output.render(str(draft), output_format="html", collapse_citations=False)

        assert "[1], [2], [3]" in out_path.read_text()

    def test_missing_csl_style_is_reported_not_passed_to_pandoc(self, isolated_config, tmp_path):
        draft = tmp_path / "draft.md"
        draft.write_text("# Title\n\nNo citations here.\n")
        with pytest.raises(render_output.MissingBinary, match="CSL style not found"):
            render_output.render(str(draft), output_format="tex", csl=str(tmp_path / "nope.csl"))

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

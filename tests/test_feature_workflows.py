"""Feature tests: the actual chains a user runs, exercised with real
binaries (pdftotext/pandoc/pdflatex) and no mocking of the seams between
modules -- as distinct from the unit tests elsewhere, which mock those
seams deliberately for speed/determinism. These are slower but catch
integration regressions the unit tests structurally can't (e.g. sync's
real handoff to pdf_text, or a regression in the real bibliography.bib
export itself)."""

import shutil
import subprocess

import pytest

from src import bib_reader, citation_gate, config, ledger, references, sync
from src.heavy import render_output

pandoc_available = shutil.which("pandoc") is not None
pdflatex_available = shutil.which("pdflatex") is not None
pdftotext_available = shutil.which("pdftotext") is not None


def make_real_pdf(md_path, pdf_path, body):
    md_path.write_text(body)
    subprocess.run(
        ["pandoc", str(md_path), "-o", str(pdf_path), "--pdf-engine=pdflatex"],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(
    not (pandoc_available and pdflatex_available and pdftotext_available),
    reason="pandoc/pdflatex/pdftotext not installed",
)
class TestFullPipelineNoMocks:
    """bib -> sync (real pdftotext) -> draft -> citation_gate -> references
    -> render_output.render, with nothing mocked -- the real workflow
    CLAUDE.md describes, end to end."""

    def test_full_chain_with_real_binaries(self, isolated_config, tmp_path):
        pdf_md = tmp_path / "source.md"
        pdf_path = tmp_path / "paper.pdf"
        make_real_pdf(pdf_md, pdf_path, "# A Paper\n\nThis paper discusses distinctive digital twin content.\n")

        isolated_config.BIB_FILE_PATH.write_text(
            "@article{smith_realpaper_2024,\n"
            "  title = {A Real Paper About Digital Twins},\n"
            "  author = {Smith, Jane},\n"
            "  year = {2024},\n"
            "  file = {paper.pdf:paper.pdf:application/pdf},\n"
            "}\n"
        )

        rc = sync.run()
        assert rc == 0

        con = ledger.connect()
        try:
            row = {r["citekey"]: r for r in ledger.all_items(con)}["smith_realpaper_2024"]
        finally:
            con.close()
        assert row["status"] == "parsed"
        assert "distinctive digital twin content" in (config.PARSED_DIR / "smith_realpaper_2024.txt").read_text()

        draft = tmp_path / "draft.md"
        draft.write_text(
            "# Chapter\n\nAs shown by prior work [@smith_realpaper_2024], digital twins matter.\n"
        )

        gate_rc = citation_gate.run([str(draft)])
        assert gate_rc == 0

        result = references.apply(draft)
        assert "wrote References section" in result
        assert "smith_realpaper_2024" in draft.read_text()

        out_path = render_output.render(str(draft), output_format="tex")
        assert out_path.exists()
        assert out_path.read_text().strip()

    def test_fabricated_citation_is_blocked_before_render(self, isolated_config, tmp_path):
        """The hard invariant (CLAUDE.md): a citekey not in the ledger
        must fail the gate, not silently make it to a rendered draft."""
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{real_key_2024,\n  title = {Real},\n  author = {A, B},\n  year = {2024},\n}\n"
        )
        assert sync.run() == 0

        draft = tmp_path / "draft.md"
        draft.write_text("Citing a real source [@real_key_2024] and a fabricated one [@invented_2024].\n")

        rc = citation_gate.run([str(draft)])
        assert rc == 1  # must fail -- @invented_2024 was never synced from the bib file

        # references.apply would itself hard-error on the fabricated key,
        # which is the second line of defense if the gate is skipped.
        with pytest.raises(KeyError, match="invented_2024"):
            references.apply(draft)


class TestRealBibliographySmoke:
    """Parses this repo's actual bibliography.bib (read-only) -- catches
    a regression against real export data that a synthetic 1-3 entry
    fixture can't, which is exactly the failure class CLAUDE.md's hard
    invariant exists to prevent."""

    def test_real_bib_file_parses_without_error(self, isolated_config, monkeypatch):
        real_bib = config.REPO_ROOT / "papers" / "bibliography.bib"
        monkeypatch.setattr(config, "BIB_FILE_PATH", real_bib)

        refs = bib_reader.read_library()
        assert len(refs) == 642

        citekeys = {r.citekey for r in refs}
        assert len(citekeys) == len(refs), "citekeys must be unique"

        # Known awkward real entries this pipeline's design explicitly
        # accounts for: a no-author webpage export, and a citekey
        # containing "--" (render_output.py's alias workaround exists
        # because of exactly this key).
        assert "noauthor_digital_nodate" in citekeys
        assert "zech_digital-twins-as--service_2024" in citekeys

    def test_real_bib_citekeys_all_pass_citation_gate(self, isolated_config, monkeypatch):
        """Every real citekey, cited in Pandoc form, must be recognized
        as known once synced -- the gate's regex must not choke on any
        real citekey shape (hyphens, underscores, digits, "--")."""
        real_bib = config.REPO_ROOT / "papers" / "bibliography.bib"
        monkeypatch.setattr(config, "BIB_FILE_PATH", real_bib)

        refs = bib_reader.read_library()
        con = ledger.connect()
        try:
            for ref in refs:
                ledger.upsert_reference(con, ref)
        finally:
            con.close()

        draft = isolated_config.CONTENT_DIR / "all_citekeys.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("\n".join(f"[@{r.citekey}]" for r in refs))

        con = ledger.connect()
        try:
            known = ledger.known_citekeys(con)
        finally:
            con.close()
        result = citation_gate.check_document(draft, known)
        assert result.ok, result.unknown[:10]
        assert result.total_citations == len(refs)

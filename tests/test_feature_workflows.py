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

from src import bib_reader, citation_gate, config, dossier, ledger, references, sync
from src import render_output

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
    AGENTS.md describes, end to end."""

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
        """The hard invariant (AGENTS.md): a citekey not in the ledger
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


real_bib_available = (config.REPO_ROOT / "papers" / "bibliography.bib").exists()


@pytest.mark.skipif(
    not real_bib_available,
    reason="papers/bibliography.bib is gitignored, per-host data (AGENTS.md) -- "
           "absent on a fresh clone/CI checkout until someone exports their own",
)
class TestRealBibliographySmoke:
    """Parses this repo's actual bibliography.bib (read-only) -- catches
    a regression against real export data that a synthetic 1-3 entry
    fixture can't, which is exactly the failure class AGENTS.md's hard
    invariant exists to prevent."""

    def test_real_bib_file_parses_without_error(self, isolated_config, monkeypatch):
        real_bib = config.REPO_ROOT / "papers" / "bibliography.bib"
        monkeypatch.setattr(config, "BIB_FILE_PATH", real_bib)

        refs = bib_reader.read_library()
        assert len(refs) == 646

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


@pytest.mark.skipif(
    not (pandoc_available and pdflatex_available and pdftotext_available),
    reason="pandoc/pdflatex/pdftotext not installed",
)
class TestReparseReproducibility:
    """The ledger half of the reproducibility contract, pinned.

    docs/ARCHITECTURE.md's "What is reproducible, and what is not" makes
    two claims about a re-parse of unchanged input: every ledger column
    comes back byte-identical *except* `last_synced`, and `pdftotext`
    output is byte-identical. Both are the kind of promise that rots
    silently -- a new column defaulting to a timestamp, or a backend flag
    that reorders output, would break it with nothing failing.

    Real `pdftotext` on a real PDF rather than a mocked extractor,
    because a fake that returns a constant string would pass this test
    while proving nothing about the backend the claim is actually about.

    `last_synced` is asserted to **change** rather than quietly excluded.
    Excluding it would leave a test that passes whatever the contract
    says; asserting it encodes the exception as part of the promise.
    """

    def _rows(self):
        con = ledger.connect()
        try:
            return {r["citekey"]: dict(r) for r in ledger.all_items(con)}
        finally:
            con.close()

    def test_reparse_changes_only_last_synced(self, isolated_config, tmp_path):
        pdf_md = tmp_path / "source.md"
        pdf_path = tmp_path / "stable.pdf"
        make_real_pdf(pdf_md, pdf_path, "# Stable\n\nText that must survive a re-parse unchanged.\n")
        isolated_config.BIB_FILE_PATH.write_text(
            "@article{roe_stable_2024,\n"
            "  title = {A Stable Paper},\n"
            "  author = {Roe, Jan},\n"
            "  year = {2024},\n"
            f"  file = {{stable.pdf:{pdf_path}:application/pdf}},\n"
            "}\n"
        )

        assert sync.run() == 0
        first = self._rows()
        first_text = (config.PARSED_DIR / "roe_stable_2024.txt").read_bytes()

        # --reparse, not a plain re-run: a second sync would skip the
        # document on its unchanged hash and compare the parse against
        # itself, which tests the skip logic rather than the parser.
        assert sync.run(reparse=True) == 0
        second = self._rows()
        second_text = (config.PARSED_DIR / "roe_stable_2024.txt").read_bytes()

        assert first_text == second_text, "pdftotext output is not byte-identical"
        assert set(first) == set(second)
        row_a, row_b = first["roe_stable_2024"], second["roe_stable_2024"]
        assert row_a["last_synced"] != row_b["last_synced"], (
            "last_synced must change -- it is wall-clock, and the contract "
            "names it as the one column that does"
        )
        # Every other column, whatever the schema grows to. Written as a
        # difference rather than a fixed list so a column added later is
        # covered by this test on the day it lands, not the day someone
        # remembers to add it here.
        unstable = {k for k in row_a if row_a[k] != row_b[k]}
        assert unstable == {"last_synced"}, (
            f"columns changed across a re-parse of unchanged input: {sorted(unstable)}"
        )


def _add_paper(citekey, title, body):
    """One parsed paper in the ledger, with the text a query ranks against.

    Raw SQL for the same reason `tests/test_dossier.py` uses it:
    `upsert_reference` takes a `bib_reader.Reference` and would drag
    bibtexparser into a chain that is otherwise stdlib-only.
    """
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    parsed = config.PARSED_DIR / f"{citekey}.txt"
    parsed.write_text(body, encoding="utf-8")
    con = ledger.connect()
    try:
        con.execute(
            "INSERT INTO items (citekey, title, parsed_path, status, last_synced) "
            "VALUES (?, ?, ?, 'parsed', '2026-01-01')",
            (citekey, title, str(parsed)),
        )
        con.commit()
    finally:
        con.close()


def _drop_paper(citekey):
    """What `sync --remove-stale` does to a paper dropped from the bib."""
    con = ledger.connect()
    try:
        con.execute("DELETE FROM items WHERE citekey = ?", (citekey,))
        con.commit()
    finally:
        con.close()
    (config.PARSED_DIR / f"{citekey}.txt").unlink()


class TestReGroundingAfterTheCorpusMoves:
    """The chain `draft-reviser`'s re-grounding mode runs: a corpus that
    moved -> `dossier status --json` -> a scoped edit -> `citation_gate`.

    Characterisation rather than test-first: the mode itself lives in
    `.claude/skills/draft-reviser/SKILL.md`, which no test can assert on.
    What these pin is the composition underneath it -- the three findings
    the skill branches on, and the fact that acting on them needs no new
    machinery. A change in `src/` that broke the mode would otherwise
    break nothing that fails.

    No binaries: the seams here are dossier <-> ledger <-> citation_gate,
    all pure Python, so this runs everywhere rather than skipping wherever
    pandoc is absent.
    """

    @pytest.fixture
    def grounded(self, isolated_config):
        """A draft that cites one paper, turned another down, and logged
        the query that found both -- written against the corpus as it
        stood at the time, so `scope.md` records that fingerprint."""
        _add_paper("kept_paper_2024", "Digital twin architectures",
                   "Digital twin architectures for engineering systems.")
        _add_paper("turned_down_2023", "Digital twin adoption economics",
                   "Digital twin adoption economics and cost recovery.")

        draft = config.DRAFTS_DIR / "dt-for-engineers" / "survey.md"
        draft.parent.mkdir(parents=True)
        draft.write_text(
            "# A survey\n\n## 1. First\n\n"
            "Twins are structured this way [@kept_paper_2024].\n\n"
            "## 2. Second\n\nmore\n"
        )

        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        (target / "evidence.md").write_text(
            "# Kept evidence\n\n## `kept_paper_2024`\n\nHow twins are structured.\n"
        )
        (target / "rejected.md").write_text(
            "# Rejected candidates\n\n| citekey | query that surfaced it | why rejected |\n"
            "|---|---|---|\n| `turned_down_2023` | digital twin | out of scope: adoption economics |\n"
        )
        (target / "sections.md").write_text(
            "# Sections and their citekeys\n\n| section | citekeys |\n|---|---|\n"
            "| 1. First | `kept_paper_2024` |\n"
        )
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)

        assert citation_gate.run([str(draft)]) == 0, "the draft starts sound"
        return draft

    def test_a_moved_corpus_produces_the_three_findings_and_a_failing_gate(self, grounded):
        """`missing` is a defect the gate agrees with; `candidates` are new;
        a paper already turned down is held back in `reconsider`."""
        _drop_paper("kept_paper_2024")
        _add_paper("fresh_twin_2026", "Digital twin fidelity",
                   "Digital twin fidelity metrics for engineering models.")

        report = dossier.drift(dossier.dossier_dir(grounded))

        # A defect: the draft stands on a paper the corpus no longer has,
        # carried with the section that cites it so the edit stays scoped.
        assert report.missing == {"kept_paper_2024": ["1. First"]}
        assert citation_gate.run([str(grounded)]) == 1, (
            "the gate is the exit criterion, and it must already disagree "
            "with a draft the drift report calls broken"
        )

        # An opportunity, and a separate kind of thing.
        assert [c.citekey for c in report.candidates] == ["fresh_twin_2026"]

        # Already judged once. `rejected.md` was subtracted from
        # `candidates`, and the reason is carried so the skill can weigh
        # it without re-retrieving and re-judging the paper.
        assert [r.citekey for r in report.reconsider] == ["turned_down_2023"]
        assert report.reconsider[0].reason == "out of scope: adoption economics"

        assert report.drifted, "the recorded fingerprint no longer matches"

    def test_the_json_the_skill_reads_carries_all_three(self, grounded, capsys):
        """One envelope, one element, exit 0 -- the contract #85 added for
        this mode. The skill branches on the payload, never the code."""
        _drop_paper("kept_paper_2024")
        _add_paper("fresh_twin_2026", "Digital twin fidelity",
                   "Digital twin fidelity metrics for engineering models.")

        assert dossier.main(["status", str(grounded), "--json"]) == 0
        (entry,) = __import__("json").loads(capsys.readouterr().out)["dossiers"]

        assert entry["corpus_available"] is True
        assert entry["missing"] == {"kept_paper_2024": ["1. First"]}
        assert [c["citekey"] for c in entry["candidates"]] == ["fresh_twin_2026"]
        assert entry["reconsider"][0]["reason"] == "out of scope: adoption economics"
        assert entry["current"] is not None, "the values a re-stamp writes back"

    def test_a_scoped_re_grounding_clears_the_defect_and_passes_the_gate(self, grounded):
        """Swapping the citation and recording the swap drives `missing`
        empty and the gate back to OK -- with nothing in `src/` beyond the
        files the skill already writes, and no fingerprint change."""
        _drop_paper("kept_paper_2024")
        _add_paper("fresh_twin_2026", "Digital twin fidelity",
                   "Digital twin fidelity metrics for engineering models.")
        target = dossier.dossier_dir(grounded)
        before = dossier.recorded_corpus(target)

        # Exactly what the skill does: edit inside the one section the
        # report named, then write the dossier back.
        grounded.write_text(
            grounded.read_text().replace("@kept_paper_2024", "@fresh_twin_2026")
        )
        (target / "evidence.md").write_text(
            "# Kept evidence\n\n## `fresh_twin_2026`\n\nHow twins are structured.\n"
        )
        (target / "sections.md").write_text(
            "# Sections and their citekeys\n\n| section | citekeys |\n|---|---|\n"
            "| 1. First | `fresh_twin_2026` |\n"
        )

        report = dossier.drift(target)
        assert not report.missing, "the defect is gone"
        assert "fresh_twin_2026" not in [c.citekey for c in report.candidates], (
            "an accepted candidate leaves the list by being recorded in evidence.md"
        )
        assert citation_gate.run([str(grounded)]) == 0

        assert dossier.recorded_corpus(target) == before, (
            "clearing the defect needs no fingerprint re-stamp -- the "
            "re-stamp is bookkeeping the skill does afterwards, not the "
            "mechanism"
        )

    def test_re_stamping_the_fingerprint_clears_drifted_and_still_parses(self, grounded):
        """The one step of the mode with a silent failure mode.

        `scope.md`'s corpus line is written once by `init` and rewritten
        only here. Reshaping it rather than rewriting it makes
        `recorded_corpus()` return `None`, and the dossier downgrades to
        "records no corpus fingerprint" instead of erroring -- so the
        skill is told to rewrite the line in place, and this is what says
        that the line it writes is one the parser accepts.
        """
        _drop_paper("kept_paper_2024")
        _add_paper("fresh_twin_2026", "Digital twin fidelity",
                   "Digital twin fidelity metrics for engineering models.")
        target = dossier.dossier_dir(grounded)
        scope = target / "scope.md"

        (count, digest) = dossier.drift(target).current
        old = dossier.recorded_corpus(target)
        scope.write_text(scope.read_text().replace(
            f"- corpus: {old[0]} citekeys, digest `{old[1]}`",
            f"- corpus: {count} citekeys, digest `{digest}`",
        ))

        assert dossier.recorded_corpus(target) == (count, digest), (
            "the re-stamped line must still match what `init` wrote"
        )
        assert not dossier.drift(target).drifted

    def test_an_unpursued_candidate_keeps_the_dossier_unclean(self, grounded):
        """The limit of the claim above, pinned deliberately.

        `Drift.clean` is `not missing and not candidates`, so a dossier
        with any candidate left unpursued still shows in the sweep -- and
        on a real corpus that is the normal outcome, since a query returns
        fifteen hits and a revision accepts one or two. Driving `clean`
        true would mean writing the rest into `rejected.md` on nothing but
        a title, which is the judgment `docs/REJECTION.md` refuses to make
        cheaply. Re-grounding therefore promises `missing`, not `clean`.
        """
        _drop_paper("kept_paper_2024")
        _add_paper("fresh_twin_2026", "Digital twin fidelity",
                   "Digital twin fidelity metrics for engineering models.")
        _add_paper("other_twin_2026", "Digital twin calibration",
                   "Digital twin calibration under drift in engineering use.")
        target = dossier.dossier_dir(grounded)

        grounded.write_text(
            grounded.read_text().replace("@kept_paper_2024", "@fresh_twin_2026")
        )
        (target / "evidence.md").write_text(
            "# Kept evidence\n\n## `fresh_twin_2026`\n\nHow twins are structured.\n"
        )
        (target / "sections.md").write_text(
            "# Sections and their citekeys\n\n| section | citekeys |\n|---|---|\n"
            "| 1. First | `fresh_twin_2026` |\n"
        )

        report = dossier.drift(target)
        assert not report.missing
        assert [c.citekey for c in report.candidates] == ["other_twin_2026"]
        assert not report.clean, (
            "an unweighed candidate is still a decision the sweep should surface"
        )

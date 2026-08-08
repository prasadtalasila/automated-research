"""Tests for src/dossier.py.

Three things carry most of the weight here, because they are the three
that can lose someone's work or waste the tokens the module exists to
save:

- the mirroring rule (`content/drafts/x/y.md` <-> `content/dossiers/x/y/`),
  since nothing else ties a draft to its dossier;
- outline extraction, since a wrong line range hands a reviser a slice
  that cuts a section in half -- and the shipped example tutorial is
  mostly fenced code full of `#` comments;
- restore, the one destructive operation, which must refuse an unsafe
  archive outright and must not write at all without --force.
"""

import tarfile
from pathlib import Path

import pytest

from src import config, dossier


@pytest.fixture
def draft(isolated_config):
    """A draft where a genre skill would save one, in the nested layout
    the shipped example content uses."""
    path = config.DRAFTS_DIR / "dt-for-engineers" / "survey.md"
    path.parent.mkdir(parents=True)
    path.write_text("# A survey\n\n## 1. First\n\ntext\n\n## 2. Second\n\nmore\n")
    return path


def _seed_ledger(citekeys):
    """A ledger holding just these citekeys.

    Inserted with raw SQL rather than through `upsert_reference`, which
    takes a `bib_reader.Reference` and so would drag bibtexparser into a
    module that is deliberately stdlib-only.
    """
    from src import ledger

    con = ledger.connect()
    try:
        con.executemany(
            "INSERT INTO items (citekey, status, last_synced) VALUES (?, 'parsed', '2026-01-01')",
            [(key,) for key in citekeys],
        )
        con.commit()
    finally:
        con.close()


class TestDossierDir:
    def test_mirrors_a_nested_draft_path(self, draft):
        assert dossier.dossier_dir(draft) == config.DOSSIERS_DIR / "dt-for-engineers" / "survey"

    def test_mirrors_a_flat_draft_path(self, isolated_config):
        config.DRAFTS_DIR.mkdir(parents=True)
        flat = config.DRAFTS_DIR / "survey.md"
        flat.write_text("# x\n")
        assert dossier.dossier_dir(flat) == config.DOSSIERS_DIR / "survey"

    def test_a_tex_draft_mirrors_the_same_way(self, isolated_config):
        config.DRAFTS_DIR.mkdir(parents=True)
        tex = config.DRAFTS_DIR / "thesis.tex"
        tex.write_text("\\section{x}\n")
        assert dossier.dossier_dir(tex) == config.DOSSIERS_DIR / "thesis"

    def test_a_draft_outside_the_drafts_dir_is_refused(self, isolated_config, tmp_path):
        stray = tmp_path / "elsewhere.md"
        stray.write_text("# x\n")
        with pytest.raises(dossier.DossierError, match="not under"):
            dossier.dossier_dir(stray)

    def test_find_draft_is_the_inverse(self, draft):
        assert dossier.find_draft(dossier.dossier_dir(draft)) == draft

    def test_find_draft_returns_none_when_the_draft_is_gone(self, draft):
        target = dossier.dossier_dir(draft)
        draft.unlink()
        assert dossier.find_draft(target) is None

    def test_find_draft_finds_a_tex_draft(self, isolated_config):
        config.DRAFTS_DIR.mkdir(parents=True)
        tex = config.DRAFTS_DIR / "thesis.tex"
        tex.write_text("\\section{x}\n")
        assert dossier.find_draft(config.DOSSIERS_DIR / "thesis") == tex

    def test_draft_name_is_the_path_under_drafts_without_a_suffix(self, draft):
        assert dossier.draft_name(draft) == "dt-for-engineers/survey"


class TestSections:
    def test_line_ranges_run_to_the_next_heading(self):
        outline = dossier.sections("# Title\n\nintro\n\n## One\n\na\n\n## Two\n\nb\n")
        assert [(s.title, s.start, s.end) for s in outline] == [
            ("Title", 1, 4),
            ("One", 5, 8),
            ("Two", 9, 11),
        ]

    def test_the_last_section_runs_to_the_end_of_the_file(self):
        (last,) = dossier.sections("## Only\n\na\nb\nc\n")
        assert (last.start, last.end, last.lines) == (1, 5, 5)

    def test_heading_levels_are_recorded(self):
        outline = dossier.sections("# A\n## B\n### C\n")
        assert [s.level for s in outline] == [1, 2, 3]

    def test_a_hash_comment_inside_a_fenced_block_is_not_a_heading(self):
        text = (
            "# Tutorial\n"
            "\n"
            "```bash\n"
            "# Step 1: make the folder\n"
            "mkdir pot\n"
            "```\n"
            "\n"
            "## Real heading\n"
        )
        assert [s.title for s in dossier.sections(text)] == ["Tutorial", "Real heading"]

    def test_a_tilde_fence_is_tracked_too(self):
        text = "# T\n\n~~~python\n# not a heading\n~~~\n\n## Real\n"
        assert [s.title for s in dossier.sections(text)] == ["T", "Real"]

    def test_latex_sectioning_commands_are_recognised(self):
        text = "\\chapter{Ch}\ntext\n\\section{Sec}\nmore\n\\subsection{Sub}\n"
        outline = dossier.sections(text)
        assert [(s.title, s.level) for s in outline] == [
            ("Ch", 1), ("Sec", 2), ("Sub", 3),
        ]

    def test_a_latex_title_containing_braces_keeps_its_whole_title(self):
        (only,) = dossier.sections("\\section{The \\emph{twin} problem}\n")
        assert only.title == "The \\emph{twin} problem"

    def test_a_trailing_label_is_not_swallowed_into_the_title(self):
        (only,) = dossier.sections("\\section{Architecture}\\label{sec:arch}\n")
        assert only.title == "Architecture"

    def test_an_unterminated_latex_title_still_yields_a_section(self):
        (only,) = dossier.sections("\\section{A title that wraps\n")
        assert only.title == "A title that wraps"

    def test_a_section_command_inside_verbatim_is_not_a_heading(self):
        text = (
            "\\section{Real}\n"
            "\\begin{lstlisting}\n"
            "\\section{Not real}\n"
            "\\end{lstlisting}\n"
            "\\section{Also real}\n"
        )
        assert [s.title for s in dossier.sections(text)] == ["Real", "Also real"]

    def test_a_closing_hash_run_is_stripped_from_the_title(self):
        (only,) = dossier.sections("## Balanced ##\n")
        assert only.title == "Balanced"

    def test_a_draft_with_no_headings_yields_nothing(self):
        assert dossier.sections("just prose\nover two lines\n") == []

    def test_the_shipped_example_tutorial_outlines_cleanly(self):
        """Regression guard against the fence bug on real content: the
        example tutorial is mostly shell and Python whose comments start
        with `#`."""
        example = (
            config.REPO_ROOT
            / "content/drafts/digital-twins-for-software-engineers/tutorial.md"
        )
        if not example.is_file():  # pragma: no cover - example content is optional
            pytest.skip("example content not present in this checkout")
        titles = [s.title for s in dossier.sections(example.read_text())]
        assert titles[0] == "Build a Digital Twin for a Potted Plant"
        assert "Step 1: Create the project folder" in titles
        assert not any(t.startswith("!") or t.startswith("/") for t in titles)


class TestInit:
    def test_writes_every_dossier_file(self, draft):
        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        assert {p.name for p in target.iterdir()} == {"README.md", *dossier.FILES}

    def test_is_idempotent_and_does_not_clobber_filled_in_files(self, draft):
        dossier.init(draft, "survey")
        evidence = dossier.dossier_dir(draft) / "evidence.md"
        evidence.write_text("# Kept evidence\n\n## `talasila_composable_2025`\n")
        written = dossier.init(draft, "survey")
        assert written == []
        assert "talasila_composable_2025" in evidence.read_text()

    def test_replaces_only_a_deleted_file(self, draft):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "steering.md").unlink()
        written = dossier.init(draft, "survey")
        assert [p.name for p in written] == ["steering.md"]

    def test_records_the_corpus_fingerprint(self, draft):
        _seed_ledger(["a_one_2020", "b_two_2021"])
        dossier.init(draft, "survey")
        recorded = dossier.recorded_corpus(dossier.dossier_dir(draft))
        assert recorded == (2, dossier.digest({"a_one_2020", "b_two_2021"}))

    def test_says_so_when_there_is_no_ledger_to_fingerprint(self, draft):
        dossier.init(draft, "survey")
        scope = (dossier.dossier_dir(draft) / "scope.md").read_text()
        assert "not recorded" in scope
        assert dossier.recorded_corpus(dossier.dossier_dir(draft)) is None

    def test_the_genre_reaches_the_dossier(self, draft):
        dossier.init(draft, "thesis-chapter")
        assert "genre: thesis-chapter" in (dossier.dossier_dir(draft) / "scope.md").read_text()


class TestKnownCitekeys:
    def test_returns_none_without_a_ledger(self, isolated_config):
        assert dossier.known_citekeys() is None

    def test_returns_none_for_a_ledger_that_is_not_a_database(self, isolated_config):
        config.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.LEDGER_PATH.write_text("not sqlite")
        assert dossier.known_citekeys() is None

    def test_an_empty_ledger_is_a_set_not_none(self, isolated_config):
        _seed_ledger([])
        assert dossier.known_citekeys() == set()

    def test_the_digest_ignores_insertion_order(self):
        assert dossier.digest({"b", "a"}) == dossier.digest({"a", "b"})


class TestStatus:
    def test_reports_a_missing_dossier_without_raising(self, draft):
        report = dossier.status(draft)
        assert report.files and not any(f.present for f in report.files)

    def test_counts_entries_per_file(self, draft):
        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        (target / "evidence.md").write_text(
            "# Kept evidence\n\n## `a_one_2020`\n\n- relevance: x\n\n## `b_two_2021`\n\n- relevance: y\n"
        )
        (target / "rejected.md").write_text(
            "# Rejected\n\n| citekey | query | why |\n|---|---|---|\n"
            "| `c_three_2022` | q | off-topic |\n"
        )
        by_name = {f.name: f for f in dossier.status(draft).files}
        assert by_name["evidence.md"].entries == 2
        assert by_name["rejected.md"].entries == 1

    def test_a_prose_file_reports_filled_in_rather_than_a_count(self, draft, capsys):
        """A count is information for the list-shaped files and noise for
        the prose ones -- "scope.md: 40 entries" is a number dressed up."""
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "steering.md").write_text(
            "# Steering\n\n## 2026-08-06 -- shorter\n\nCut the platform section.\n"
        )
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "steering.md   filled in" in out
        assert "steering.md   2 entries" not in out

    def test_a_skeleton_file_counts_as_empty(self, draft):
        dossier.init(draft, "survey")
        by_name = {f.name: f for f in dossier.status(draft).files}
        assert by_name["evidence.md"].entries == 0
        assert by_name["rejected.md"].entries == 0
        assert by_name["steering.md"].entries == 0

    def test_the_outline_comes_back_with_the_status(self, draft):
        dossier.init(draft, "survey")
        assert [s.title for s in dossier.status(draft).outline] == [
            "A survey", "1. First", "2. Second",
        ]

    def test_drift_is_flagged_when_the_corpus_moves(self, draft):
        _seed_ledger(["a_one_2020"])
        dossier.init(draft, "survey")
        _seed_ledger(["b_two_2021"])
        report = dossier.status(draft)
        assert report.drifted
        assert report.recorded[0] == 1 and report.current[0] == 2

    def test_an_unchanged_corpus_does_not_drift(self, draft):
        _seed_ledger(["a_one_2020"])
        dossier.init(draft, "survey")
        assert not dossier.status(draft).drifted

    def test_citekeys_nowhere_in_the_dossier_are_named(self, draft):
        _seed_ledger(["a_one_2020", "b_two_2021"])
        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        (target / "evidence.md").write_text("# Kept\n\n## `a_one_2020`\n")
        assert dossier.status(draft).unconsidered == {"b_two_2021"}

    def test_a_rejected_citekey_counts_as_considered(self, draft):
        _seed_ledger(["a_one_2020", "b_two_2021"])
        dossier.init(draft, "survey")
        target = dossier.dossier_dir(draft)
        (target / "rejected.md").write_text(
            "| citekey | query | why |\n|---|---|---|\n| `b_two_2021` | q | off-topic |\n"
        )
        assert "b_two_2021" not in dossier.status(draft).unconsidered

    def test_backticked_prose_is_not_mistaken_for_a_citekey(self, draft):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "evidence.md").write_text(
            "# Kept\n\nRun `status` with `--force` on `content`.\n"
        )
        assert dossier.cited_citekeys(dossier.dossier_dir(draft)) == set()

    def test_a_separator_is_what_distinguishes_a_citekey_from_prose(self, draft):
        """Pins the rule `_CITEKEY_TOKEN`'s comment states: a letter start
        plus at least one separator-then-alphanumeric segment."""
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "evidence.md").write_text(
            "# Kept\n\n"
            "Real: `talasila_composable_2025`, `zech_digital-twins-as--service_2024`.\n"
            "Prose: `status`, `--force`, `content`, `md`.\n"
        )
        assert dossier.cited_citekeys(dossier.dossier_dir(draft)) == {
            "talasila_composable_2025",
            "zech_digital-twins-as--service_2024",
        }

    def test_drift_is_unavailable_rather_than_fatal_without_a_ledger(self, draft):
        dossier.init(draft, "survey")
        report = dossier.status(draft)
        assert report.current is None and not report.drifted

    def test_accepts_the_dossier_directory_as_well_as_the_draft(self, draft):
        dossier.init(draft, "survey")
        report = dossier.status(dossier.dossier_dir(draft))
        assert report.draft == draft

    def test_reports_a_dossier_that_outlived_its_draft(self, draft):
        dossier.init(draft, "survey")
        draft.unlink()
        report = dossier.status(dossier.dossier_dir(draft))
        assert report.draft is None and report.outline == []


class TestList:
    def test_finds_every_dossier(self, draft, isolated_config):
        other = config.DRAFTS_DIR / "other.md"
        other.write_text("# other\n")
        dossier.init(draft, "survey")
        dossier.init(other, "tutorial")
        assert dossier.all_dossiers() == [
            config.DOSSIERS_DIR / "dt-for-engineers" / "survey",
            config.DOSSIERS_DIR / "other",
        ]

    def test_no_dossiers_directory_is_not_an_error(self, isolated_config):
        assert dossier.all_dossiers() == []


class TestExport:
    def test_bundles_the_draft_and_its_dossier(self, draft):
        dossier.init(draft, "survey")
        names = {name for _, name in dossier.bundle_members([], with_rendered=False)}
        assert "drafts/dt-for-engineers/survey.md" in names
        assert "dossiers/dt-for-engineers/survey/scope.md" in names

    def test_rendered_output_is_opt_in(self, draft):
        config.RENDERED_DIR.mkdir(parents=True)
        (config.RENDERED_DIR / "survey.pdf").write_bytes(b"%PDF")
        assert not any(
            name.startswith("rendered/")
            for _, name in dossier.bundle_members([], with_rendered=False)
        )
        assert any(
            name.startswith("rendered/")
            for _, name in dossier.bundle_members([], with_rendered=True)
        )

    def test_a_name_selects_one_topic_directory(self, draft, isolated_config):
        other = config.DRAFTS_DIR / "unrelated.md"
        other.write_text("# other\n")
        dossier.init(draft, "survey")
        dossier.init(other, "tutorial")
        names = {
            name
            for _, name in dossier.bundle_members(["dt-for-engineers"], with_rendered=False)
        }
        assert "drafts/dt-for-engineers/survey.md" in names
        assert "dossiers/dt-for-engineers/survey/scope.md" in names
        assert not any("unrelated" in name for name in names)

    def test_a_name_can_be_a_single_flat_draft(self, isolated_config):
        config.DRAFTS_DIR.mkdir(parents=True)
        (config.DRAFTS_DIR / "survey.md").write_text("# s\n")
        (config.DRAFTS_DIR / "tutorial.md").write_text("# t\n")
        names = {name for _, name in dossier.bundle_members(["survey"], with_rendered=False)}
        assert names == {"drafts/survey.md"}

    def test_exporting_nothing_is_an_error_rather_than_an_empty_archive(self, isolated_config):
        with pytest.raises(dossier.DossierError, match="Nothing to export"):
            dossier.export([], Path("out.tar.gz"))

    def test_writes_an_archive(self, draft, tmp_path):
        dossier.init(draft, "survey")
        out, count = dossier.export([], tmp_path / "bundle.tar.gz")
        assert out.is_file() and count >= 2
        with tarfile.open(out) as tar:
            assert "drafts/dt-for-engineers/survey.md" in tar.getnames()


class TestRestore:
    @pytest.fixture
    def bundle(self, draft, tmp_path):
        dossier.init(draft, "survey")
        out, _ = dossier.export([], tmp_path / "bundle.tar.gz")
        return out

    def test_is_a_dry_run_by_default(self, bundle, draft):
        draft.unlink()
        plan = dossier.restore(bundle)
        assert not plan.performed
        assert not draft.exists()
        assert draft in plan.new

    def test_force_writes_the_files_back(self, bundle, draft):
        target = dossier.dossier_dir(draft)
        draft.unlink()
        (target / "scope.md").unlink()
        plan = dossier.restore(bundle, force=True)
        assert plan.performed
        assert draft.is_file()
        assert (target / "scope.md").is_file()

    def test_reports_which_files_it_would_overwrite(self, bundle, draft):
        plan = dossier.restore(bundle)
        assert draft in plan.overwrite and not plan.new

    def test_round_trips_content_exactly(self, bundle, draft):
        original = draft.read_text()
        draft.write_text("# clobbered\n")
        dossier.restore(bundle, force=True)
        assert draft.read_text() == original

    def test_a_path_too_long_for_a_tar_header_round_trips(self, isolated_config, tmp_path):
        """`_checked_members` refuses anything that isn't a regular file or
        directory, which raises the question of whether the extended
        headers tar uses for a >100-character path survive that check.

        They do, and not by luck: Python's `tarfile` consumes GNU longname
        (`L`/`K`) and PAX (`x`/`g`) header blocks while reading and folds
        them into the member they describe, so `getmembers()` only ever
        yields the real entry. Pinned here rather than argued, because the
        failure it would cause -- `export` producing a bundle its own
        `restore` refuses -- is exactly the kind a backup tool must not
        have.
        """
        deep = config.DRAFTS_DIR / ("topic-" + "x" * 90) / ("sub-" + "y" * 90)
        deep.mkdir(parents=True)
        draft = deep / ("survey-" + "z" * 80 + ".md")
        draft.write_text("# A survey with an inconveniently long path\n")
        assert len(str(draft.relative_to(config.DRAFTS_DIR))) > 100

        dossier.init(draft, "survey")
        archive, _ = dossier.export([], tmp_path / "long.tar.gz")

        draft.unlink()
        plan = dossier.restore(archive, force=True)
        assert plan.performed
        assert draft.is_file()
        assert draft.read_text() == "# A survey with an inconveniently long path\n"
        assert (dossier.dossier_dir(draft) / "scope.md").is_file()

    def _archive_containing(self, tmp_path, name, payload=b"x"):
        archive = tmp_path / "hostile.tar.gz"
        member = tmp_path / "payload"
        member.write_bytes(payload)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(member, arcname=name)
        return archive

    def test_refuses_a_member_that_escapes_the_content_directory(self, isolated_config, tmp_path):
        archive = self._archive_containing(tmp_path, "drafts/../../../etc/passwd")
        with pytest.raises(dossier.DossierError, match="escapes"):
            dossier.restore(archive, force=True)

    def test_refuses_an_absolute_member(self, isolated_config, tmp_path):
        archive = tmp_path / "abs.tar.gz"
        payload = tmp_path / "payload"
        payload.write_bytes(b"x")
        with tarfile.open(archive, "w:gz") as tar:
            info = tar.gettarinfo(payload, arcname="/etc/passwd")
            with payload.open("rb") as handle:
                tar.addfile(info, handle)
        with pytest.raises(dossier.DossierError, match="escapes|not under"):
            dossier.restore(archive, force=True)

    def test_refuses_a_member_outside_the_three_known_directories(self, isolated_config, tmp_path):
        archive = self._archive_containing(tmp_path, "ledger.sqlite")
        with pytest.raises(dossier.DossierError, match="not under"):
            dossier.restore(archive, force=True)

    def test_refuses_a_symlink_member(self, isolated_config, tmp_path):
        archive = tmp_path / "link.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo("drafts/evil.md")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        with pytest.raises(dossier.DossierError, match="not a regular file"):
            dossier.restore(archive, force=True)

    def test_an_unsafe_member_blocks_the_whole_archive(self, isolated_config, tmp_path):
        archive = tmp_path / "mixed.tar.gz"
        good = tmp_path / "good.md"
        good.write_text("# fine\n")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(good, arcname="drafts/good.md")
            tar.add(good, arcname="../escape.md")
        with pytest.raises(dossier.DossierError):
            dossier.restore(archive, force=True)
        assert not (config.DRAFTS_DIR / "good.md").exists()


class TestCli:
    def test_init_then_status_then_sections(self, draft, capsys):
        assert dossier.main(["init", str(draft), "--genre", "survey"]) == 0
        assert dossier.main(["status", str(draft)]) == 0
        assert dossier.main(["sections", str(draft)]) == 0
        out = capsys.readouterr().out
        assert "scope.md" in out and "1. First" in out

    def test_status_without_a_dossier_exits_nonzero_with_the_fix(self, draft, capsys):
        assert dossier.main(["status", str(draft)]) == 1
        assert "init" in capsys.readouterr().out

    def test_status_without_a_ledger_still_exits_zero(self, draft, capsys):
        """The two "missing" cases are deliberately different exit codes,
        and docs/CLI.md documents the difference: no dossier is actionable
        ("run init"), no ledger just means one section of the report is
        unavailable. A machine with no corpus built must still be able to
        see what it has."""
        dossier.init(draft, "survey")
        assert dossier.main(["status", str(draft)]) == 0
        assert "unavailable" in capsys.readouterr().out

    def test_sections_on_a_missing_draft_exits_nonzero(self, isolated_config, capsys):
        assert dossier.main(["sections", "content/drafts/nope.md"]) == 1
        assert "No such draft" in capsys.readouterr().err

    def test_list_with_nothing_to_list(self, isolated_config, capsys):
        assert dossier.main(["list"]) == 0
        assert "No dossiers" in capsys.readouterr().out

    def test_export_then_restore_round_trip(self, draft, tmp_path, capsys):
        dossier.main(["init", str(draft), "--genre", "survey"])
        archive = tmp_path / "b.tar.gz"
        assert dossier.main(["export", "--out", str(archive)]) == 0
        draft.unlink()
        assert dossier.main(["restore", str(archive)]) == 0
        assert not draft.exists(), "a dry run must not write"
        assert "Would restore" in capsys.readouterr().out
        assert dossier.main(["restore", str(archive), "--force"]) == 0
        assert draft.is_file()

    def test_export_with_nothing_to_export_exits_nonzero(self, isolated_config, capsys):
        assert dossier.main(["export"]) == 1
        assert "Nothing to export" in capsys.readouterr().err

    def test_restore_of_a_missing_archive_exits_nonzero(self, isolated_config, capsys):
        assert dossier.main(["restore", "nope.tar.gz"]) == 1
        assert "No such archive" in capsys.readouterr().err

    def test_init_on_a_draft_outside_drafts_reports_the_rule(self, isolated_config, tmp_path, capsys):
        stray = tmp_path / "stray.md"
        stray.write_text("# x\n")
        assert dossier.main(["init", str(stray), "--genre", "survey"]) == 1
        assert "not under" in capsys.readouterr().err


class TestRetrievalLog:
    def test_appends_a_row_and_totals_it(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)
        dossier.log_retrieval(draft, "evidence", "digital twin", 1, 3, 2100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (2, 4500)

    def test_creates_the_file_for_a_dossier_that_predates_it(self, draft):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "retrieval.md").unlink()
        dossier.log_retrieval(draft, "search", "q", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_creates_the_dossier_when_a_skill_logs_before_init(self, draft):
        dossier.log_retrieval(draft, "search", "q", 15, 15, 100)
        assert (dossier.dossier_dir(draft) / "retrieval.md").is_file()

    def test_logging_before_init_leaves_init_free_to_write_the_rest(self, draft):
        dossier.log_retrieval(draft, "search", "q", 15, 15, 100)
        written = {path.name for path in dossier.init(draft, "survey")}
        assert "retrieval.md" not in written
        assert "scope.md" in written
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_a_pipe_in_the_query_does_not_break_the_row(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "twin | shadow", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_a_hand_edited_row_is_skipped_rather_than_fatal(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "q", 15, 15, 100)
        path = dossier.dossier_dir(draft) / "retrieval.md"
        path.write_text(path.read_text() + "| 2026-08-06 | search | q | 15 | 15 | lots |\n")
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_no_log_means_no_cost(self, draft):
        dossier.init(draft, "survey")
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (0, 0)

    def test_status_reports_the_measured_cost(self, draft, capsys):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "1 call(s) returned 2,400 characters" in out

    def test_status_says_nothing_about_retrieval_when_nothing_was_logged(self, draft, capsys):
        dossier.init(draft, "survey")
        dossier.main(["status", str(draft)])
        assert "call(s) returned" not in capsys.readouterr().out

    def test_a_stale_existence_check_cannot_truncate_another_writers_row(
        self, draft, monkeypatch
    ):
        """Two writers reaching this function at once both see the file
        as absent; the one that loses the create must not destroy the
        winner's row.

        The window is real -- `--log` is a flag on the retrieval CLI, and
        a skill that dispatched parallel subagents could hand it to all
        of them. It is reproduced here by making the second call's
        existence check stale, which is exactly what the loser of the
        race observes, rather than by racing real processes and hoping
        the interleaving lands.
        """
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "retrieval.md").unlink()
        dossier.log_retrieval(draft, "search", "first", 15, 15, 100)

        # A nested context, not monkeypatch.undo(): undo() would also
        # revert `isolated_config`'s patches, which share this fixture.
        with monkeypatch.context() as stale:
            stale.setattr(Path, "exists", lambda self: False)
            dossier.log_retrieval(draft, "search", "second", 15, 15, 200)

        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (2, 300)

    def test_a_row_appended_while_the_file_is_being_created_is_not_overwritten(
        self, draft, monkeypatch
    ):
        """The narrower race inside the create itself.

        Making the file exist and filling it in are two steps, and a
        second writer can append between them -- it finds the file
        already there, so it skips creation and appends to what is still
        an empty file. If the creating writer then writes the template
        from offset 0, it writes straight over that row.

        Reproduced by injecting a second writer at the moment
        `retrieval.md` is first opened for writing, rather than by racing
        real processes: the window is microseconds wide and process
        startup dwarfs it, so eight concurrent processes hit it
        essentially never and prove nothing.
        """
        dossier.init(draft, "survey")
        path = dossier.dossier_dir(draft) / "retrieval.md"
        path.unlink()

        real_open, injected = Path.open, []

        def racing_open(self, mode="r", *args, **kwargs):
            handle = real_open(self, mode, *args, **kwargs)
            if self.name == "retrieval.md" and "r" not in mode and not injected:
                injected.append(True)
                with real_open(self, "a", encoding="utf-8") as other:
                    other.write("| 2026-08-08 | search | loser | 15 | 15 | 200 |\n")
            return handle

        with monkeypatch.context() as racing:
            racing.setattr(Path, "open", racing_open)
            dossier.log_retrieval(draft, "search", "winner", 15, 15, 100)

        assert injected, "the interleaving never happened, so nothing was tested"
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (2, 300)

    def test_status_flags_a_run_that_searched_and_recorded_nothing(self, draft, capsys):
        """The signature of a run that closed without transcribing what
        it found. Nothing else reports it: the packets are gone, and the
        draft itself looks finished."""
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "searched and recorded nothing it found" in out

    def test_status_does_not_flag_a_dossier_that_recorded_what_it_found(self, draft, capsys):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)
        evidence = dossier.dossier_dir(draft) / "evidence.md"
        evidence.write_text(evidence.read_text() + "\n## ferko_architecting_2022\n")
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "1 kept, 0 rejected" in out
        assert "searched and recorded nothing" not in out

    def test_a_newline_in_the_query_does_not_split_the_row(self, draft):
        """`retrieval_cost` reads rows positionally, so a query carrying a
        newline would not error -- it would quietly become two rows, one
        of which parses and one of which doesn't."""
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin\narchitecture", 15, 15, 100)
        text = (dossier.dossier_dir(draft) / "retrieval.md").read_text()
        assert "digital twin architecture" in text
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_tabs_and_carriage_returns_are_flattened_too(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "twin\tshadow\r\nmodel", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)


# ---------------------------------------------------------------------
# Corpus drift across every dossier (`status --all`)
# ---------------------------------------------------------------------


def _seed_corpus(entries):
    """A ledger holding these citekeys, each with a real parsed text file.

    `_seed_ledger` above is enough for the fingerprint tests, which only
    need citekeys to exist. Query matching needs the text those citekeys
    rank against, so this writes `content/parsed/<citekey>.txt` and points
    the ledger's `parsed_path` at it -- the same shape `sync` produces.
    """
    from src import ledger

    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    con = ledger.connect()
    try:
        for citekey, title, body in entries:
            parsed = config.PARSED_DIR / f"{citekey}.txt"
            parsed.write_text(body, encoding="utf-8")
            con.execute(
                "INSERT INTO items (citekey, title, parsed_path, status, last_synced) "
                "VALUES (?, ?, ?, 'parsed', '2026-01-01')",
                (citekey, title, str(parsed)),
            )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def grounded(draft):
    """A dossier that cites one paper, rejected another, and logged the
    query that found them -- the state every drift finding is computed
    against."""
    dossier.init(draft, "survey")
    target = dossier.dossier_dir(draft)
    (target / "evidence.md").write_text(
        "# Kept evidence\n\n## `kept_paper_2024`\n\nWhy it was kept.\n"
    )
    (target / "rejected.md").write_text(
        "# Rejected candidates\n\n| citekey | query that surfaced it | why rejected |\n"
        "|---|---|---|\n| `turned_down_2023` | digital twin | off topic |\n"
    )
    (target / "sections.md").write_text(
        "# Sections and their citekeys\n\n| section | citekeys |\n|---|---|\n"
        "| 1. First | `kept_paper_2024` |\n"
    )
    dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 2400)
    return target


class TestRecordedQueries:
    def test_reads_the_queries_out_of_retrieval_md(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "digital twin", 15, 15, 100)
        dossier.log_retrieval(draft, "evidence", "co-simulation", 2, 2, 100)
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == [
            "digital twin", "co-simulation",
        ]

    def test_a_repeated_query_is_reported_once_in_first_seen_order(self, draft):
        dossier.init(draft, "survey")
        for query in ("twin", "shadow", "twin"):
            dossier.log_retrieval(draft, "search", query, 5, 5, 100)
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == ["twin", "shadow"]

    def test_an_escaped_pipe_is_restored(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "search", "twin | shadow", 5, 5, 100)
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == ["twin | shadow"]

    def test_a_hand_edited_row_that_does_not_parse_is_skipped(self, draft):
        dossier.init(draft, "survey")
        path = dossier.dossier_dir(draft) / "retrieval.md"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("| 2026-01-01 | search | only three cells |\n")
        dossier.log_retrieval(draft, "search", "real query", 5, 5, 100)
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == ["real query"]

    def test_an_empty_query_cell_is_not_a_query(self, draft):
        dossier.init(draft, "survey")
        path = dossier.dossier_dir(draft) / "retrieval.md"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("| 2026-01-01 | search |  | 5 | 5 | 100 |\n")
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == []

    def test_no_retrieval_file_means_no_queries(self, draft):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "retrieval.md").unlink()
        assert dossier.recorded_queries(dossier.dossier_dir(draft)) == []


class TestSectionCitekeys:
    def test_maps_a_citekey_to_the_sections_citing_it(self, grounded):
        (grounded / "sections.md").write_text(
            "# Sections and their citekeys\n\n| section | citekeys |\n|---|---|\n"
            "| 1. First | `a_one_2024`, `b_two_2024` |\n"
            "| 2. Second | `b_two_2024` |\n"
        )
        assert dossier.section_citekeys(grounded) == {
            "a_one_2024": ["1. First"],
            "b_two_2024": ["1. First", "2. Second"],
        }

    def test_a_missing_sections_file_maps_nothing(self, draft):
        assert dossier.section_citekeys(dossier.dossier_dir(draft)) == {}

    def test_a_row_with_no_citekeys_contributes_nothing(self, grounded):
        (grounded / "sections.md").write_text(
            "# Sections\n\n| section | citekeys |\n|---|---|\n| 1. First | none yet |\n"
        )
        assert dossier.section_citekeys(grounded) == {}


class TestDrift:
    def test_a_cited_key_that_left_the_ledger_is_reported_with_its_sections(self, grounded):
        _seed_corpus([("other_paper_2025", "Other", "unrelated text")])
        report = dossier.drift(grounded)
        assert report.missing == {"kept_paper_2024": ["1. First"]}

    def test_a_rejected_key_leaving_the_ledger_is_not_a_finding(self, grounded):
        _seed_corpus([("kept_paper_2024", "Kept", "text")])
        report = dossier.drift(grounded)
        assert report.missing == {}

    def test_a_new_paper_matching_a_recorded_query_is_a_candidate(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin architecture"),
            ("fresh_twin_2026", "A fresh twin paper", "digital twin co-simulation study"),
            ("unrelated_2026", "Baking bread", "sourdough starter hydration"),
        ])
        report = dossier.drift(grounded)
        assert [c.citekey for c in report.candidates] == ["fresh_twin_2026"]
        assert report.candidates[0].queries == ["digital twin"]

    def test_a_paper_already_rejected_is_never_offered_again(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "digital twin everywhere"),
        ])
        report = dossier.drift(grounded)
        assert [c.citekey for c in report.candidates] == []

    def test_the_candidate_carries_why_it_was_reachable(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        report = dossier.drift(grounded)
        assert report.candidates[0].title == "Fresh"

    def test_no_ledger_reports_unavailable_rather_than_raising(self, grounded):
        report = dossier.drift(grounded)
        assert report.corpus_available is False
        assert report.missing == {} and report.candidates == []

    def test_no_recorded_fingerprint_still_reports_missing_citations(self, grounded):
        """The fingerprint answers "did the corpus move"; a cited key
        vanishing is a finding whether or not the dossier recorded one."""
        (grounded / "scope.md").write_text("# Scope\n\n- genre: survey\n")
        _seed_corpus([("other_paper_2025", "Other", "text")])
        report = dossier.drift(grounded)
        assert report.recorded is None
        assert report.missing == {"kept_paper_2024": ["1. First"]}

    def test_an_empty_dossier_directory_is_reported_not_refused(self, draft):
        target = dossier.dossier_dir(draft)
        target.mkdir(parents=True)
        _seed_corpus([("any_paper_2025", "Any", "text")])
        report = dossier.drift(target)
        assert report.missing == {} and report.candidates == []

    def test_a_dossier_whose_draft_is_gone_is_still_reported(self, grounded, draft):
        draft.unlink()
        _seed_corpus([("kept_paper_2024", "Kept", "text")])
        report = dossier.drift(grounded)
        assert report.draft is None


class TestEphemeralIndex:
    """The drift scan must leave the corpus layer exactly as it found it.

    `src.retrieval.search()` cannot be called here: it goes through
    `ledger.connect()`, which creates `content/`, executes the schema and
    runs migrations, and through `_load_index`, which rewrites
    `retrieval_index.json` whenever a fingerprint moved -- both of which a
    read-only report must not do.
    """

    def test_no_retrieval_index_is_written(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        dossier.drift(grounded)
        assert not config.RETRIEVAL_INDEX_PATH.exists()

    def test_an_existing_retrieval_index_is_left_byte_for_byte(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        config.RETRIEVAL_INDEX_PATH.write_text('{"version": 1, "items": {}}')
        before = config.RETRIEVAL_INDEX_PATH.read_bytes()
        dossier.drift(grounded)
        assert config.RETRIEVAL_INDEX_PATH.read_bytes() == before

    def test_a_warm_cache_entry_is_reused_rather_than_re_tokenized(self, grounded):
        """Seeding the on-disk cache with a *wrong* term count for a paper
        proves the cache was read: the match can only come from the cache,
        since the parsed text says something else entirely."""
        _seed_corpus([("cached_paper_2026", "Cached", "sourdough starter")])
        from src import retrieval

        con = __import__("sqlite3").connect(config.LEDGER_PATH)
        con.row_factory = __import__("sqlite3").Row
        (row,) = con.execute("SELECT * FROM items").fetchall()
        fingerprint = retrieval._fingerprint(row)
        con.close()
        config.RETRIEVAL_INDEX_PATH.write_text(__import__("json").dumps({
            "version": 1,
            "items": {"cached_paper_2026": {
                "fingerprint": fingerprint,
                "length": 2,
                "term_freqs": {"digital": 1, "twin": 1},
            }},
        }))
        report = dossier.drift(grounded)
        assert [c.citekey for c in report.candidates] == ["cached_paper_2026"]

    def test_a_stale_cache_entry_is_re_tokenized_in_memory(self, grounded):
        _seed_corpus([("fresh_twin_2026", "Fresh", "digital twin")])
        config.RETRIEVAL_INDEX_PATH.write_text(__import__("json").dumps({
            "version": 1,
            "items": {"fresh_twin_2026": {
                "fingerprint": ["stale"],
                "length": 2,
                "term_freqs": {"sourdough": 1},
            }},
        }))
        report = dossier.drift(grounded)
        assert [c.citekey for c in report.candidates] == ["fresh_twin_2026"]

    def test_the_ledger_is_never_created_by_a_scan(self, grounded):
        dossier.drift(grounded)
        assert not config.LEDGER_PATH.exists()


class TestDriftAll:
    def test_reports_every_dossier(self, isolated_config):
        for name in ("alpha", "beta"):
            path = config.DRAFTS_DIR / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# x\n")
            dossier.init(path, "survey")
        _seed_corpus([("any_paper_2025", "Any", "text")])
        assert [r.name for r in dossier.drift_all()] == ["alpha", "beta"]

    def test_no_dossiers_is_an_empty_report_not_an_error(self, isolated_config):
        assert dossier.drift_all() == []

    def test_the_ledger_is_read_once_for_the_whole_sweep(self, isolated_config, monkeypatch):
        """Per-dossier ledger reads made `--all` cost O(dossiers) full
        table scans plus one corpus tokenization each."""
        for name in ("alpha", "beta", "gamma"):
            path = config.DRAFTS_DIR / f"{name}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# x\n")
            dossier.init(path, "survey")
            dossier.log_retrieval(path, "search", "digital twin", 5, 5, 100)
        _seed_corpus([("fresh_twin_2026", "Fresh", "digital twin")])

        calls = []
        real = dossier._corpus_rows
        monkeypatch.setattr(dossier, "_corpus_rows", lambda: calls.append(1) or real())
        dossier.drift_all()
        assert len(calls) == 1


class TestStatusAllCLI:
    def test_exits_zero_and_names_the_findings(self, grounded, capsys):
        _seed_corpus([
            ("other_paper_2025", "Other", "text"),
            ("fresh_twin_2026", "A fresh twin", "digital twin co-simulation"),
        ])
        assert dossier.main(["status", "--all"]) == 0
        out = capsys.readouterr().out
        assert "kept_paper_2024" in out          # cited, now gone
        assert "fresh_twin_2026" in out          # new, matches a logged query
        assert "1. First" in out                 # the section to edit

    def test_exits_zero_with_no_dossiers_at_all(self, isolated_config, capsys):
        assert dossier.main(["status", "--all"]) == 0
        assert "No dossiers" in capsys.readouterr().out

    def test_exits_zero_with_no_ledger(self, grounded, capsys):
        assert dossier.main(["status", "--all"]) == 0
        assert "unavailable" in capsys.readouterr().out

    def test_an_uncheckable_dossier_is_not_reported_as_current(self, grounded, capsys):
        """No findings is not the same as nothing to find. Calling an
        unchecked dossier current is the one way this could mislead."""
        assert dossier.main(["status", "--all"]) == 0
        out = capsys.readouterr().out
        assert "could not be checked" in out
        assert "drift is unknown" in out
        assert "Every dossier is current" not in out

    def test_a_mixed_sweep_reports_both_the_drift_and_the_unknown(self, grounded, capsys):
        """One dossier under a readable ledger, one that predates it --
        the summary has to carry both facts, not just the louder one."""
        stray = config.DOSSIERS_DIR / "orphan"
        stray.mkdir(parents=True)
        (stray / "scope.md").write_text("# Scope\n")
        _seed_corpus([("other_paper_2025", "Other", "text")])
        assert dossier.main(["status", "--all"]) == 0
        out = capsys.readouterr().out
        assert "have drifted" in out
        assert "could not be checked" not in out

    def test_a_clean_dossier_says_so(self, grounded, capsys):
        _seed_corpus([("kept_paper_2024", "Kept", "sourdough bread")])
        assert dossier.main(["status", "--all"]) == 0
        assert "no drift" in capsys.readouterr().out.lower()

    def test_json_is_machine_readable_for_the_reviser(self, grounded, capsys):
        _seed_corpus([
            ("other_paper_2025", "Other", "text"),
            ("fresh_twin_2026", "A fresh twin", "digital twin co-simulation"),
        ])
        assert dossier.main(["status", "--all", "--json"]) == 0
        payload = __import__("json").loads(capsys.readouterr().out)
        (entry,) = payload["dossiers"]
        assert entry["missing"] == {"kept_paper_2024": ["1. First"]}
        assert entry["candidates"][0]["citekey"] == "fresh_twin_2026"
        assert entry["candidates"][0]["queries"] == ["digital twin"]

    def test_json_for_one_draft_has_the_same_shape(self, grounded, draft, capsys):
        _seed_corpus([("fresh_twin_2026", "Fresh", "digital twin")])
        assert dossier.main(["status", str(draft), "--json"]) == 0
        payload = __import__("json").loads(capsys.readouterr().out)
        assert len(payload["dossiers"]) == 1

    def test_a_draft_and_all_together_is_refused(self, draft, capsys):
        assert dossier.main(["status", str(draft), "--all"]) == 2
        assert "not both" in capsys.readouterr().err

    def test_neither_a_draft_nor_all_is_refused(self, isolated_config, capsys):
        assert dossier.main(["status"]) == 2
        assert "--all" in capsys.readouterr().err

    def test_a_dossier_whose_draft_is_gone_is_flagged(self, grounded, draft, capsys):
        draft.unlink()
        _seed_corpus([("kept_paper_2024", "Kept", "text")])
        dossier.main(["status", "--all"])
        assert "draft missing" in capsys.readouterr().out

    def test_the_candidate_list_is_capped_with_a_visible_remainder(self, grounded, capsys):
        _seed_corpus(
            [("kept_paper_2024", "Kept", "digital twin")]
            + [(f"fresh_{n}_2026", f"Fresh {n}", "digital twin study") for n in range(12)]
        )
        dossier.main(["status", "--all"])
        assert "more" in capsys.readouterr().out

    def test_a_missing_citekey_list_is_capped_too(self, grounded, capsys):
        (grounded / "sections.md").write_text(
            "# Sections\n\n| section | citekeys |\n|---|---|\n"
            + "".join(f"| S{n} | `gone_{n}_2024` |\n" for n in range(12))
        )
        _seed_corpus([("survivor_2026", "Survivor", "sourdough")])
        dossier.main(["status", "--all"])
        out = capsys.readouterr().out
        # 12 from sections.md, plus `kept_paper_2024` from evidence.md --
        # a citekey counts as cited from either file.
        assert "13 cited citekey(s) no longer in the ledger" in out
        assert "... and 3 more" in out

    def test_missing_citations_are_reported_without_any_candidates(self, grounded, capsys):
        """The two findings are independent: a draft can cite a paper that
        left without the corpus having gained anything it would want."""
        _seed_corpus([("survivor_2026", "Survivor", "sourdough bread")])
        dossier.main(["status", "--all"])
        out = capsys.readouterr().out
        assert "no longer in the ledger" in out
        assert "new candidate(s)" not in out


class TestDriftEdges:
    def test_a_sections_row_with_the_wrong_cell_count_is_skipped(self, grounded):
        (grounded / "sections.md").write_text(
            "# Sections\n\n| section | citekeys |\n|---|---|\n"
            "| 1. First | `kept_paper_2024` | stray fourth cell |\n"
            "| 2. Second | `also_kept_2024` |\n"
        )
        assert dossier.section_citekeys(grounded) == {"also_kept_2024": ["2. Second"]}

    def test_a_query_of_nothing_but_stopwords_ranks_nothing(self, grounded):
        """`_tokenize` drops stopwords, so "the and of" reduces to no terms
        at all -- scoring it would rank the whole corpus by an empty query."""
        (grounded / "retrieval.md").write_text(
            "# Retrieval calls\n\n| date | mode | query | asked | results | chars |\n"
            "|---|---|---|---|---|---|\n| 2026-01-01 | search | the and of | 5 | 5 | 100 |\n"
        )
        _seed_corpus([("kept_paper_2024", "Kept", "digital twin")])
        assert dossier.drift(grounded).candidates == []

    def test_a_dossier_outside_the_dossiers_dir_falls_back_to_its_name(self, tmp_path):
        assert dossier.dossier_name(tmp_path / "stray-dossier") == "stray-dossier"


class TestRejectedReasons:
    def test_maps_a_citekey_to_why_it_was_turned_down(self, grounded):
        assert dossier.rejected_reasons(grounded) == {"turned_down_2023": "off topic"}

    def test_a_row_with_the_wrong_cell_count_is_skipped(self, grounded):
        (grounded / "rejected.md").write_text(
            "# Rejected\n\n| citekey | query | why |\n|---|---|---|\n"
            "| `broken_2023` | q |\n| `good_2023` | q | a real reason |\n"
        )
        assert dossier.rejected_reasons(grounded) == {"good_2023": "a real reason"}

    def test_a_row_naming_no_citekey_contributes_nothing(self, grounded):
        (grounded / "rejected.md").write_text(
            "# Rejected\n\n| citekey | query | why |\n|---|---|---|\n"
            "| none yet | q | r |\n"
        )
        assert dossier.rejected_reasons(grounded) == {}

    def test_a_missing_file_maps_nothing(self, draft):
        assert dossier.rejected_reasons(dossier.dossier_dir(draft)) == {}


class TestReconsider:
    """A paper this draft already read and declined, which its queries
    still reach. Not drift -- it was declined against a corpus that
    contained it -- but the reason is what a re-grounding pass needs in
    order to decide whether the decision still holds."""

    def test_a_still_matching_rejection_is_offered_back_with_its_reason(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "digital twin everywhere"),
        ])
        (entry,) = dossier.drift(grounded).reconsider
        assert (entry.citekey, entry.reason) == ("turned_down_2023", "off topic")
        assert entry.queries == ["digital twin"]

    def test_a_rejection_the_queries_no_longer_reach_is_not_offered(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "sourdough starter hydration"),
        ])
        assert dossier.drift(grounded).reconsider == []

    def test_it_never_duplicates_a_candidate(self, grounded):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        report = dossier.drift(grounded)
        assert [c.citekey for c in report.candidates] == ["fresh_twin_2026"]
        assert [r.citekey for r in report.reconsider] == ["turned_down_2023"]

    def test_a_cited_paper_is_never_offered_for_reconsideration(self, grounded):
        (grounded / "rejected.md").write_text(
            "# Rejected\n\n| citekey | query | why |\n|---|---|---|\n"
            "| `kept_paper_2024` | digital twin | a stale row, since kept |\n"
        )
        _seed_corpus([("kept_paper_2024", "Kept", "digital twin")])
        assert dossier.drift(grounded).reconsider == []

    def test_it_does_not_by_itself_make_a_dossier_drifted(self, grounded, capsys):
        """A rejection that still matches is true on every sweep forever.
        Counting it as drift would mark every dossier that ever declined a
        paper permanently stale, which is the signal this command exists
        to give."""
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "digital twin"),
        ])
        report = dossier.drift(grounded)
        assert report.reconsider and report.clean
        assert dossier.main(["status", "--all"]) == 0
        out = capsys.readouterr().out
        assert "no drift" in out
        assert "turned_down_2023" not in out

    def test_it_is_printed_once_the_dossier_is_already_drifting(self, grounded, capsys):
        _seed_corpus([
            ("turned_down_2023", "Turned down", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        dossier.main(["status", "--all"])
        out = capsys.readouterr().out
        assert "turned_down_2023" in out
        assert "off topic" in out

    def test_json_always_carries_it_for_the_reviser(self, grounded, capsys):
        _seed_corpus([
            ("kept_paper_2024", "Kept", "digital twin"),
            ("turned_down_2023", "Turned down", "digital twin"),
        ])
        dossier.main(["status", "--all", "--json"])
        (entry,) = __import__("json").loads(capsys.readouterr().out)["dossiers"]
        assert entry["reconsider"] == [{
            "citekey": "turned_down_2023",
            "title": "Turned down",
            "queries": ["digital twin"],
            "reason": "off topic",
        }]

    def test_the_reconsider_list_is_capped_with_a_visible_remainder(self, grounded, capsys):
        (grounded / "rejected.md").write_text(
            "# Rejected\n\n| citekey | query | why |\n|---|---|---|\n"
            + "".join(f"| `old_{n}_2023` | digital twin | thin |\n" for n in range(12))
        )
        _seed_corpus(
            [("fresh_twin_2026", "Fresh", "digital twin")]
            + [(f"old_{n}_2023", f"Old {n}", "digital twin study") for n in range(12)]
        )
        dossier.main(["status", "--all"])
        out = capsys.readouterr().out
        assert "12 previously rejected paper(s)" in out
        assert "... and 2 more" in out

    def test_the_trailer_does_not_contradict_the_reconsider_list(self, grounded, capsys):
        """"nothing here was turned down before" is false the moment a
        reconsider list is on screen."""
        _seed_corpus([
            ("turned_down_2023", "Turned down", "digital twin"),
            ("fresh_twin_2026", "Fresh", "digital twin"),
        ])
        dossier.main(["status", "--all"])
        out = capsys.readouterr().out
        assert "turned down before" not in out
        assert "the reconsider list is" in out


# ---------------------------------------------------------------------
# The reporting CLI's own output
#
# `status`, `list`, `sections` and `restore` are read by a human deciding
# what to do next, and most of what they print is a branch: a dossier
# whose draft is gone, a file that was never created, a corpus that moved
# by more than fits on a screen. Those branches were reachable only
# through `main()`, so they went unexercised while the functions beneath
# them were fully covered -- which is the shape of gap that lets a report
# say something untrue without any test noticing.
# ---------------------------------------------------------------------


class TestPathsOutsideTheContentTree:
    def test_draft_name_of_a_draft_outside_drafts_falls_back_to_its_stem(
            self, isolated_config, tmp_path):
        stray = tmp_path / "elsewhere.md"
        assert dossier.draft_name(stray) == "elsewhere"

    def test_find_draft_of_a_dossier_outside_dossiers_finds_nothing(
            self, isolated_config, tmp_path):
        assert dossier.find_draft(tmp_path / "not-a-dossier") is None


class TestUnreadableLedger:
    def test_a_ledger_that_cannot_be_opened_reports_unavailable(
            self, isolated_config, monkeypatch):
        """`exists()` is not `openable()`. A directory where the ledger
        should be is the cheap way to prove the difference, and the scan
        has to survive it rather than raise into a report."""
        fake = config.CONTENT_DIR / "ledger.sqlite"
        fake.mkdir(parents=True)
        monkeypatch.setattr(config, "LEDGER_PATH", fake)
        assert dossier._corpus_rows() is None
        assert dossier.known_citekeys() is None


class TestInitCLI:
    def test_a_second_init_says_the_dossier_is_already_complete(self, draft, capsys):
        dossier.init(draft, "survey")
        assert dossier.main(["init", str(draft), "--genre", "survey"]) == 0
        assert "already complete" in capsys.readouterr().out

    def test_with_a_ledger_present_it_does_not_warn_about_drift_checks(self, draft, capsys):
        _seed_ledger(["talasila_composable_2025"])
        assert dossier.main(["init", str(draft), "--genre", "survey"]) == 0
        out = capsys.readouterr().out
        assert "created scope.md" in out
        assert "No ledger" not in out


class TestStatusCLIOutput:
    def test_a_dossier_that_outlived_its_draft_says_so(self, draft, capsys):
        dossier.init(draft, "survey")
        draft.unlink()
        assert dossier.main(["status", str(dossier.dossier_dir(draft))]) == 0
        assert "MISSING -- the dossier outlived its draft" in capsys.readouterr().out

    def test_a_file_that_was_never_created_reads_absent(self, draft, capsys):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "steering.md").unlink()
        dossier.main(["status", str(draft)])
        assert "steering.md   absent" in capsys.readouterr().out.replace("  ", "  ")

    def test_the_kept_and_rejected_tally_appears_beside_the_retrieval_cost(
            self, grounded, draft, capsys):
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "1 call(s) returned" in out
        assert "1 kept, 1 rejected" in out

    def test_a_dossier_with_no_fingerprint_reports_the_current_corpus_instead(
            self, draft, capsys):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "scope.md").write_text("# Scope\n\n- genre: survey\n")
        _seed_ledger(["a_paper_2024", "b_paper_2024"])
        assert dossier.main(["status", str(draft)]) == 0
        out = capsys.readouterr().out
        assert "scope.md records no corpus fingerprint" in out
        assert "now: 2 citekeys" in out

    def test_an_unmoved_corpus_says_the_evidence_is_current(self, draft, capsys):
        _seed_ledger(["a_paper_2024"])
        dossier.init(draft, "survey")
        assert dossier.main(["status", str(draft)]) == 0
        assert "unchanged -- the dossier's evidence is current" in capsys.readouterr().out

    def test_a_moved_corpus_names_what_was_never_considered(self, draft, capsys):
        _seed_ledger(["a_paper_2024"])
        dossier.init(draft, "survey")
        _seed_ledger(["b_paper_2025", "c_paper_2025"])
        assert dossier.main(["status", str(draft)]) == 0
        out = capsys.readouterr().out
        assert "CHANGED (+2 citekeys)" in out
        assert "b_paper_2025" in out
        assert "Drift is not itself a reason to redraft" in out

    def test_the_never_considered_list_is_capped_with_a_visible_remainder(
            self, draft, capsys):
        _seed_ledger(["a_paper_2024"])
        dossier.init(draft, "survey")
        _seed_ledger([f"new_{n}_paper_2025" for n in range(12)])
        dossier.main(["status", str(draft)])
        assert "... and 3 more" in capsys.readouterr().out


class TestSectionsCLIOutput:
    def test_a_draft_with_no_headings_says_so_and_exits_zero(self, isolated_config, capsys):
        config.DRAFTS_DIR.mkdir(parents=True)
        flat = config.DRAFTS_DIR / "prose.md"
        flat.write_text("just prose\nover two lines\n")
        assert dossier.main(["sections", str(flat)]) == 0
        assert "No headings in" in capsys.readouterr().out


class TestListCLIOutput:
    def test_it_names_every_dossier_and_flags_an_orphan(self, draft, capsys):
        dossier.init(draft, "survey")
        orphan = config.DRAFTS_DIR / "gone.md"
        orphan.write_text("# x\n")
        dossier.init(orphan, "survey")
        orphan.unlink()
        assert dossier.main(["list"]) == 0
        out = capsys.readouterr().out
        assert "dt-for-engineers/survey" in out
        assert "gone   (draft missing)" in out
        assert "2 dossier(s) under" in out


class TestRestoreCLIOutput:
    def test_an_archive_that_is_not_a_tarball_is_refused_without_a_traceback(
            self, isolated_config, tmp_path, capsys):
        junk = tmp_path / "not-really.tar.gz"
        junk.write_bytes(b"this is not a gzip stream")
        assert dossier.main(["restore", str(junk)]) == 1
        assert "[error]" in capsys.readouterr().err

    def test_an_unsafe_member_is_refused_without_a_traceback(
            self, isolated_config, tmp_path, capsys):
        archive = tmp_path / "hostile.tar.gz"
        payload = tmp_path / "payload.md"
        payload.write_text("x")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="../escaped.md")
        assert dossier.main(["restore", str(archive)]) == 1
        assert "escapes the extraction directory" in capsys.readouterr().err

    def test_a_directory_member_is_carried_but_not_counted_as_a_file(
            self, isolated_config, tmp_path, capsys):
        """`export` only ever adds files, but a hand-rolled or
        hand-edited bundle can carry directory entries, and the plan
        counts files -- an empty directory is not something to warn about
        overwriting."""
        archive = tmp_path / "with-dir.tar.gz"
        staging = tmp_path / "drafts"
        staging.mkdir()
        (staging / "one.md").write_text("# one\n")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(staging, arcname="drafts")
        assert dossier.main(["restore", str(archive)]) == 0
        out = capsys.readouterr().out
        assert "1 new file(s)" in out
        assert "Dry run" in out

    def test_the_overwrite_list_is_capped_with_a_visible_remainder(
            self, isolated_config, tmp_path, capsys):
        config.DRAFTS_DIR.mkdir(parents=True)
        for n in range(12):
            (config.DRAFTS_DIR / f"draft{n}.md").write_text(f"# {n}\n")
        archive = tmp_path / "all.tar.gz"
        dossier.export([], archive)
        assert dossier.main(["restore", str(archive)]) == 0
        out = capsys.readouterr().out
        assert "12 existing file(s) would be OVERWRITTEN" in out
        assert "... and 2 more" in out

    def test_a_corpus_that_only_shrank_reports_drift_with_nothing_to_look_at(
            self, draft, capsys):
        """Drift is a digest comparison, so losing a paper moves it just
        as gaining one does -- but there is then nothing "never
        considered" to list, and the report must not print an empty
        heading."""
        _seed_ledger(["a_paper_2024", "b_paper_2024"])
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "evidence.md").write_text(
            "# Kept evidence\n\n## `a_paper_2024`\n\nkept.\n\n## `b_paper_2024`\n\nkept.\n"
        )
        from src import ledger
        con = ledger.connect()
        con.execute("DELETE FROM items WHERE citekey = 'b_paper_2024'")
        con.commit()
        con.close()

        assert dossier.main(["status", str(draft)]) == 0
        out = capsys.readouterr().out
        assert "CHANGED (-1 citekeys)" in out
        assert "appear nowhere in this dossier" not in out

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
        dossier.log_retrieval(draft, "triage", "digital twin", 15, 15, 2400)
        dossier.log_retrieval(draft, "evidence", "digital twin", 1, 3, 2100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (2, 4500)

    def test_creates_the_file_for_a_dossier_that_predates_it(self, draft):
        dossier.init(draft, "survey")
        (dossier.dossier_dir(draft) / "retrieval.md").unlink()
        dossier.log_retrieval(draft, "triage", "q", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_creates_the_dossier_when_a_skill_logs_before_init(self, draft):
        dossier.log_retrieval(draft, "triage", "q", 15, 15, 100)
        assert (dossier.dossier_dir(draft) / "retrieval.md").is_file()

    def test_logging_before_init_leaves_init_free_to_write_the_rest(self, draft):
        dossier.log_retrieval(draft, "triage", "q", 15, 15, 100)
        written = {path.name for path in dossier.init(draft, "survey")}
        assert "retrieval.md" not in written
        assert "scope.md" in written
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_a_pipe_in_the_query_does_not_break_the_row(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "triage", "twin | shadow", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_a_hand_edited_row_is_skipped_rather_than_fatal(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "triage", "q", 15, 15, 100)
        path = dossier.dossier_dir(draft) / "retrieval.md"
        path.write_text(path.read_text() + "| 2026-08-06 | triage | q | 15 | 15 | lots |\n")
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_no_log_means_no_cost(self, draft):
        dossier.init(draft, "survey")
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (0, 0)

    def test_status_reports_the_measured_cost(self, draft, capsys):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "triage", "digital twin", 15, 15, 2400)
        dossier.main(["status", str(draft)])
        out = capsys.readouterr().out
        assert "1 call(s) returned 2,400 characters" in out

    def test_status_says_nothing_about_retrieval_when_nothing_was_logged(self, draft, capsys):
        dossier.init(draft, "survey")
        dossier.main(["status", str(draft)])
        assert "call(s) returned" not in capsys.readouterr().out

    def test_a_newline_in_the_query_does_not_split_the_row(self, draft):
        """`retrieval_cost` reads rows positionally, so a query carrying a
        newline would not error -- it would quietly become two rows, one
        of which parses and one of which doesn't."""
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "triage", "digital twin\narchitecture", 15, 15, 100)
        text = (dossier.dossier_dir(draft) / "retrieval.md").read_text()
        assert "digital twin architecture" in text
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

    def test_tabs_and_carriage_returns_are_flattened_too(self, draft):
        dossier.init(draft, "survey")
        dossier.log_retrieval(draft, "triage", "twin\tshadow\r\nmodel", 15, 15, 100)
        assert dossier.retrieval_cost(dossier.dossier_dir(draft)) == (1, 100)

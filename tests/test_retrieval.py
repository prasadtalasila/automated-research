"""src/retrieval.py: BM25-ranked search over a cached term-frequency
index, the retrieval contract genre skills call before the
embeddings-based upgrade (src/enrich/embed_index.py)."""

import json
from pathlib import Path

from src import config, ledger, retrieval

from tests.conftest import make_reference


class TestTokenize:
    def test_lowercases_and_splits_alnum(self):
        assert retrieval._tokenize("Digital Twins ARE great") == ["digital", "twins", "great"]

    def test_drops_stopwords_and_short_words(self):
        assert retrieval._tokenize("a of on in for and to with is") == []

    def test_keeps_numbers(self):
        assert retrieval._tokenize("ISO 9001 standard") == ["iso", "9001", "standard"]


class TestSnippet:
    def test_centers_window_on_first_matching_term(self):
        text = "x" * 100 + " digital twin simulation " + "y" * 100
        snippet = retrieval._snippet(text, {"digital"}, window=20)
        assert "digital" in snippet

    def test_falls_back_to_start_of_text_when_no_term_found(self):
        text = "no matching terms here at all"
        snippet = retrieval._snippet(text, {"zzz"}, window=10)
        assert snippet == "no matchin"


class TestSearch:
    def test_empty_query_returns_empty(self, ledger_con):
        assert retrieval.search("") == []

    def test_ranks_by_term_overlap_descending(self, ledger_con):
        ledger.upsert_reference(
            ledger_con, make_reference(citekey="a2024", title="Digital Twin Digital Twin Digital")
        )
        ledger.upsert_reference(
            ledger_con, make_reference(citekey="b2024", title="Digital Twin Overview")
        )
        ledger.upsert_reference(
            ledger_con, make_reference(citekey="c2024", title="Unrelated Paper About Cats")
        )

        results = retrieval.search("digital twin", k=5)
        assert [r.citekey for r in results] == ["a2024", "b2024"]
        assert results[0].score > results[1].score

    def test_top_k_truncation(self, ledger_con):
        for i in range(5):
            ledger.upsert_reference(
                ledger_con, make_reference(citekey=f"item{i}_2024", title="Digital Twin Paper")
            )
        results = retrieval.search("digital twin", k=2)
        assert len(results) == 2

    def test_uses_parsed_text_when_available(self, ledger_con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("this document mentions blockchain repeatedly blockchain blockchain")
        ref = make_reference(citekey="a2024", title="Unrelated Title")
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, "a2024", parsed)

        results = retrieval.search("blockchain")
        assert len(results) == 1
        assert results[0].citekey == "a2024"

    def test_missing_parsed_file_does_not_crash(self, ledger_con):
        ref = make_reference(citekey="a2024", title="Some Title About Robotics")
        ledger.upsert_reference(ledger_con, ref)
        ledger.mark_parsed(ledger_con, "a2024", "content/parsed/does-not-exist.txt")

        results = retrieval.search("robotics")
        assert len(results) == 1
        assert results[0].citekey == "a2024"

    def test_no_matching_terms_excludes_item(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Completely unrelated"))
        assert retrieval.search("nonexistentterm12345") == []

    def test_length_normalization_prevents_long_docs_from_winning_structurally(
        self, ledger_con, tmp_path
    ):
        # Regression for the raw-term-frequency ranker's core bug: with no
        # length normalization, a long document only needed to accumulate
        # *more raw hits* than a short one to outrank it, regardless of how
        # small a fraction of the long document those hits represent. Here
        # the long document mentions the query term twice (a higher raw
        # count than the short document's one mention) but is buried in
        # ~1200 words of unrelated filler -- the old scorer would have
        # ranked it first on raw count alone; BM25's length normalization
        # must rank the short, tightly-on-topic document first instead.
        short_parsed = tmp_path / "short2024.txt"
        short_parsed.write_text("Blockchain is the entire subject of this short paper.")
        long_parsed = tmp_path / "long2024.txt"
        long_parsed.write_text(
            "irrelevant filler word " * 400 + "blockchain mentioned twice blockchain here"
        )

        ledger.upsert_reference(ledger_con, make_reference(citekey="short2024", title="Short Paper"))
        ledger.mark_parsed(ledger_con, "short2024", short_parsed)
        ledger.upsert_reference(ledger_con, make_reference(citekey="long2024", title="Long Paper"))
        ledger.mark_parsed(ledger_con, "long2024", long_parsed)

        results = retrieval.search("blockchain")
        assert [r.citekey for r in results] == ["short2024", "long2024"]

    def test_score_is_a_float(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))
        results = retrieval.search("digital")
        assert isinstance(results[0].score, float)


class TestIndexCaching:
    """The scale fix: search() must not re-read and re-tokenize every
    document's parsed text from disk on every call -- only building a
    snippet for the returned top-k should touch a parsed file at all."""

    def test_cache_file_is_created_on_first_search(self, ledger_con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("digital twin content")
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))
        ledger.mark_parsed(ledger_con, "a2024", parsed)

        assert not config.RETRIEVAL_INDEX_PATH.exists()
        retrieval.search("digital")
        assert config.RETRIEVAL_INDEX_PATH.exists()

    def test_second_call_does_not_reread_a_doc_outside_the_results(
        self, ledger_con, tmp_path, monkeypatch
    ):
        winner_parsed = tmp_path / "winner2024.txt"
        winner_parsed.write_text("digital twin digital twin content")
        loser_parsed = tmp_path / "loser2024.txt"
        loser_parsed.write_text("nothing related to the query at all, just filler text")

        ledger.upsert_reference(ledger_con, make_reference(citekey="winner2024", title="Digital Twin"))
        ledger.mark_parsed(ledger_con, "winner2024", winner_parsed)
        ledger.upsert_reference(ledger_con, make_reference(citekey="loser2024", title="Unrelated"))
        ledger.mark_parsed(ledger_con, "loser2024", loser_parsed)

        retrieval.search("digital twin", k=1)  # builds the cache

        read_calls = []
        real_read_text = Path.read_text

        def spy_read_text(self, *a, **kw):
            read_calls.append(self)
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy_read_text)

        results = retrieval.search("digital twin", k=1)
        assert [r.citekey for r in results] == ["winner2024"]
        # loser2024 never makes the top-k, so ranking it must have come
        # from the cached index, not a fresh read+tokenize of its file.
        assert loser_parsed not in read_calls

    def test_changed_parsed_file_content_triggers_reindex(self, ledger_con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("nothing about the topic here")
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Some Title"))
        ledger.mark_parsed(ledger_con, "a2024", parsed)
        assert retrieval.search("blockchain") == []

        parsed.write_text("blockchain blockchain blockchain")
        results = retrieval.search("blockchain")
        assert [r.citekey for r in results] == ["a2024"]

    def test_corrupt_cache_file_is_rebuilt_not_fatal(self, ledger_con, tmp_path):
        config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.RETRIEVAL_INDEX_PATH.write_text("{not valid json")
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))

        results = retrieval.search("digital")
        assert [r.citekey for r in results] == ["a2024"]

    def test_stale_schema_version_cache_is_rebuilt_not_trusted(self, ledger_con, tmp_path):
        config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.RETRIEVAL_INDEX_PATH.write_text(json.dumps({
            "version": 0,
            "items": {"a2024": {"fingerprint": ["wrong"], "length": 1, "term_freqs": {}}},
        }))
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))

        results = retrieval.search("digital")
        assert [r.citekey for r in results] == ["a2024"]

    def test_cache_that_is_valid_json_but_wrong_top_level_shape_is_rebuilt(
        self, ledger_con, tmp_path
    ):
        # Regression (PR #6 review): _load_cache only checked data.get("version"),
        # so valid JSON that isn't a dict at all (a bare array here) would
        # crash on that .get() call instead of being treated as a cache miss.
        config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.RETRIEVAL_INDEX_PATH.write_text(json.dumps([1, 2, 3]))
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))

        results = retrieval.search("digital")
        assert [r.citekey for r in results] == ["a2024"]

    def test_cache_whose_items_value_is_the_wrong_shape_is_rebuilt(self, ledger_con):
        # Same class of bug one level deeper: "items" present but not a
        # dict (e.g. a list) -- cached.get(citekey) in _load_index() would
        # otherwise crash on it instead of treating it as a cache miss.
        config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.RETRIEVAL_INDEX_PATH.write_text(json.dumps({"version": 1, "items": ["not", "a", "dict"]}))
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))

        results = retrieval.search("digital")
        assert [r.citekey for r in results] == ["a2024"]

    def test_cache_entry_for_one_citekey_that_is_the_wrong_shape_is_rebuilt(self, ledger_con):
        # A single cached per-document entry that isn't a dict (rather
        # than the whole cache) must not crash cached_entry.get(...) in
        # _load_index() either.
        config.RETRIEVAL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.RETRIEVAL_INDEX_PATH.write_text(
            json.dumps({"version": 1, "items": {"a2024": "not-a-dict"}})
        )
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))

        results = retrieval.search("digital")
        assert [r.citekey for r in results] == ["a2024"]

    def test_removed_citekey_is_dropped_from_the_cache(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Digital Twin"))
        retrieval.search("digital")
        with open(config.RETRIEVAL_INDEX_PATH) as f:
            assert "a2024" in json.load(f)["items"]

        ledger_con.execute("DELETE FROM items WHERE citekey = ?", ("a2024",))
        ledger_con.commit()
        retrieval.search("digital")
        with open(config.RETRIEVAL_INDEX_PATH) as f:
            assert "a2024" not in json.load(f)["items"]


class TestWindows:
    def test_returns_nothing_when_no_term_appears(self):
        assert retrieval._windows("nothing relevant here", {"blockchain"}, 50, 3) == []

    def test_prefers_a_window_covering_more_distinct_terms(self):
        text = (
            "moisture " * 20
            + " ||| soil moisture sensor calibration ||| "
            + "moisture " * 20
        )
        (best,) = retrieval._windows(text, {"soil", "moisture", "sensor"}, 60, 1)
        assert "soil" in best and "sensor" in best

    def test_finds_a_passage_late_in_a_long_document(self):
        """The limitation `_snippet` has and this fixes: it anchors on the
        first occurrence of any term, so a document that says the word
        early and discusses it 40,000 characters later reports the early
        mention."""
        text = "twin " + "filler " * 6000 + "the pump is actuated by the twin controller"
        windows = retrieval._windows(text, {"pump", "actuated", "twin"}, 60, 1)
        assert "pump" in windows[0]

    def test_windows_do_not_overlap(self):
        text = "alpha " * 5 + "beta " * 200 + "alpha " * 5
        windows = retrieval._windows(text, {"alpha"}, 40, 2)
        assert len(windows) == 2
        assert windows[0] != windows[1]

    def test_returns_windows_in_document_order(self):
        text = "start marker one " + "x " * 300 + " end marker two"
        windows = retrieval._windows(text, {"marker", "start", "end"}, 40, 2)
        assert "start" in windows[0] and "end" in windows[1]

    def test_respects_the_requested_count(self):
        text = ("term " + "pad " * 40) * 10
        assert len(retrieval._windows(text, {"term"}, 30, 3)) == 3


class TestTriage:
    def test_returns_a_shorter_snippet_than_search(self, ledger_con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("digital twin " + "context " * 500)
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="A Twin"))
        ledger.mark_parsed(ledger_con, "a2024", parsed)

        (triaged,) = retrieval.triage("digital twin", k=5)
        (searched,) = retrieval.search("digital twin", k=5)
        assert len(triaged.snippet) < len(searched.snippet)
        assert len(triaged.snippet) <= retrieval.TRIAGE_CHARS

    def test_ranks_identically_to_search(self, ledger_con):
        for key, title in [("a2024", "Digital Twin Digital"), ("b2024", "Digital Overview")]:
            ledger.upsert_reference(ledger_con, make_reference(citekey=key, title=title))
        assert [r.citekey for r in retrieval.triage("digital twin", k=5)] == [
            r.citekey for r in retrieval.search("digital twin", k=5)
        ]


class TestEvidence:
    def _seed(self, con, tmp_path, text, citekey="a2024"):
        parsed = tmp_path / f"{citekey}.txt"
        parsed.write_text(text)
        ledger.upsert_reference(con, make_reference(citekey=citekey, title="A Paper"))
        ledger.mark_parsed(con, citekey, parsed)

    def test_returns_the_supporting_passages(self, ledger_con, tmp_path):
        self._seed(ledger_con, tmp_path,
                   "padding " * 100 + "simulation time must follow wall clock time" + " tail" * 50)
        passages = retrieval.evidence("a2024", "simulation wall clock", chars=80)
        assert any("wall clock" in p for p in passages)

    def test_reads_more_of_the_document_than_a_search_snippet(self, ledger_con, tmp_path):
        body = " ".join(f"clock segment {i} simulation" for i in range(200))
        self._seed(ledger_con, tmp_path, body)
        total = sum(len(p) for p in retrieval.evidence("a2024", "clock simulation", chars=300))
        assert total > 500

    def test_an_empty_query_returns_nothing(self, ledger_con, tmp_path):
        self._seed(ledger_con, tmp_path, "some text")
        assert retrieval.evidence("a2024", "the of and") == []

    def test_a_citekey_with_no_parsed_text_is_not_an_error(self, ledger_con):
        ledger.upsert_reference(ledger_con, make_reference(citekey="a2024", title="Robotics"))
        assert retrieval.evidence("a2024", "quantum entanglement") == []

    def test_the_title_alone_can_carry_a_match(self, ledger_con):
        ledger.upsert_reference(
            ledger_con, make_reference(citekey="a2024", title="Robotics And Control"))
        assert retrieval.evidence("a2024", "robotics", chars=40) != []

    def test_an_unknown_citekey_raises(self, ledger_con):
        import pytest

        with pytest.raises(KeyError, match="not in the ledger"):
            retrieval.evidence("nope_2024", "anything")


class TestCli:
    def _seed(self, con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("padding " * 50 + "digital twin architecture patterns catalog")
        ledger.upsert_reference(con, make_reference(citekey="a2024", title="Twin Patterns"))
        ledger.mark_parsed(con, "a2024", parsed)

    def test_triage_prints_candidates_and_the_reject_only_warning(
        self, ledger_con, tmp_path, capsys
    ):
        self._seed(ledger_con, tmp_path)
        assert retrieval.main(["triage", "digital twin architecture"]) == 0
        out = capsys.readouterr().out
        assert "a2024" in out
        assert "Do not cite from a triage snippet" in out
        assert "characters returned" in out

    def test_evidence_prints_passages(self, ledger_con, tmp_path, capsys):
        self._seed(ledger_con, tmp_path)
        assert retrieval.main(
            ["evidence", "architecture patterns", "--citekey", "a2024"]) == 0
        assert "patterns" in capsys.readouterr().out

    def test_evidence_on_an_unknown_citekey_exits_nonzero(self, ledger_con, tmp_path, capsys):
        self._seed(ledger_con, tmp_path)
        assert retrieval.main(["evidence", "x", "--citekey", "nope_2024"]) == 1
        assert "not in the ledger" in capsys.readouterr().err

    def test_no_ledger_exits_nonzero_with_the_fix(self, isolated_config, capsys):
        assert retrieval.main(["triage", "anything"]) == 1
        assert "src.sync" in capsys.readouterr().err

    def test_no_results_is_not_an_error(self, ledger_con, tmp_path, capsys):
        self._seed(ledger_con, tmp_path)
        assert retrieval.main(["triage", "quantum chromodynamics"]) == 0
        assert "No results." in capsys.readouterr().out

    def test_log_records_the_call_in_the_dossier(self, ledger_con, tmp_path, capsys):
        from src import dossier

        self._seed(ledger_con, tmp_path)
        draft = config.DRAFTS_DIR / "survey.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("# s\n")

        assert retrieval.main(
            ["triage", "digital twin architecture", "--log", str(draft)]) == 0
        calls, chars = dossier.retrieval_cost(dossier.dossier_dir(draft))
        assert calls == 1 and chars > 0
        assert "Logged to" in capsys.readouterr().out

    def test_a_draft_outside_drafts_reports_but_does_not_fail_the_search(
        self, ledger_con, tmp_path, capsys
    ):
        """A measurement is worth less than the retrieval it measures."""
        self._seed(ledger_con, tmp_path)
        stray = tmp_path / "stray.md"
        stray.write_text("# s\n")
        assert retrieval.main(["triage", "digital twin", "--log", str(stray)]) == 0
        captured = capsys.readouterr()
        assert "a2024" in captured.out
        assert "[not logged]" in captured.err


class TestTwoStageCost:
    """The claim `docs/RETRIEVAL.md` makes about when two-stage wins.

    An earlier version defaulted to 3 windows of 700 characters, which
    cost more than one-stage retrieval in every realistic scenario. These
    pin the defaults to the arithmetic the documentation states, so the
    claim and the code cannot drift apart silently.
    """

    def _one_stage(self, k=15):
        return k * 500

    def _two_stage(self, survivors, k=15):
        return (k * retrieval.TRIAGE_CHARS
                + survivors * retrieval.EVIDENCE_CHARS * retrieval.EVIDENCE_WINDOWS)

    def test_wins_when_triage_rejects_most_candidates(self):
        assert self._two_stage(survivors=3) < self._one_stage()

    def test_loses_when_almost_everything_survives_triage(self):
        """Documented, not a bug -- and the reason the skills say to
        reject hard at stage one."""
        assert self._two_stage(survivors=8) > self._one_stage()

    def test_a_triage_window_is_well_under_a_search_snippet(self):
        assert retrieval.TRIAGE_CHARS < 500 / 2

    def test_the_docs_quote_the_actual_defaults(self):
        """docs/CLI.md and docs/RETRIEVAL.md both spell these numbers out
        in prose, and prose does not fail a build when a constant moves.
        Review caught exactly this drift on the previous PR (CLI.md still
        said 700 after the default became 600), so the quoted values are
        pinned to the constants here rather than trusted."""
        cli = (config.REPO_ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")
        chars_row = next(line for line in cli.splitlines() if "`--chars N`" in line)
        assert f"{retrieval.TRIAGE_CHARS} / {retrieval.EVIDENCE_CHARS} / 500" in chars_row

        k_row = next(line for line in cli.splitlines() if "`--k N`" in line)
        assert "15 / 5" in k_row

        windows_row = next(line for line in cli.splitlines() if "`--windows N`" in line)
        assert f"| {retrieval.EVIDENCE_WINDOWS} |" in windows_row

        # The subcommand table states the same number in prose, and the
        # first version of this guard checked only the flag row -- review
        # caught the description still saying "the 3 passages".
        evidence_row = next(
            line for line in cli.splitlines() if '`evidence "<query>" --citekey KEY`' in line
        )
        assert f"{retrieval.EVIDENCE_WINDOWS} by default" in evidence_row

        retr = (config.REPO_ROOT / "docs" / "RETRIEVAL.md").read_text(encoding="utf-8")
        assert f"{retrieval.TRIAGE_CHARS} chars" in retr
        assert f"{retrieval.EVIDENCE_WINDOWS} x {retrieval.EVIDENCE_CHARS} chars" in retr

    def test_the_documented_break_even_table_is_arithmetically_right(self):
        """The RETRIEVAL.md table states four payload figures. They are the
        argument for the defaults, so they are checked rather than
        asserted -- the previous defaults (3 x 700) lost to one-stage in
        every row and the prose had not noticed."""
        retr = (config.REPO_ROOT / "docs" / "RETRIEVAL.md").read_text(encoding="utf-8")
        per_survivor = retrieval.EVIDENCE_CHARS * retrieval.EVIDENCE_WINDOWS
        triage = 15 * retrieval.TRIAGE_CHARS
        assert f"15 x 500 = **{15 * 500:,}**" in retr
        for survivors in (3, 5, 8):
            total = triage + survivors * per_survivor
            assert f"{triage:,} + {survivors} x {per_survivor:,} = **{total:,}**" in retr


class TestLogNeverFailsTheSearch:
    """docs/CLI.md states that a `--log` problem is reported and skipped,
    never fatal. `DossierError` covered "that path isn't a draft"; a
    filesystem failure was not covered and would have thrown away results
    the caller had already paid to compute."""

    def _seed(self, con, tmp_path):
        parsed = tmp_path / "a2024.txt"
        parsed.write_text("padding " * 50 + "digital twin architecture patterns")
        ledger.upsert_reference(con, make_reference(citekey="a2024", title="Twin Patterns"))
        ledger.mark_parsed(con, "a2024", parsed)

    def test_an_oserror_while_logging_is_reported_not_raised(
        self, ledger_con, tmp_path, capsys, monkeypatch
    ):
        from src import dossier

        self._seed(ledger_con, tmp_path)
        draft = config.DRAFTS_DIR / "survey.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("# s\n")

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(dossier, "log_retrieval", boom)

        assert retrieval.main(["triage", "digital twin", "--log", str(draft)]) == 0
        captured = capsys.readouterr()
        assert "a2024" in captured.out, "the retrieval results must still be printed"
        assert "[not logged]" in captured.err
        assert "No space left on device" in captured.err

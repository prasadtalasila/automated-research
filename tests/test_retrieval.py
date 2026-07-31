"""src/retrieval.py: BM25-ranked search over a cached term-frequency
index, the retrieval contract genre skills call before the
embeddings-based upgrade (src/heavy/embed_index.py)."""

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

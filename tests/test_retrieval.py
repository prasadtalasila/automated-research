"""src/retrieval.py: keyword-overlap search, the retrieval contract
genre skills call before the embeddings-based upgrade (src/heavy/embed_index.py)."""

from src import ledger, retrieval

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

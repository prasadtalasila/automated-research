"""src/heavy/corpus.py: unifies bib-sourced docs (real citekeys) and
source-pdfs docs (never citekey-shaped -- AGENTS.md's "doc:" namespace
invariant)."""

import json

import pytest

from src import ledger
from src.heavy import corpus

from tests.conftest import make_reference


class TestSafeFilename:
    def test_replaces_colon(self):
        assert corpus.safe_filename("doc:my-file") == "doc_my-file"

    def test_no_colon_unchanged(self):
        assert corpus.safe_filename("smith_2024") == "smith_2024"


class TestSourcePdfManifest:
    def test_missing_manifest_returns_empty(self, isolated_config):
        assert corpus._source_pdf_manifest() == {}

    def test_reads_manifest_keyed_by_file(self, isolated_config):
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True)
        isolated_config.SOURCE_PDFS_MANIFEST.write_text(json.dumps({
            "items": [
                {"file": "paper.pdf", "title": "A Great Paper"},
                {"title": "Missing file key, should be skipped"},
            ]
        }))
        manifest = corpus._source_pdf_manifest()
        assert manifest == {"paper.pdf": {"file": "paper.pdf", "title": "A Great Paper"}}


class TestBuildCorpus:
    def test_combines_ledger_and_source_pdfs(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="Bib Paper"))
        con.close()

        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True)
        (isolated_config.SOURCE_PDFS_DIR / "extra.pdf").write_bytes(b"%PDF-1.4")
        isolated_config.SOURCE_PDFS_MANIFEST.write_text(json.dumps({
            "items": [{"file": "extra.pdf", "title": "Extra Source PDF"}]
        }))

        docs, _ = corpus.build_corpus()
        by_id = {d.doc_id: d for d in docs}

        assert by_id["smith2024"].source == "bib"
        assert by_id["smith2024"].citekey == "smith2024"

        assert by_id["doc:extra"].source == "source-pdfs"
        assert by_id["doc:extra"].citekey is None
        assert by_id["doc:extra"].title == "Extra Source PDF"
        assert by_id["doc:extra"].pdf_path.endswith("extra.pdf")

    def test_source_pdfs_without_manifest_entry_uses_stem_as_title(self, isolated_config):
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True)
        (isolated_config.SOURCE_PDFS_DIR / "untitled.pdf").write_bytes(b"%PDF-1.4")

        docs, _ = corpus.build_corpus()
        assert docs[0].title == "untitled"

    def test_no_source_pdfs_dir_is_fine(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()
        docs, _ = corpus.build_corpus()
        assert len(docs) == 1

    def test_untitled_bib_item_defaults(self, isolated_config):
        con = ledger.connect()
        con.execute(
            "INSERT INTO items (citekey, status, last_synced) VALUES (?, 'discovered', 'now')",
            ("bare_key",),
        )
        con.commit()
        con.close()
        docs, _ = corpus.build_corpus()
        assert docs[0].title == "Untitled"


class TestSourcePdfsAlreadyInTheLedger:
    """A raw PDF that duplicates a bib-backed one must not enter the
    corpus a second time: it would be embedded and clustered twice, once
    citable and once not, with nothing linking the two (issue #42)."""

    def _ledger_pdf(self, isolated_config, citekey="smith2024", body=b"%PDF-1.4 real"):
        """A ledger row whose PDF exists on disk, synced the normal way so
        pdf_hash/pdf_size are populated the way `sync` populates them."""
        pdf = isolated_config.CONTENT_DIR.parent / f"{citekey}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(body)
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey=citekey, pdf_path=str(pdf)))
        con.close()
        return pdf

    def test_the_same_file_is_skipped(self, isolated_config):
        """The bib entry's own PDF living inside the source-pdfs directory
        -- what happens when the reference manager's export and
        `[source_pdfs].dir` are pointed at one place."""
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        pdf = isolated_config.SOURCE_PDFS_DIR / "same.pdf"
        pdf.write_bytes(b"%PDF-1.4 real")
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", pdf_path=str(pdf)))
        con.close()

        docs, complaints = corpus.build_corpus()

        assert [d.doc_id for d in docs] == ["smith2024"]
        assert any("same.pdf" in c and "smith2024" in c for c in complaints)

    def test_a_byte_identical_copy_is_skipped(self, isolated_config):
        self._ledger_pdf(isolated_config, body=b"%PDF-1.4 identical bytes")
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        (isolated_config.SOURCE_PDFS_DIR / "copy.pdf").write_bytes(b"%PDF-1.4 identical bytes")

        docs, complaints = corpus.build_corpus()

        assert [d.doc_id for d in docs] == ["smith2024"]
        assert any("copy.pdf" in c and "smith2024" in c for c in complaints)

    def test_same_size_different_content_is_still_included(self, isolated_config):
        """Size is only the cheap pre-filter -- the digest decides. Two
        different papers that happen to be the same length are two
        documents, not one."""
        self._ledger_pdf(isolated_config, body=b"%PDF-1.4 aaaaaaaa")
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        (isolated_config.SOURCE_PDFS_DIR / "other.pdf").write_bytes(b"%PDF-1.4 bbbbbbbb")

        docs, _ = corpus.build_corpus()

        assert sorted(d.doc_id for d in docs) == ["doc:other", "smith2024"]

    def test_a_genuinely_new_pdf_is_included_and_reported_as_uncitable(self, isolated_config):
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        (isolated_config.SOURCE_PDFS_DIR / "new.pdf").write_bytes(b"%PDF-1.4 brand new")

        docs, complaints = corpus.build_corpus()

        assert [d.doc_id for d in docs] == ["doc:new"]
        assert any("never be cited" in c for c in complaints)

    def test_a_ledger_row_with_no_pdf_never_matches(self, isolated_config):
        """A bib entry whose PDF is missing has no path, size or hash to
        compare against -- it must not swallow an unrelated source PDF."""
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="nopdf2024"))
        con.close()
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        (isolated_config.SOURCE_PDFS_DIR / "unrelated.pdf").write_bytes(b"%PDF-1.4 x")

        docs, _ = corpus.build_corpus()

        assert sorted(d.doc_id for d in docs) == ["doc:unrelated", "nopdf2024"]

    def test_no_source_pdfs_means_no_complaints(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        docs, complaints = corpus.build_corpus()

        assert [d.doc_id for d in docs] == ["smith2024"]
        assert complaints == []

    def test_an_unreadable_source_pdf_is_treated_as_new(self, isolated_config, monkeypatch):
        """Hashing failures must not drop a document silently -- falling
        back to "new" over-includes, which the count above still reports."""
        self._ledger_pdf(isolated_config, body=b"%PDF-1.4 same size!")
        isolated_config.SOURCE_PDFS_DIR.mkdir(parents=True, exist_ok=True)
        (isolated_config.SOURCE_PDFS_DIR / "unreadable.pdf").write_bytes(b"%PDF-1.4 same size!")
        monkeypatch.setattr(corpus, "_sha256", lambda path: None)

        docs, _ = corpus.build_corpus()

        assert "doc:unreadable" in [d.doc_id for d in docs]


class TestUnreadableFilesAreNotFatal:
    """Every comparison in the duplicate check degrades to "treat it as
    new" rather than raising. Over-including a document is visible in the
    run's own count; a traceback in the middle of corpus assembly is not
    something a caller can act on."""

    def test_sha256_of_a_missing_file_is_none(self, tmp_path):
        assert corpus._sha256(tmp_path / "not-there.pdf") is None

    def test_realpath_failure_is_not_fatal(self, monkeypatch):
        def boom(_path):
            raise OSError("nope")

        monkeypatch.setattr(corpus.os.path, "realpath", boom)
        assert corpus._real("/anything") is None

    def test_a_ledger_row_whose_path_cannot_be_resolved_is_skipped(self, monkeypatch):
        monkeypatch.setattr(corpus, "_real", lambda _p: None)
        rows = [{"pdf_path": "/gone.pdf", "citekey": "k", "pdf_size": 10, "pdf_hash": "h"}]
        by_path, by_size = corpus._ledger_pdf_index(rows)
        assert by_path == {}
        assert by_size == {10: [("h", "k")]}

    def test_a_ledger_row_with_no_hash_yet_is_not_indexed_by_size(self):
        """A row synced before its PDF was hashed has nothing to compare
        content against -- size alone must never declare a duplicate."""
        rows = [
            {"pdf_path": "/a.pdf", "citekey": "unhashed", "pdf_size": 10, "pdf_hash": None},
            {"pdf_path": "/b.pdf", "citekey": "hashed", "pdf_size": 20, "pdf_hash": "h"},
        ]
        _by_path, by_size = corpus._ledger_pdf_index(rows)
        assert by_size == {20: [("h", "hashed")]}

    def test_a_file_that_cannot_be_stat_ed_is_treated_as_new(self, tmp_path):
        """A dangling symlink or a file removed between the glob and the
        check: no size to compare, so it cannot be shown to be a
        duplicate, so it stays in the corpus."""
        assert corpus._already_citable(tmp_path / "gone.pdf", {}, {13: [("h", "k")]}) is None


class TestAssertNoCitekeyCollision:
    def test_passes_when_no_collision(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        docs, _ = corpus.build_corpus()
        corpus.assert_no_citekey_collision(docs)  # should not raise

    def test_raises_on_collision_with_real_citekey(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="doc:collide"))
        con.close()

        from src.heavy.corpus import CorpusDoc

        docs = [
            CorpusDoc(doc_id="doc:collide", citekey=None, source="source-pdfs", title="x", pdf_path=None),
        ]
        with pytest.raises(AssertionError, match="collides with a real citekey"):
            corpus.assert_no_citekey_collision(docs)

    def test_raises_if_source_pdfs_doc_has_a_citekey(self, isolated_config):
        from src.heavy.corpus import CorpusDoc

        ledger.connect().close()
        docs = [
            CorpusDoc(doc_id="doc:x", citekey="should-not-have-one", source="source-pdfs", title="x", pdf_path=None),
        ]
        with pytest.raises(AssertionError, match="must not have a citekey"):
            corpus.assert_no_citekey_collision(docs)

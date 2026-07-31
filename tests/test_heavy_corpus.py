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

        docs = corpus.build_corpus()
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

        docs = corpus.build_corpus()
        assert docs[0].title == "untitled"

    def test_no_source_pdfs_dir_is_fine(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()
        docs = corpus.build_corpus()
        assert len(docs) == 1

    def test_untitled_bib_item_defaults(self, isolated_config):
        con = ledger.connect()
        con.execute(
            "INSERT INTO items (citekey, status, last_synced) VALUES (?, 'discovered', 'now')",
            ("bare_key",),
        )
        con.commit()
        con.close()
        docs = corpus.build_corpus()
        assert docs[0].title == "Untitled"


class TestAssertNoCitekeyCollision:
    def test_passes_when_no_collision(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        docs = corpus.build_corpus()
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

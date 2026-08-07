"""src/enrich/corpus.py: the enrichment layer's corpus is the ledger, so
every document it yields is citable and `doc_id == citekey`."""

from src import ledger
from src.enrich import corpus

from tests.conftest import make_reference


class TestBuildCorpus:
    def test_yields_one_doc_per_ledger_item(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="Bib Paper"))
        ledger.upsert_reference(con, make_reference(citekey="jones2023", title="Another"))
        con.close()

        docs = corpus.build_corpus()

        assert sorted(d.doc_id for d in docs) == ["jones2023", "smith2024"]

    def test_carries_the_ledger_row_through(self, isolated_config):
        pdf = isolated_config.CONTENT_DIR.parent / "smith2024.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 real")
        con = ledger.connect()
        ledger.upsert_reference(
            con, make_reference(citekey="smith2024", title="Bib Paper", pdf_path=str(pdf))
        )
        ledger.mark_parsed(con, "smith2024", isolated_config.PARSED_DIR / "smith2024.txt")
        con.commit()
        con.close()

        doc = corpus.build_corpus()[0]

        assert doc.doc_id == "smith2024"
        assert doc.citekey == "smith2024"
        assert doc.title == "Bib Paper"
        assert doc.pdf_path == str(pdf)
        assert doc.text_path.endswith("smith2024.txt")

    def test_doc_id_always_equals_citekey(self, isolated_config):
        """The invariant the rest of the enrichment layer writes files
        under -- Docling's <stem>.md and Chroma's <stem>::<n> chunk ids are
        both keyed off doc_id, and a draft cites the citekey."""
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        assert all(d.doc_id == d.citekey for d in corpus.build_corpus())

    def test_an_empty_ledger_is_an_empty_corpus(self, isolated_config):
        ledger.connect().close()
        assert corpus.build_corpus() == []

    def test_untitled_bib_item_defaults(self, isolated_config):
        con = ledger.connect()
        con.execute(
            "INSERT INTO items (citekey, status, last_synced) VALUES (?, 'discovered', 'now')",
            ("bare_key",),
        )
        con.commit()
        con.close()

        assert corpus.build_corpus()[0].title == "Untitled"

    def test_a_bib_item_with_no_pdf_is_still_a_document(self, isolated_config):
        """It has metadata worth indexing even with nothing to parse --
        the stages downstream each decide what to do with pdf_path=None."""
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="nopdf2024"))
        con.close()

        doc = corpus.build_corpus()[0]

        assert doc.doc_id == "nopdf2024"
        assert doc.pdf_path is None

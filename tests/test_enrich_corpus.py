"""src/enrich/corpus.py: the enrichment layer's corpus is the ledger, so
every document it yields is citable, keyed by its citekey and nothing else."""

from src import bib_reader, ledger
from src.enrich import corpus

from tests.conftest import make_reference


class TestBuildCorpus:
    def test_yields_one_doc_per_ledger_item(self, isolated_config):
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024", title="Bib Paper"))
        ledger.upsert_reference(con, make_reference(citekey="jones2023", title="Another"))
        con.close()

        docs = corpus.build_corpus()

        assert sorted(d.citekey for d in docs) == ["jones2023", "smith2024"]

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

        assert doc.citekey == "smith2024"
        assert doc.citekey == "smith2024"
        assert doc.title == "Bib Paper"
        assert doc.pdf_path == str(pdf)
        assert doc.text_path.endswith("smith2024.txt")

    def test_every_citekey_is_usable_as_a_filename(self, isolated_config):
        """The citekey is the stem every on-disk artefact is written under
        -- Docling's <citekey>.md, Chroma's <citekey>::<n> chunk ids -- so
        one that cannot be a filename must never reach this layer.
        `src/bib_reader.py` is what keeps it out; this pins the contract
        from the consuming end, where the breakage would actually happen."""
        con = ledger.connect()
        ledger.upsert_reference(con, make_reference(citekey="smith2024"))
        con.close()

        for doc in corpus.build_corpus():
            assert bib_reader.citekey_problem(doc.citekey) is None

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

        assert doc.citekey == "nopdf2024"
        assert doc.pdf_path is None

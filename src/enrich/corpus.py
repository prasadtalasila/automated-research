"""The enrichment layer's view of the corpus: the bibliography, nothing else.

Every document here comes from the ledger that `python -m src.sync`
populates from the bib file, so every document is citable and
`doc_id == citekey` -- whatever citekey the exported bib file assigned
(src/bib_reader.py; the bib file is the source of truth, this project
doesn't generate its own).

That is the whole contract, and it is deliberately narrower than it once
was. An earlier version also swept a directory of raw PDFs gathered
outside the bib file (`config.toml`'s `[source_pdfs].dir`) into the
corpus, giving each a `doc:<stem>` id that `citation_gate.py` would
always reject. Supporting those documents cost every stage downstream a
permanently non-citable case -- a Chroma hit with an empty citekey, a
figure record citing a filename instead of a reference, a
size-and-digest duplicate check against the ledger, an assertion keeping
two id namespaces apart -- all to index evidence that no draft was ever
allowed to cite. Sourcing the corpus from the bibliography alone deletes
that case at its origin rather than handling it five times over: if a
paper is worth indexing, catalogue it in your reference manager,
re-export, and re-run `python -m src.sync`.
"""

from dataclasses import dataclass

from src import ledger


@dataclass
class CorpusDoc:
    # doc_id is the stem for this document's on-disk artefacts (Docling's
    # .md/.passages.json, Chroma's chunk ids) and equals citekey. It stays
    # a separate field because those two roles are separate -- one is an
    # identity this layer writes files under, the other is a bibliographic
    # reference a draft cites -- but nothing may make them diverge.
    doc_id: str
    citekey: str
    title: str
    pdf_path: str | None
    text_path: str | None = None


def build_corpus() -> list[CorpusDoc]:
    """Every ledger item, as the enrichment stages consume them."""
    con = ledger.connect()
    try:
        rows = ledger.all_items(con)
    finally:
        con.close()

    return [
        CorpusDoc(
            doc_id=item["citekey"],
            citekey=item["citekey"],
            title=item["title"] or "Untitled",
            pdf_path=item["pdf_path"],
            text_path=item["parsed_path"],
        )
        for item in rows
    ]

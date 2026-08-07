"""The enrichment layer's view of the corpus: the bibliography, nothing else.

Every document here comes from the ledger that `python -m src.sync`
populates from the bib file, so every document is citable, and its
citekey is its whole identity -- whatever citekey the exported bib file
assigned (src/bib_reader.py; the bib file is the source of truth, this
project doesn't generate its own).

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
    # The citekey is this document's whole identity: the stem its on-disk
    # artefacts are written under (Docling's .md/.passages.json, Chroma's
    # chunk ids) as well as the reference a draft cites. Those were two
    # fields while a second, uncitable source existed and they could
    # differ; with one source they never can, and carrying both invited
    # code that treated them as if they might (issue #57).
    #
    # src/bib_reader.py guarantees this is usable as a filename -- see
    # citekey_problem() there, which rejects an entry whose citekey is not.
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
            citekey=item["citekey"],
            title=item["title"] or "Untitled",
            pdf_path=item["pdf_path"],
            text_path=item["parsed_path"],
        )
        for item in rows
    ]

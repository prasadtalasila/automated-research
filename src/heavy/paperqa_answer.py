"""Stage 5: PaperQA2 grounded question-answering over local PDFs.

Needs `paper-qa` from docker/requirements-full.txt in a venv, AND an LLM
API key (PaperQA2 calls out to an LLM for both the per-source summaries
and the final synthesis -- there is no local/offline mode). This
environment has no ANTHROPIC_API_KEY or OPENAI_API_KEY, so this stage
can be installed and wired but not actually executed here; it is
verified by confirming it fails with a clear, actionable message rather
than a traceback when no key is present, not by producing a real answer.

Only Zotero-sourced PDFs and source-pdfs/ PDFs on disk are used -- no
citekeys are invented for source-pdfs docs (see src/heavy/corpus.py);
if you want a PaperQA2 answer's sources treated as real citations, add
the paper to Zotero and sync first.
"""

import os

from src.heavy.corpus import CorpusDoc


class MissingAPIKey(RuntimeError):
    pass


def _require_api_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise MissingAPIKey(
            "PaperQA2 needs an LLM to run -- set ANTHROPIC_API_KEY or "
            "OPENAI_API_KEY and re-run this stage. No key is configured "
            "in this environment, so this stage cannot execute here."
        )


def answer(question: str, docs: list[CorpusDoc]) -> str:
    _require_api_key()

    from paperqa import Docs

    pdf_paths = [doc.pdf_path for doc in docs if doc.pdf_path]
    if not pdf_paths:
        raise ValueError("No PDFs available to build a PaperQA2 Docs collection")

    collection = Docs()
    for path in pdf_paths:
        collection.add(path)

    return str(collection.query(question))

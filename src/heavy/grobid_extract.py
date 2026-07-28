"""Stage 2 (the "GROBID" half of "GROBID/Zotero"): bibliographic-quality
header + reference extraction for source-pdfs docs.

Zotero-sourced docs already have real metadata (src/bib_reader.py) --
this stage exists for the source-pdfs/ docs, which don't. It talks to a
running GROBID REST service (docker/setup.sh starts one on
GROBID_URL, default http://localhost:8070); it does not install or run
GROBID itself, and GROBID needs a JRE this host doesn't have. Calling
this on a host with no reachable GROBID should fail cleanly, not hang
or stack-trace.
"""

import requests

from src import config
from src.heavy.corpus import CorpusDoc, safe_filename


class GrobidUnavailable(RuntimeError):
    pass


def is_available(timeout: float = 3.0) -> bool:
    try:
        resp = requests.get(f"{config.GROBID_URL}/api/isalive", timeout=timeout)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def extract_header(doc: CorpusDoc, timeout: float = 60.0) -> str:
    """Returns GROBID's TEI-XML header extraction for one PDF."""
    if not is_available():
        raise GrobidUnavailable(
            f"No GROBID service reachable at {config.GROBID_URL}. "
            "This needs a JRE + the GROBID service (docker/setup.sh runs "
            "one) -- not available on a plain host without Java. Use the "
            "Docker target, or skip this stage."
        )
    if not doc.pdf_path:
        raise ValueError(f"{doc.doc_id}: no PDF to send to GROBID")

    with open(doc.pdf_path, "rb") as f:
        resp = requests.post(
            f"{config.GROBID_URL}/api/processHeaderDocument",
            files={"input": f},
            timeout=timeout,
        )
    resp.raise_for_status()
    return resp.text


def extract_corpus(docs: list[CorpusDoc]) -> dict[str, str]:
    """Returns {doc_id: 'ok: <path>' | 'unavailable' | 'error: ...'}."""
    if not is_available():
        return {doc.doc_id: "unavailable" for doc in docs}

    config.GROBID_DIR.mkdir(parents=True, exist_ok=True)
    status = {}
    for doc in docs:
        try:
            tei = extract_header(doc)
            out_path = config.GROBID_DIR / f"{safe_filename(doc.doc_id)}.tei.xml"
            out_path.write_text(tei)
            status[doc.doc_id] = f"ok: {out_path}"
        except Exception as exc:  # noqa: BLE001 -- report per-doc, don't abort the batch
            status[doc.doc_id] = f"error: {exc}"
    return status

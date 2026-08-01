import shutil
from pathlib import Path

import pytest

from src import config, ledger


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point every src.config path constant at a throwaway tmp_path tree.

    src.config computes these once at import time as plain Path objects,
    not functions, and every consumer module does `from src import
    config` then reads `config.SOME_PATH` at call time -- so patching
    attributes on this one shared module object is visible everywhere,
    no importlib.reload needed. Each derived path (e.g. PARSED_DIR from
    CONTENT_DIR) is set independently here, since config.py itself only
    derives them once at import time -- patching just the parent
    wouldn't move an already-computed child.
    """
    content_dir = tmp_path / "content"
    source_pdfs_dir = tmp_path / "source-pdfs"

    monkeypatch.setattr(config, "BIB_FILE_PATH", tmp_path / "bibliography.bib")
    monkeypatch.setattr(config, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(config, "PARSED_DIR", content_dir / "parsed")
    monkeypatch.setattr(config, "LEDGER_PATH", content_dir / "ledger.sqlite")
    monkeypatch.setattr(config, "PROVENANCE_DIR", content_dir / "provenance")
    monkeypatch.setattr(config, "RETRIEVAL_INDEX_PATH", content_dir / "retrieval_index.json")
    monkeypatch.setattr(config, "SOURCE_PDFS_DIR", source_pdfs_dir)
    monkeypatch.setattr(config, "SOURCE_PDFS_MANIFEST", source_pdfs_dir / "manifest.json")
    monkeypatch.setattr(config, "DOCLING_DIR", content_dir / "docling")
    monkeypatch.setattr(config, "DOCLING_CACHE_PATH", content_dir / "docling_cache.json")
    monkeypatch.setattr(config, "GROBID_DIR", content_dir / "grobid")
    monkeypatch.setattr(config, "CHROMA_DIR", content_dir / "chroma")
    monkeypatch.setattr(config, "TOPICS_PATH", content_dir / "topics.json")
    monkeypatch.setattr(config, "TOPIC_EMBED_CACHE_PATH", content_dir / "topic_embed_cache.json")
    monkeypatch.setattr(config, "RENDERED_DIR", content_dir / "rendered")
    return config


@pytest.fixture
def ledger_con(isolated_config):
    con = ledger.connect()
    yield con
    con.close()


def make_reference(citekey="smith_example_2024", **overrides):
    """A minimal src.bib_reader.Reference, for tests that don't need a
    real .bib file on disk."""
    from src.bib_reader import Reference

    fields = dict(
        citekey=citekey,
        item_type="article",
        title="An Example Paper",
        authors=[("Jane", "Smith")],
        year="2024",
        doi=None,
        url=None,
        fields={},
        pdf_path=None,
    )
    fields.update(overrides)
    return Reference(**fields)


@pytest.fixture
def make_ref():
    return make_reference


@pytest.fixture
def system_python():
    """A python3 that can't import bibtexparser, to verify the documented
    invariant (AGENTS.md) that citation_gate.py/references.py/
    render_output.py run with the bare system interpreter, no venv
    required. A venv's python is typically just a symlink to the same
    system binary (`file` on it resolves identically to /usr/bin/python3),
    so comparing resolved paths can't tell them apart -- what actually
    differs is which pyvenv.cfg (if any) gets picked up based on the
    *invoked* path, which in turn determines whether bibtexparser is on
    sys.path. So check that directly instead.
    """
    import subprocess

    candidates = []
    which_result = shutil.which("python3")
    if which_result:
        candidates.append(which_result)
    candidates += ["/usr/bin/python3", "/usr/local/bin/python3"]

    seen = set()
    for candidate in candidates:
        if candidate in seen or not Path(candidate).exists():
            continue
        seen.add(candidate)
        probe = subprocess.run(
            [candidate, "-c", "import bibtexparser"],
            capture_output=True,
        )
        if probe.returncode != 0:
            return candidate
    pytest.skip("no system python3 without bibtexparser found on this host")

"""Central configuration for the research pipeline.

Defaults live in config.toml (repo root); any value can be overridden
with an environment variable of the same name (e.g.
BIB_FILE=/path/to/other.bib python -m src.sync) without editing the file.
tomllib is stdlib since Python 3.11, so this adds no dependency.
"""

import os
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(REPO_ROOT / "config.toml")))

with open(CONFIG_PATH, "rb") as _f:
    _toml = tomllib.load(_f)


def _get(env_var: str, *toml_path: str, default: str = "") -> str:
    if env_var in os.environ:
        return os.environ[env_var]
    node = _toml
    for key in toml_path:
        if not isinstance(node, dict):
            return default
        node = node.get(key, {})
    return node if isinstance(node, str) else default


def _get_float(env_var: str, *toml_path: str, default: float) -> float:
    if env_var in os.environ:
        return float(os.environ[env_var])
    node = _toml
    for key in toml_path:
        if not isinstance(node, dict):
            return default
        node = node.get(key, {})
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return float(node)
    return default


# REPO_ROOT / <absolute path> correctly collapses to the absolute path
# (pathlib behavior), so env var overrides may be absolute or relative.
BIB_FILE_PATH = REPO_ROOT / _get("BIB_FILE", "bib", "path", default="bibliography.bib")

CONTENT_DIR = REPO_ROOT / _get("CONTENT_DIR", "content", "dir", default="content")
PARSED_DIR = CONTENT_DIR / "parsed"
LEDGER_PATH = CONTENT_DIR / "ledger.sqlite"
PROVENANCE_DIR = CONTENT_DIR / "provenance"

SOURCE_PDFS_DIR = REPO_ROOT / _get("SOURCE_PDFS_DIR", "source_pdfs", "dir", default="source-pdfs")
SOURCE_PDFS_MANIFEST = SOURCE_PDFS_DIR / "manifest.json"

# Heavier optional pipeline (docker/requirements-full.txt), per src/heavy/.
DOCLING_DIR = CONTENT_DIR / "docling"
GROBID_DIR = CONTENT_DIR / "grobid"
CHROMA_DIR = CONTENT_DIR / "chroma"
TOPICS_PATH = CONTENT_DIR / "topics.json"
RENDERED_DIR = CONTENT_DIR / "rendered"

GROBID_URL = _get("GROBID_URL", "heavy", "grobid_url", default="http://localhost:8070")
GROBID_HEALTH_TIMEOUT = _get_float(
    "GROBID_HEALTH_TIMEOUT", "heavy", "grobid_health_timeout", default=3.0,
)
GROBID_EXTRACT_TIMEOUT = _get_float(
    "GROBID_EXTRACT_TIMEOUT", "heavy", "grobid_extract_timeout", default=60.0,
)
EMBEDDING_MODEL = _get(
    "EMBEDDING_MODEL", "heavy", "embedding_model",
    default="sentence-transformers/all-MiniLM-L6-v2",
)

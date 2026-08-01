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


def _get_bool(env_var: str, *toml_path: str, default: bool) -> bool:
    """Env vars arrive as strings, so "false"/"0"/"no" have to be read as
    False -- bool("false") is True, which would make every documented way
    of turning a setting off via the environment silently turn it on."""
    if env_var in os.environ:
        return os.environ[env_var].strip().lower() in ("1", "true", "yes", "on")
    node = _toml
    for key in toml_path:
        if not isinstance(node, dict):
            return default
        node = node.get(key, {})
    return node if isinstance(node, bool) else default


# REPO_ROOT / <absolute path> correctly collapses to the absolute path
# (pathlib behavior), so env var overrides may be absolute or relative.
BIB_FILE_PATH = REPO_ROOT / _get("BIB_FILE", "bib", "path", default="papers/bibliography.bib")

CONTENT_DIR = REPO_ROOT / _get("CONTENT_DIR", "content", "dir", default="content")
PARSED_DIR = CONTENT_DIR / "parsed"
LEDGER_PATH = CONTENT_DIR / "ledger.sqlite"
PROVENANCE_DIR = CONTENT_DIR / "provenance"
# Cached BM25 term-frequency index for src/retrieval.py -- keyed by a
# cheap per-item fingerprint (parsed-file stat, not content), so a
# search() call only re-tokenizes docs whose text actually changed since
# the last run, mirroring src/ledger.py's own stat-before-hash skip logic.
RETRIEVAL_INDEX_PATH = CONTENT_DIR / "retrieval_index.json"

SOURCE_PDFS_DIR = REPO_ROOT / _get("SOURCE_PDFS_DIR", "source_pdfs", "dir", default="papers/pdfs")
SOURCE_PDFS_MANIFEST = SOURCE_PDFS_DIR / "manifest.json"

# Which backend src/pdf_text.py dispatches to -- see config.toml's
# [parser] comment for the tradeoffs (speed, page-boundary loss) before
# switching off the default.
PARSER_BACKENDS = ("pdftotext", "docling")
PARSER = _get("PARSER", "parser", "backend", default="pdftotext")

# Parse-quality guard (src/pdf_text.quality_warning): a PDF extractor
# that sets its glyph-spacing tolerance too coarse fuses adjacent words
# together, which src/retrieval.py's whitespace tokenizer then cannot
# match against. Measured over the same 10 PDFs, pdftotext produced
# 0.01% such tokens and a since-removed backend produced 4.19% -- three
# orders of magnitude apart -- so 1% sits well clear of both.
PARSE_LONG_WORD_CHARS = int(_get_float("PARSE_LONG_WORD_CHARS", "parser", "long_word_chars", default=20))
PARSE_LONG_WORD_RATIO = _get_float("PARSE_LONG_WORD_RATIO", "parser", "long_word_ratio", default=0.01)
# Below this many words the ratio is too noisy to mean anything (a
# cover page, or a scan that yielded almost no text).
PARSE_MIN_TOKENS = int(_get_float("PARSE_MIN_TOKENS", "parser", "min_tokens", default=200))

# Heavier optional pipeline (pyproject.toml's "heavy" Poetry group), per src/heavy/.
DOCLING_DIR = CONTENT_DIR / "docling"
# Per-doc (size, mtime_ns) PDF fingerprint, so docling_parse.parse_doc()
# only re-runs Docling's layout/OCR models -- the slowest stage in this
# pipeline -- for a PDF that's new or has actually changed since the last
# call, mirroring src/ledger.py's own stat-before-hash skip logic.
DOCLING_CACHE_PATH = CONTENT_DIR / "docling_cache.json"
# Whether docling_parse.py also extracts figure bitmaps (into
# content/docling/<doc>_artifacts/) plus a <doc>.figures.json index of
# page/caption/citation for each. Changing this invalidates the whole
# Docling cache -- it changes what every .md should contain, so the next
# run re-parses the corpus from scratch. See DEVELOPER.md's "Figures".
DOCLING_IMAGES = _get_bool("DOCLING_IMAGES", "heavy", "docling_images", default=False)
# Render scale for those bitmaps; 2.0 is ~144 DPI, legible for reading a
# figure back while checking a draft without storing print-resolution PNGs.
DOCLING_IMAGE_SCALE = _get_float("DOCLING_IMAGE_SCALE", "heavy", "docling_image_scale", default=2.0)
CHROMA_DIR = CONTENT_DIR / "chroma"

TOPICS_PATH = CONTENT_DIR / "topics.json"
# Per-doc whole-text embedding cache keyed by content hash, so
# topic_model.run_topic_model() only re-encodes docs whose text actually
# changed since the last run -- see that module's docstring.
TOPIC_EMBED_CACHE_PATH = CONTENT_DIR / "topic_embed_cache.json"
RENDERED_DIR = CONTENT_DIR / "rendered"

EMBEDDING_MODEL = _get(
    "EMBEDDING_MODEL", "heavy", "embedding_model",
    default="sentence-transformers/all-MiniLM-L6-v2",
)

"""Central configuration for the research pipeline.

Paths can be overridden with environment variables of the same name
(e.g. ZOTERO_DATA_DIR=/path/to/other/Zotero python -m src.sync).
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ZOTERO_DATA_DIR = Path(os.environ.get("ZOTERO_DATA_DIR", "/home/TestUserDTaaS/Zotero"))
ZOTERO_SQLITE = ZOTERO_DATA_DIR / "zotero.sqlite"
ZOTERO_STORAGE = ZOTERO_DATA_DIR / "storage"

CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", str(REPO_ROOT / "content")))
PARSED_DIR = CONTENT_DIR / "parsed"
LEDGER_PATH = CONTENT_DIR / "ledger.sqlite"
LIBRARY_BIB_PATH = CONTENT_DIR / "library.bib"
PROVENANCE_DIR = CONTENT_DIR / "provenance"

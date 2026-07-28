"""Stage 7: Pandoc/LaTeX rendering of generated Markdown/tex into PDF/DOCX.

Needs the `pandoc` and TeX Live binaries -- these are apt packages, not
pip packages, so they can't be installed via docker/requirements-full.txt
or a venv. This host has neither and has no sudo to get them; only the
Docker target (docker/Dockerfile installs both) can run this stage.
Verified here via the clean-failure path only.
"""

import shutil
import subprocess
from pathlib import Path

from src import config


class MissingBinary(RuntimeError):
    pass


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise MissingBinary(
            f"'{binary}' is not on PATH. This stage needs Pandoc + TeX Live, "
            "which need root to install (apt) and aren't available here. "
            "Use the Docker target (docker/Dockerfile installs both)."
        )


def render(input_path: str, output_format: str = "pdf") -> Path:
    _require("pandoc")
    if output_format == "pdf":
        _require("pdflatex")

    config.RENDERED_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path)
    out_path = config.RENDERED_DIR / f"{input_path.stem}.{output_format}"

    subprocess.run(
        ["pandoc", str(input_path), "-o", str(out_path)],
        check=True, capture_output=True, text=True,
    )
    return out_path

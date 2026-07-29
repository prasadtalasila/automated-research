"""Stage 7: Pandoc/LaTeX rendering of generated Markdown into PDF/DOCX.

Needs the `pandoc` and TeX Live binaries (apt packages, not pip -- not
installable via docker/requirements-full.txt or a venv). Verified working
on this host (2026-07-28): `pandoc`, `pdflatex`, `latexmk` are all on
PATH. Where they aren't, this stage fails cleanly with MissingBinary
rather than hanging or stack-tracing -- see docker/Dockerfile for a
target that installs them when the host doesn't have root.

Every genre-skill draft cites sources with Pandoc-style `[@citekey]`
markers (see src/citation_gate.py), so rendering always resolves them via
pandoc's built-in `--citeproc` against `config.BIB_FILE_PATH` -- without
it, citations would come out as literal, unresolved `[@key]` text with no
bibliography. Pandoc's own citation-key tokenizer has a real limitation
that surfaces on this corpus: a double hyphen (`--`) inside a citekey
(bibtexparser produces these, e.g. `zech_digital-twins-as--service_2024`)
truncates the key mid-token, silently losing the citation. `_safe_render_inputs`
works around this by aliasing just the affected citekey(s) in temporary
copies of the input and the bib file -- never touching the real
`bibliography.bib` -- before handing both to pandoc.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from src import config
from src.citation_gate import _PANDOC_CITE_RE


class MissingBinary(RuntimeError):
    pass


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise MissingBinary(
            f"'{binary}' is not on PATH. This stage needs Pandoc + TeX Live, "
            "which need root to install (apt) and aren't available here. "
            "Use the Docker target (docker/Dockerfile installs both)."
        )


def _alias_for(citekey: str) -> str:
    # "--" is the one substring pandoc's own citation tokenizer can't
    # carry through a citekey (see module docstring) -- collapsing it to
    # a single hyphen plus a marker keeps the alias readable and, checked
    # against every citekey currently in the ledger, collision-free.
    return citekey.replace("--", "-x2d-")


def _safe_render_inputs(input_path: Path, bib_path: Path, tmp_dir: Path) -> tuple[Path, Path]:
    """Returns (markdown_path, bib_path) safe to hand to `pandoc --citeproc`.

    If no citekey in `input_path` contains "--", returns the original
    paths unchanged (no temp copies needed). Otherwise returns paths to
    aliased copies under `tmp_dir` -- the real bibliography.bib is never
    modified.
    """
    text = input_path.read_text()
    bad_keys = {m.group(1) for m in _PANDOC_CITE_RE.finditer(text) if "--" in m.group(1)}
    if not bad_keys:
        return input_path, bib_path

    bib_text = bib_path.read_text()
    for key in bad_keys:
        alias = _alias_for(key)
        text = re.sub(
            r"(?<![A-Za-z0-9._%+-])(-?@)" + re.escape(key) + r"(?![A-Za-z0-9_-])",
            r"\1" + alias,
            text,
        )
        # Anchored on the entry header's trailing "," so e.g. aliasing
        # `zech_digital-twins-as--service_2024` doesn't also touch the
        # separate `zech_digital-twins-as--service_2024-1` entry.
        bib_text = re.sub(
            r"(@\w+\{)" + re.escape(key) + r"(,)",
            r"\1" + alias + r"\2",
            bib_text,
            count=1,
        )

    safe_md = tmp_dir / input_path.name
    safe_bib = tmp_dir / bib_path.name
    safe_md.write_text(text)
    safe_bib.write_text(bib_text)
    return safe_md, safe_bib


def render(input_path: str, output_format: str = "pdf", documentclass: str = "article") -> Path:
    """Renders `input_path` (Pandoc markdown) to `output_format` (pdf/tex/docx/...).

    `--standalone` is always passed so a `tex` output is a complete,
    compilable LaTeX document (documentclass + preamble), not a bare
    fragment -- matching what pandoc already builds internally on the way
    to a `pdf` output. `documentclass` defaults to LaTeX's plain `article`
    class, the right shape for the short, section-based genre drafts this
    project produces (no chapters, no front matter); pass a different
    value only if a specific draft genuinely needs one.
    """
    _require("pandoc")
    if output_format == "pdf":
        _require("pdflatex")

    config.RENDERED_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path)
    out_path = config.RENDERED_DIR / f"{input_path.stem}.{output_format}"

    with tempfile.TemporaryDirectory() as tmp:
        safe_md, safe_bib = _safe_render_inputs(input_path, config.BIB_FILE_PATH, Path(tmp))
        cmd = [
            "pandoc", str(safe_md),
            "--standalone",
            "--variable", f"documentclass={documentclass}",
            "--citeproc", "--bibliography", str(safe_bib),
        ]
        if output_format == "pdf":
            cmd += ["--pdf-engine", "pdflatex"]
        cmd += ["-o", str(out_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    return out_path

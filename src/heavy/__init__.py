"""Optional heavier pipeline: Docling -> sentence-transformers/Chroma
-> BERTopic -> Pandoc/LaTeX.

Most of what's in here needs pyproject.toml's "heavy" Poetry group
(`poetry install --with heavy`), installed in a venv (PEP 668 blocks
system pip on the host this was built on). The stdlib-only core
pipeline in src/ (sync, retrieval, citation_gate) does not depend on
anything here and keeps working regardless.

Exception: render_output.py needs only stdlib plus src.config /
src.citation_gate / src.references (also stdlib-only) -- it runs fine
with the bare system python3, no heavy group installed, same as
citation_gate.py itself. It does need `pandoc`/`pdflatex` on PATH, but
those are apt packages, not part of this Poetry group -- see that
module's own docstring.
"""

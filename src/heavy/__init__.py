"""Optional heavier pipeline: Docling -> GROBID -> sentence-transformers/Chroma
-> BERTopic -> Pandoc/LaTeX.

Everything in here needs dependencies from docker/requirements-full.txt,
installed in a venv (PEP 668 blocks system pip on the host this was built
on). The stdlib-only core pipeline in src/ (sync, retrieval, citation_gate)
does not depend on anything here and keeps working regardless.
"""

# Optional heavier toolchain for the research pipeline, per the original
# design (Ubuntu 24.04 + Claude Code with full permissions in an isolated
# container). Unlocks TeX Live (compiling generated .tex chapters to
# PDF), Pandoc, and the embeddings/clustering stack
# (sentence-transformers + Chroma + BERTopic)
# for hosts that don't have root, TeX Live, or Pandoc available
# directly. `pip install` outside a venv is blocked by PEP 668 regardless
# of root, though -- see AGENTS.md's "Environment constraints" section for
# what is and isn't conditional on root access.
#
# NOT built or run in this session -- no Docker daemon is available here.
# Treat this as a starting point to validate, not a tested artifact.
#
# Build:
#   docker build -t chitragupta -f docker/Dockerfile .
# Run (mount persistent volumes -- see note at the bottom):
#   docker run -it --rm \
#     -v "$(pwd)":/workspace/chitragupta \
#     -v chitragupta-content:/workspace/chitragupta/content \
#     chitragupta

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /workspace/chitragupta

# Single script also used on a bare host -- one source of truth for
# dependency versions/order/logic instead of separate, divergent copies
# of that logic here. Note this COPY precedes both stages
# below, so editing this script invalidates both of their layers -- the
# per-stage caching below only protects against edits to *later*
# Dockerfile lines or unrelated repo files, not to this script.
COPY scripts/install_full_pipeline.sh scripts/install_full_pipeline.sh
RUN chmod +x scripts/install_full_pipeline.sh

# Stage 1: OS packages (TeX Live, Pandoc, poppler-utils,
# git/curl/unzip). Its own cached layer -- rarely changes.
RUN scripts/install_full_pipeline.sh os-deps && rm -rf /var/lib/apt/lists/*

# Stage 2: Python deps -- same script + pyproject.toml/poetry.lock as
# the host (Poetry itself came from stage 1's os-deps, which apt-installs
# python3-poetry). python -m src.citation_gate doesn't need any of this
# and still runs with the bare python3 from stage 1; python -m src.sync
# needs bibtexparser, the one dependency the rest of the core pipeline
# can't avoid.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV VIRTUAL_ENV=/opt/venv
COPY pyproject.toml poetry.lock poetry.toml ./
RUN SKIP_VENV=1 scripts/install_full_pipeline.sh python-deps

CMD ["/bin/bash"]

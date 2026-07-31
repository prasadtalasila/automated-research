# Optional heavier toolchain for the research pipeline, per the original
# design (Ubuntu 24.04 + Claude Code with full permissions in an isolated
# container). Unlocks GROBID (bibliographic-quality PDF parsing), TeX Live
# (compiling generated .tex chapters to PDF), Pandoc, and the
# embeddings/clustering stack (sentence-transformers + Chroma + BERTopic)
# for hosts that don't have root, a JDK, TeX Live, or Pandoc available
# directly. `pip install` outside a venv is blocked by PEP 668 regardless
# of root, though -- see AGENTS.md's "Environment constraints" section for
# what is and isn't conditional on root access.
#
# NOT built or run in this session -- no Docker daemon is available here,
# and this revision (new per-stage layer split, new install-script
# stages, new GROBID source URL) is more unverified than before, not
# less. Treat this as a starting point to validate, not a tested artifact.
#
# Build:
#   docker build -t research-pipeline -f docker/Dockerfile .
# Run (mount persistent volumes -- see note at the bottom):
#   docker run -it --rm \
#     -v "$(pwd)":/workspace/automated-research \
#     -v research-pipeline-content:/workspace/automated-research/content \
#     research-pipeline

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /workspace/automated-research

# Single script also used on a bare host -- one source of truth for
# dependency versions/order/logic (notably the GROBID JDK-21-specifically
# requirement) instead of separate, divergent copies of that logic here.
# Note this COPY precedes all three stages
# below, so editing this script invalidates all of their layers
# (including the multi-GB grobid one) -- the per-stage caching below only
# protects against edits to *later* Dockerfile lines or unrelated repo
# files, not to this script.
COPY scripts/install_full_pipeline.sh scripts/install_full_pipeline.sh
RUN chmod +x scripts/install_full_pipeline.sh

# Stage 1: OS packages (JDK 21, TeX Live, Pandoc, poppler-utils,
# git/curl/unzip). Its own cached layer -- rarely changes.
RUN scripts/install_full_pipeline.sh os-deps && rm -rf /var/lib/apt/lists/*

# Stage 2: GROBID -- fetch + build from source (multi-GB, multi-minute).
# Its own layer, placed before the Python layers below, so editing those
# (or anything else later in this file) doesn't force a rebuild of this
# one -- Docker only invalidates a layer and what follows it, not what
# came before.
ARG GROBID_VERSION=0.9.0
ENV GROBID_DIR=/opt/grobid
RUN GROBID_VERSION="${GROBID_VERSION}" scripts/install_full_pipeline.sh grobid

# Stage 3: Python deps -- same script + pyproject.toml/poetry.lock as
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

COPY docker/setup.sh /usr/local/bin/setup-grobid.sh
RUN chmod +x /usr/local/bin/setup-grobid.sh

CMD ["/bin/bash"]

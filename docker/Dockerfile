# Optional heavier toolchain for the research pipeline, per the original
# design (Ubuntu 24.04 + Claude Code with full permissions in an isolated
# container). Unlocks GROBID (bibliographic-quality PDF parsing), TeX Live
# (compiling generated .tex chapters to PDF), Pandoc, and the
# embeddings/clustering stack (sentence-transformers + Chroma + BERTopic)
# that this host cannot install (no root, PEP 668 blocks system pip).
#
# NOT built or run in this session -- no Docker daemon is available here.
# Treat this as a starting point to validate, not a tested artifact.
#
# Build:
#   docker build -t research-pipeline -f docker/Dockerfile .
# Run (mount persistent volumes -- see note at the bottom):
#   docker run -it --rm \
#     -v "$(pwd)":/workspace/automated-research \
#     -v "$HOME/Zotero":/root/Zotero:ro \
#     -v research-pipeline-content:/workspace/automated-research/content \
#     research-pipeline

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base toolchain: Python for the pipeline scripts, poppler-utils for
# pdftotext (already used on the host), git, and a JRE for GROBID.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    poppler-utils \
    git curl ca-certificates \
    openjdk-17-jre-headless \
    pandoc \
    texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended latexmk \
    && rm -rf /var/lib/apt/lists/*

# GROBID: fetch a released, self-contained distribution (ships its own
# run script) rather than nesting another Docker daemon inside this
# container -- avoids the extra privileged-mode risk that would come
# with Docker-in-Docker.
ARG GROBID_VERSION=0.8.1
RUN curl -fsSL -o /tmp/grobid.zip \
        "https://github.com/kermitt2/grobid/archive/refs/tags/${GROBID_VERSION}.zip" \
    && mkdir -p /opt/grobid \
    && unzip -q /tmp/grobid.zip -d /opt/grobid --strip-components=1 \
    && rm /tmp/grobid.zip
# Building GROBID from source (./gradlew clean install) is a multi-GB,
# multi-minute step best run once and cached as its own image layer --
# left as an explicit step in docker/setup.sh rather than baked in here,
# so iterating on the rest of this Dockerfile doesn't re-trigger it.

WORKDIR /workspace/automated-research

# Python deps for the heavier retrieval/clustering path. Kept separate
# from the stdlib-only core pipeline (src/), which runs without any of
# this installed.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY docker/requirements-full.txt /tmp/requirements-full.txt
RUN pip install --no-cache-dir -r /tmp/requirements-full.txt

COPY docker/setup.sh /usr/local/bin/setup-grobid.sh
RUN chmod +x /usr/local/bin/setup-grobid.sh

CMD ["/bin/bash"]

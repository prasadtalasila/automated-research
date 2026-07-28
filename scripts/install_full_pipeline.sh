#!/usr/bin/env bash
# Single install path for both a bare host and the Docker image
# (docker/Dockerfile calls this exact script) -- one source of truth for
# how dependencies get installed, so a fix discovered on one target
# (e.g. the paper-qa/knowledge-storm install-order conflict pinned in
# docker/requirements-full.txt) automatically applies to both, instead
# of drifting between a hand-run host command and a separate Dockerfile
# RUN line.
#
# Host usage:
#   bash scripts/install_full_pipeline.sh
#   (creates/reuses .venv-full/; override with VENV_DIR=/other/path)
#   then: .venv-full/bin/python -m src.sync
#         .venv-full/bin/python scripts/full_pipeline.py
#
# Docker usage: docker/Dockerfile creates /opt/venv, puts it on PATH,
# and runs this script with SKIP_VENV=1 so it installs into that venv
# via the `pip` already on PATH instead of creating a second one.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIREMENTS="$REPO_ROOT/docker/requirements-full.txt"

if [ "${SKIP_VENV:-0}" = "1" ]; then
    PIP=pip
else
    VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv-full}"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    PIP="$VENV_DIR/bin/pip"
fi

"$PIP" install --upgrade pip
"$PIP" install -r "$REQUIREMENTS"

echo
echo "Installed. Run pipeline scripts via:"
if [ "${SKIP_VENV:-0}" = "1" ]; then
    echo "  python -m src.sync"
    echo "  python scripts/full_pipeline.py"
else
    echo "  $VENV_DIR/bin/python -m src.sync"
    echo "  $VENV_DIR/bin/python scripts/full_pipeline.py"
fi

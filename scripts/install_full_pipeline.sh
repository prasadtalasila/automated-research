#!/usr/bin/env bash
# Single install path for both a bare host and the Docker image -- one
# source of truth for how every dependency gets installed (OS packages,
# GROBID, and the Python venv), so a fix discovered on one target (e.g.
# the paper-qa/knowledge-storm install-order conflict pinned in
# docker/requirements-full.txt) automatically applies to both, instead
# of drifting between a hand-run host command and separate Dockerfile
# RUN lines.
#
# Usage: bash scripts/install_full_pipeline.sh [STAGE ...]
#
#   python-deps  (default if no STAGE given) -- venv + pip install of
#                docker/requirements-full.txt. What every host needs
#                regardless of which OS packages are present.
#   os-deps      -- apt-get the system packages the heavy pipeline needs
#                (JDK 21, TeX Live, Pandoc, poppler-utils, git/curl/unzip).
#                Needs root; auto-sudo's if not already root. Opt-in --
#                not everyone wants this script touching apt.
#   grobid       -- fetch + build GROBID standalone (multi-GB, slow).
#                Opt-in and not part of `all` for the same reason.
#   all          -- os-deps + python-deps.
#
# Host usage:
#   bash scripts/install_full_pipeline.sh all
#   bash scripts/install_full_pipeline.sh grobid   # optional, heavy
#   then: .venv-full/bin/python -m src.sync
#         .venv-full/bin/python scripts/full_pipeline.py
#
# Docker usage: docker/Dockerfile calls this once per stage as separate
# RUN lines (os-deps, grobid, then python-deps with SKIP_VENV=1 into the
# /opt/venv it creates) so each stage is its own cached layer -- editing
# later Dockerfile content or repo files doesn't force earlier ones to
# rebuild.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIREMENTS="$REPO_ROOT/docker/requirements-full.txt"

GROBID_VERSION="${GROBID_VERSION:-0.9.0}"
GROBID_DIR="${GROBID_DIR:-$HOME/grobid-${GROBID_VERSION}}"

sudo_if_needed() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "Need root to run: $*" >&2
        echo "Re-run this script as root, or install sudo." >&2
        exit 1
    fi
}

install_os_deps() {
    echo "Installing OS packages (JDK 21, TeX Live, Pandoc, poppler-utils) ..."
    sudo_if_needed apt-get update
    sudo_if_needed apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        poppler-utils \
        git curl ca-certificates unzip \
        openjdk-21-jdk-headless \
        pandoc \
        texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended latexmk
}

install_python_deps() {
    if [ "${SKIP_VENV:-0}" = "1" ]; then
        PIP=pip
    else
        if [ "$(id -u)" = "0" ]; then
            echo "Warning: running as root (e.g. via sudo) will create a root-owned" >&2
            echo ".venv-full/ that your normal user can't later modify or remove" >&2
            echo "without sudo. Re-run without sudo, or set SKIP_VENV=1 if this is" >&2
            echo "intentional (e.g. inside Docker, where /opt/venv is already root-owned)." >&2
        fi
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
}

# GROBID's build.gradle pins a Java 21 toolchain, and its bundled Kotlin
# compiler (2.0.21) throws `IllegalArgumentException: 25.0.3` trying to
# parse a JDK 25 version string -- it predates JDK 25's existence.
# Discovered by hand the slow way (install 25, watch the build fail deep
# inside the Kotlin compiler, reinstall 21); checking up front turns that
# into one clear line of output instead.
check_java21() {
    if ! command -v java >/dev/null 2>&1; then
        echo "java not found. Run '$0 os-deps' first, or install a JDK 21 manually." >&2
        exit 1
    fi
    if ! command -v javac >/dev/null 2>&1; then
        echo "javac not found -- you have a JRE, not a JDK. GROBID compiles" >&2
        echo "Kotlin/Java from source and needs a full JDK, not just a JRE." >&2
        exit 1
    fi
    local ver
    ver="$(java -version 2>&1 | head -1 | grep -oE '"[0-9]+' | tr -d '"')"
    if [ "$ver" != "21" ]; then
        echo "java is version ${ver:-unknown}, but GROBID needs exactly JDK 21" >&2
        echo "(not whatever's newest): its build.gradle pins a Java 21" >&2
        echo "toolchain, and its bundled Kotlin compiler (2.0.21) cannot parse" >&2
        echo "a non-21 (e.g. JDK 25) version string." >&2
        local jdk21
        jdk21="$(ls -d /usr/lib/jvm/*21* 2>/dev/null | head -1)"
        if [ -n "$jdk21" ]; then
            echo "A JDK 21 looks already installed at ${jdk21} -- it's just not" >&2
            echo "the default 'java'. Either of these fixes it (don't need both):" >&2
            echo "  sudo update-alternatives --config java   # changes the system default" >&2
            echo "  echo 'org.gradle.java.home=${jdk21}' >> ${GROBID_DIR}/gradle.properties   # scoped to this GROBID checkout only" >&2
        else
            echo "Install JDK 21 specifically, e.g.:" >&2
            echo "  sudo apt-get install -y openjdk-21-jdk-headless" >&2
        fi
        exit 1
    fi
}

install_grobid() {
    if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
        echo "curl and unzip are required to fetch GROBID. Run '$0 os-deps'" >&2
        echo "first, or install them manually." >&2
        exit 1
    fi
    check_java21

    if [ -x "${GROBID_DIR}/gradlew" ]; then
        echo "GROBID source already present at ${GROBID_DIR}, skipping fetch."
    else
        echo "Fetching GROBID ${GROBID_VERSION} from grobidOrg/grobid ..."
        mkdir -p "${GROBID_DIR}"
        curl -fsSL -o /tmp/grobid.zip \
            "https://github.com/grobidOrg/grobid/archive/refs/tags/${GROBID_VERSION}.zip"
        unzip -q /tmp/grobid.zip -d "${GROBID_DIR}" --strip-components=1
        rm /tmp/grobid.zip
    fi

    echo "Building GROBID (first run only; this step is what's slow/multi-GB) ..."
    (cd "${GROBID_DIR}" && ./gradlew clean build -x test)

    echo
    echo "Built. GROBID_DIR=${GROBID_DIR}"
    echo "Start it with:"
    echo "  (cd ${GROBID_DIR} && ./gradlew run)"
    echo "-- or build+run the standalone distribution; see README's"
    echo "'Building GROBID standalone' section for that recipe."
}

STAGES=("$@")
if [ ${#STAGES[@]} -eq 0 ]; then
    STAGES=("python-deps")
fi

for stage in "${STAGES[@]}"; do
    case "$stage" in
        os-deps) install_os_deps ;;
        python-deps) install_python_deps ;;
        grobid) install_grobid ;;
        all) install_os_deps; install_python_deps ;;
        *)
            echo "Unknown stage: $stage" >&2
            echo "Expected one of: os-deps, python-deps, grobid, all" >&2
            exit 1
            ;;
    esac
done

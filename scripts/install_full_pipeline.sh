#!/usr/bin/env bash
# Single install path for both a bare host and the Docker image -- one
# source of truth for how every dependency gets installed (OS packages,
# GROBID, and the Python venv), so a fix discovered on one target
# automatically applies to both, instead of drifting between a hand-run
# host command and separate Dockerfile RUN lines.
#
# Usage: bash scripts/install_full_pipeline.sh [STAGE ...]
#
#   python-deps  (default if no STAGE given) -- venv + `poetry install
#                --with heavy` (see pyproject.toml/poetry.lock). What
#                every host needs regardless of which OS packages are
#                present. Poetry is a dependency/lockfile manager here
#                only -- package-mode = false in pyproject.toml, nothing
#                is published or pip-installable from this repo.
#   os-deps      -- apt-get the system packages the heavy pipeline needs
#                (JDK 21, TeX Live, Pandoc, poppler-utils, Poetry itself,
#                git/curl/unzip). Needs root; auto-sudo's if not already
#                root. Opt-in -- not everyone wants this script touching apt.
#   grobid       -- fetch + build GROBID standalone (multi-GB, slow).
#                Opt-in and not part of `all` for the same reason.
#   dev-deps     -- `poetry install --with dev` (pytest, pytest-cov) into
#                the same venv as python-deps. Only needed to run the
#                test suite, not the pipeline itself -- opt-in, and not
#                part of `all`. Run `python-deps` first.
#   all          -- os-deps + python-deps.
#
# Host usage:
#   bash scripts/install_full_pipeline.sh all
#   bash scripts/install_full_pipeline.sh grobid     # optional, heavy
#   bash scripts/install_full_pipeline.sh dev-deps   # optional, to run tests
#   then: .venv-full/bin/python -m src.sync
#         .venv-full/bin/python scripts/full_pipeline.py
#         .venv-full/bin/python -m pytest
#
# Docker usage: docker/Dockerfile calls this once per stage as separate
# RUN lines (os-deps, grobid, then python-deps with SKIP_VENV=1 into the
# /opt/venv it creates) so each stage is its own cached layer -- editing
# later Dockerfile content or repo files doesn't force earlier ones to
# rebuild.
#
# Why Poetry doesn't need its own venv-creation step, in either target:
# `poetry.toml` (committed, project-local) sets `virtualenvs.create =
# false`, so Poetry always installs into whatever venv `VIRTUAL_ENV`
# points at rather than making its own -- this script still creates that
# venv itself (python3 -m venv on a bare host; already done by
# docker/Dockerfile via a separate RUN line for /opt/venv), then exports
# VIRTUAL_ENV before calling `poetry install`. One mechanism for both
# targets, instead of Poetry inventing a second, differently-named venv
# convention (its own in-project mode always calls the directory
# `.venv`, which would silently orphan the existing `.venv-full` this
# script and every doc already reference).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
    echo "Installing OS packages (JDK 21, TeX Live, Pandoc, poppler-utils, Poetry) ..."
    sudo_if_needed apt-get update
    sudo_if_needed apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        python3-poetry \
        poppler-utils \
        git curl ca-certificates unzip zip \
        openjdk-21-jdk-headless \
        pandoc \
        texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended latexmk \
        lmodern
    # python3-poetry (apt), not `pip install poetry`: PEP 668 blocks bare
    # pip on this host regardless of root (see AGENTS.md), and Poetry is
    # itself the thing python-deps below shells out to -- it can't
    # bootstrap itself via the same pip it's meant to replace.
    # lmodern (the LaTeX package providing lmodern.sty) is a separate
    # Debian package from fonts-lmodern (just the OTF font files) --
    # texlive-latex-recommended pulls in the latter but not the former.
    # Pandoc's default LaTeX template \usepackage{lmodern}s unconditionally,
    # so without it every pandoc/pdflatex render fails with "File
    # `lmodern.sty' not found", not a docling/GROBID-side problem. Found
    # by hand rendering a real draft after the rest of the toolchain
    # reported fine.
    # zip: scripts/release.py itself only needs stdlib zipfile, not this
    # binary -- it's here so a human can inspect/repack a release archive
    # by hand (unzip was already required for GROBID's own extraction).
}

check_poetry() {
    if ! command -v poetry >/dev/null 2>&1; then
        echo "poetry not found. Run '$0 os-deps' first (installs python3-poetry)," >&2
        echo "or install Poetry manually: https://python-poetry.org/docs/#installation" >&2
        exit 1
    fi
}

# POSIX venvs (python3 -m venv on Linux/macOS) put the interpreter/pip
# under bin/; a native Windows Python's venv module uses Scripts/
# instead -- this script's own callers (below) and CI
# (.github/workflows/ci.yml's windows-latest leg) both need to work with
# either layout, so every bin/-hardcoded path goes through this instead.
venv_bin_dir() {
    if [ -x "$1/bin/python" ] || [ -x "$1/bin/python3" ]; then
        echo "$1/bin"
    else
        echo "$1/Scripts"
    fi
}

# Resolves (and creates, unless SKIP_VENV=1) the venv this script's
# `poetry install` calls target, and exports VIRTUAL_ENV so Poetry uses
# it -- poetry.toml's `virtualenvs.create = false` means Poetry will
# never make its own, by design (see the header comment for why).
resolve_venv_dir() {
    if [ "${SKIP_VENV:-0}" = "1" ]; then
        VENV_DIR="${VIRTUAL_ENV:-/opt/venv}"
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
    fi
    export VIRTUAL_ENV="$VENV_DIR"
}

install_python_deps() {
    check_poetry
    resolve_venv_dir

    (cd "$REPO_ROOT" && poetry install --with heavy)
    local bin_dir
    bin_dir="$(venv_bin_dir "$VENV_DIR")"
    ensure_gpu_torch "$bin_dir/pip" "$bin_dir/python"

    echo
    echo "Installed. Run pipeline scripts via:"
    echo "  $bin_dir/python -m src.sync"
    echo "  $bin_dir/python scripts/full_pipeline.py"
}

# pip's default torch wheel is built against whatever CUDA major version
# is current upstream (cu130 as of torch 2.13) -- it silently falls back
# to CPU-only at runtime (`torch.cuda.is_available() == False`, no error)
# on any host whose NVIDIA driver predates that CUDA version, which is
# common: driver upgrades lag well behind PyTorch releases, and a driver
# reporting e.g. "CUDA Version: 12.5" (`nvidia-smi`'s ceiling, not a
# minimum) cannot run a cu130 wheel. Found by hand on an A40 host with
# driver 555.42.02: sentence-transformers/docling/bertopic all installed
# clean, but silently ran on CPU. Older CUDA-tagged wheels (e.g. cu124)
# work fine on newer drivers -- CUDA is backward compatible within a
# driver's supported range -- so on a GPU host we detect the driver's
# ceiling and reinstall from the newest wheel tag at or under it, instead
# of leaving a GPU host silently CPU-bound.
ensure_gpu_torch() {
    local pip="$1"
    local python_bin="$2"

    if ! command -v nvidia-smi >/dev/null 2>&1; then
        return  # no GPU on this host -- default (CPU) wheel is correct as-is
    fi

    if "$python_bin" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
        echo "torch already sees the GPU (driver supports its bundled CUDA build)."
        return
    fi

    local driver_cuda
    driver_cuda="$(nvidia-smi 2>/dev/null | grep -oP 'CUDA Version:\s*\K[0-9]+\.[0-9]+' | head -1)"
    if [ -z "$driver_cuda" ]; then
        echo "Warning: nvidia-smi is present but its CUDA ceiling couldn't be" >&2
        echo "parsed -- leaving torch as installed (may be CPU-only)." >&2
        return
    fi

    # Only the single newest tag at or under the driver's ceiling is
    # attempted, not a cascade down through older ones: this is a
    # reinstall, so an attempt that "succeeds" (exit 0) but still doesn't
    # detect the GPU would otherwise leave torch progressively
    # downgraded, one tag older each time -- worse than the CPU-only
    # default it started from, while still printing a "just CPU" warning
    # that undersells how far it drifted.
    local tag tag_ver best_tag="" best_ver=""
    for tag in cu130 cu129 cu128 cu126 cu124 cu121 cu118; do
        tag_ver="${tag#cu}"
        tag_ver="${tag_ver:0:-1}.${tag_ver: -1}"
        if [ "$(awk -v a="$driver_cuda" -v b="$tag_ver" 'BEGIN{print (b<=a)?1:0}')" = "1" ]; then
            best_tag="$tag"
            best_ver="$tag_ver"
            break
        fi
    done

    if [ -z "$best_tag" ]; then
        echo "Warning: driver's CUDA ceiling (${driver_cuda}) is older than every" >&2
        echo "torch CUDA wheel tag this script knows about. Leaving the default" >&2
        echo "wheel installed (CPU-only on this driver) -- upgrade the NVIDIA" >&2
        echo "driver, or install a matching torch build by hand from" >&2
        echo "https://pytorch.org/get-started/locally/." >&2
        return
    fi

    echo "GPU present but torch can't see it (driver's CUDA ceiling is" \
         "${driver_cuda}, older than the default wheel's build). Reinstalling" \
         "from the ${best_tag} wheel index (needs driver CUDA <= ${best_ver}) ..."

    # --force-reinstall, and no --extra-index-url: pip's "upgrade only if
    # needed" logic otherwise compares bare version numbers (e.g. 2.13.0
    # vs 2.6.0+cu124) and treats the already-installed default wheel as
    # newer, silently keeping it -- discovered by hand when the first cut
    # of this reported success without ever actually swapping the wheel.
    # Restricting to a single cu-tagged index is also required, not just
    # tidy: it's what forces pip to resolve torch's own pure-Python deps
    # (numpy, sympy, jinja2, ...) from that same CUDA-tagged set rather
    # than drifting back to plain PyPI's higher-numbered ones. Output is
    # left unsilenced (unlike the rest of this script's pip calls aren't
    # either) -- an install failure here needs to be as debuggable as any
    # other, not swallowed just because it's a fallback path.
    if "$pip" install --force-reinstall \
        --index-url "https://download.pytorch.org/whl/${best_tag}" \
        "torch" "torchvision"; then
        if "$python_bin" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
            echo "torch now sees the GPU via the ${best_tag} wheel."
            return
        fi
    fi

    echo "Warning: reinstalling torch from ${best_tag} didn't make the GPU" >&2
    echo "visible. Restoring the default wheel via 'poetry install --with heavy'" >&2
    echo "(CPU-only on this driver, but at least back to a known, lockfile-" >&2
    echo "tracked state) ..." >&2
    (cd "$REPO_ROOT" && poetry install --with heavy)
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
        # unzip has no tar-style --strip-components: passing it as an
        # unzip flag is silently treated as a filename filter that
        # matches nothing, so it extracts zero files (found by hand: the
        # first cut of this line "succeeded" but left GROBID_DIR empty).
        # GitHub's source zip wraps everything in one top-level
        # "grobid-<version>/" dir, so extract to a scratch dir and move
        # its contents up into GROBID_DIR ourselves.
        local tmp_extract
        tmp_extract="$(mktemp -d)"
        unzip -q /tmp/grobid.zip -d "$tmp_extract"
        shopt -s dotglob
        mv "$tmp_extract"/*/* "${GROBID_DIR}/"
        shopt -u dotglob
        rm -rf "$tmp_extract" /tmp/grobid.zip
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

install_dev_deps() {
    check_poetry
    local venv_dir="${VENV_DIR:-$REPO_ROOT/.venv-full}"
    local bin_dir
    bin_dir="$(venv_bin_dir "$venv_dir")"
    if [ ! -x "$bin_dir/pip" ]; then
        echo "No venv at ${venv_dir} -- run '$0 python-deps' first." >&2
        exit 1
    fi
    export VIRTUAL_ENV="$venv_dir"

    (cd "$REPO_ROOT" && poetry install --with dev)
    # `poetry install --with dev` re-resolves the whole lock file, not
    # just the newly-added group -- it's additive against what's already
    # installed (verified by hand: running this after `python-deps` did
    # not remove the heavy group), but it can still touch transitive
    # packages shared with the heavy group, torch included. Re-run the
    # same GPU check as python-deps rather than assume it's still fine.
    ensure_gpu_torch "$bin_dir/pip" "$bin_dir/python"

    echo
    echo "Installed. Run the test suite via:"
    echo "  ${bin_dir}/python -m pytest --cov=src --cov=scripts --cov-report=term-missing"
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
        dev-deps) install_dev_deps ;;
        all) install_os_deps; install_python_deps ;;
        *)
            echo "Unknown stage: $stage" >&2
            echo "Expected one of: os-deps, python-deps, grobid, dev-deps, all" >&2
            exit 1
            ;;
    esac
done

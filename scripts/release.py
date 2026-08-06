#!/usr/bin/env python3
"""Builds a distributable release/chitragupta-<version>.zip.

Version comes from pyproject.toml's [tool.poetry].version -- the single
source of truth for it (see that file's own comments on why Poetry here
is a lockfile/venv manager only, not a publishing mechanism; this script
doesn't change that, it just reads the version Poetry itself considers
current).

Bundles every git-tracked file (`git ls-files`, so .gitignore's exclusions
-- content/parsed/, papers/bibliography.bib, .venv-full/, etc. -- are
already handled) except:

- developer-only material not useful to someone consuming the pipeline
  rather than extending it: DEVELOPER.md, tests/, bench/ (parser
  wall-clock measurement against *this* host's own bib corpus, which a
  release consumer doesn't have), and this repo's own meta-tooling
  (.github/, .gitignore, AGENTS.md -- CI config, git config, and
  coding-agent guidance for developing *this* repo, none of it
  meaningful once unzipped elsewhere).
- content/ and papers/, which do have a handful of git-tracked files
  despite mostly being gitignored (e.g. content/drafts/*.md example
  drafts, papers/pdfs/manifest.json) -- per-host example/personal data
  that shouldn't ship as someone else's example in a fresh release.
  Shipped as empty placeholder directories instead of omitted outright,
  so config.toml's default paths (content.dir, source_pdfs.dir) still
  resolve to something that exists before a first `sync` run populates
  them.

Stdlib only (tomllib, zipfile, shutil) -- runs with bare `python3`, no
venv, same as citation_gate.py/references.py. Needs `git` on PATH to list
tracked files; nothing else.

Usage:
    python3 scripts/release.py
"""

import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Developer-only / this-repo-only material a release doesn't need to ship
# -- everything else git-tracks is fair game (see module docstring).
EXCLUDE_TOP_LEVEL = {"tests", "DEVELOPER.md", ".github", ".gitignore", "AGENTS.md", "bench"}

# Ships as an empty placeholder directory instead of its tracked contents
# -- see module docstring.
EMPTY_TOP_LEVEL = {"content", "papers"}


def get_version() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["poetry"]["version"]


def tracked_files() -> list[str]:
    """Every git-tracked path relative to REPO_ROOT, minus EXCLUDE_TOP_LEVEL
    and EMPTY_TOP_LEVEL (the latter ships as an empty directory instead --
    see build_release)."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    paths = [p for p in result.stdout.decode().split("\0") if p]
    skip = EXCLUDE_TOP_LEVEL | EMPTY_TOP_LEVEL
    return [p for p in paths if p.split("/", 1)[0] not in skip]


def build_release() -> tuple[Path, int]:
    version = get_version()
    name = f"chitragupta-{version}"
    release_dir = REPO_ROOT / "release"
    staging = release_dir / name

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    paths = tracked_files()
    for rel_path in paths:
        src = REPO_ROOT / rel_path
        dst = staging / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for empty_dir in EMPTY_TOP_LEVEL:
        (staging / empty_dir).mkdir(parents=True, exist_ok=True)

    zip_path = release_dir / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(staging.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(release_dir))
            elif f.is_dir() and not any(f.iterdir()):
                # zipfile.write() only carries a directory into the archive
                # via a file inside it -- EMPTY_TOP_LEVEL's placeholders
                # have none, so they need an explicit directory entry.
                zf.writestr(zipfile.ZipInfo(f"{f.relative_to(release_dir)}/"), "")

    shutil.rmtree(staging)
    return zip_path, len(paths)


def main() -> int:
    zip_path, n_files = build_release()
    print(f"Release archive: {zip_path} ({n_files} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

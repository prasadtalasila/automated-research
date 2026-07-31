# Running with Docker

`docker/` (Dockerfile + `docker/setup.sh`) builds the same GROBID/TeX
Live/Pandoc/Poetry stack inside a container, for hosts where the
user doesn't hold root permissions. There's nothing Docker-exclusive
about any individual piece -- `scripts/install_full_pipeline.sh` is the
single install path for both the host and this image (see
[GROBID.md](GROBID.md) for the bare-host equivalent of what this image
does for GROBID specifically).

**Untested end-to-end**: no Docker daemon has been available in any
environment this was developed in, so nothing below has actually been
built or run -- it's what `docker/Dockerfile` and `docker/setup.sh`
document, not something exercised. Validate before relying on it.

## Build

```bash
docker build -t research-pipeline -f docker/Dockerfile .
```

This runs `scripts/install_full_pipeline.sh` three times as separate,
independently cached layers -- `os-deps`, then `grobid`, then
`python-deps` (via Poetry, with `SKIP_VENV=1` so it installs into
`/opt/venv` instead of creating its own) -- so editing later Dockerfile
lines or unrelated repo files doesn't force earlier layers to rebuild.
**Exception**: the script itself is `COPY`'d once, before any of the
three stages run, so editing `scripts/install_full_pipeline.sh`
invalidates all three layers, including the multi-GB `grobid` one --
Docker's cache keys each layer on the exact command *and* any files that
command's `COPY` depends on, and this file feeds all of them. The
`grobid` layer alone is multi-GB and multi-minute; expect a long first
build.

## Run

Mount your repo and a volume for `content/` so it survives container
restarts:

```bash
docker run -it --rm \
    -v "$(pwd)":/workspace/automated-research \
    -v research-pipeline-content:/workspace/automated-research/content \
    research-pipeline
```

## Start GROBID and verify the toolchain

GROBID is built into the image but not started automatically. Inside the
running container:

```bash
docker exec -it <container> /usr/local/bin/setup-grobid.sh
```

This starts GROBID in the background and checks `latexmk`, `pandoc`,
and the `sentence-transformers`/`chromadb`/`bertopic` imports, printing
`OK`/`MISSING` for each.

## Running pipeline commands inside the container

The same commands as the main README's Quickstart work directly with no
venv prefix, since `/opt/venv` is already on `PATH` (and exported as
`VIRTUAL_ENV`, so Poetry installs into it rather than creating its own)
inside the container:

```bash
python -m src.sync
python scripts/full_pipeline.py --stages embed,bertopic
python -m src.citation_gate path/to/draft.md
```

To run the test suite inside the container, add the `dev` group:

```bash
SKIP_VENV=1 bash scripts/install_full_pipeline.sh dev-deps
python -m pytest --cov=src --cov=scripts --cov-report=term-missing
```

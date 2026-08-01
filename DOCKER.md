# Running with Docker

`docker/Dockerfile` builds the same TeX Live/Pandoc/Poetry stack inside
a container, for hosts where the user doesn't hold root permissions.
There's nothing Docker-exclusive about any individual piece --
`scripts/install_full_pipeline.sh` is the single install path for both
the host and this image.

**Untested end-to-end**: no Docker daemon has been available in any
environment this was developed in, so nothing below has actually been
built or run -- it's what `docker/Dockerfile` and `docker/setup.sh`
document, not something exercised. Validate before relying on it.

## Build

```bash
docker build -t research-pipeline -f docker/Dockerfile .
```

This runs `scripts/install_full_pipeline.sh` twice as separate,
independently cached layers -- `os-deps`, then `python-deps` (via
Poetry, with `SKIP_VENV=1` so it installs into `/opt/venv` instead of
creating its own) -- so editing later Dockerfile lines or unrelated repo
files doesn't force earlier layers to rebuild.
**Exception**: the script itself is `COPY`'d once, before either of the
two stages runs, so editing `scripts/install_full_pipeline.sh`
invalidates both layers --
Docker's cache keys each layer on the exact command *and* any files that
command's `COPY` depends on, and this file feeds both of them. The
`python-deps` layer pulls torch and Docling's models; expect a long
first build.

## Run

Mount your repo and a volume for `content/` so it survives container
restarts:

```bash
docker run -it --rm \
    -v "$(pwd)":/workspace/automated-research \
    -v research-pipeline-content:/workspace/automated-research/content \
    research-pipeline
```

## Verify the toolchain

Inside the running container, check that the render and heavy-pipeline
dependencies actually resolved:

```bash
command -v latexmk pandoc pdftotext
python -c "import sentence_transformers, chromadb, bertopic, docling; print('heavy group OK')"
```

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

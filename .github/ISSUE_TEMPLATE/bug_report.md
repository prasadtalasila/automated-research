---
name: Bug report
about: Report a defect to help improve the software
title: "[BUG]"
labels: bug
assignees: ''

---

## Describe the Bug

A clear and concise description of what the bug is.

## Steps to Reproduce

The exact commands run, in order, and where it went wrong:

1. `python -m src.sync` / a genre skill invoked as '...'
1. See error

## Expected Behaviour

A clear and concise description of what was expected to happen.

## Actual Output

Paste the relevant output. For a `sync` problem, `logs/sync.log` carries the
per-document progress and warnings that stdout does not.

**Do not paste your bibliography or any PDF text** -- `papers/` and
`content/` are per-host, and a citekey plus the failing line is enough.

## Application Environment

- OS: [e.g. Ubuntu 24.04]
- Python version: [`python3 --version`]
- chitragupta version: [`pyproject.toml`'s `[tool.poetry].version`, e.g. 3.5.0]
- Parser backend: [`pdftotext` (default) or `docling`, per `config.toml`]
- `[parser].workers`: [e.g. 1, the default]
- Installed via: [`scripts/install_full_pipeline.sh` stages run, or Docker]
- Enrichment layer installed: [yes / no]

## Additional Context

Any other context about the problem.

# CLAUDE.md

Guidance for working in this repository specifically.

## The hard invariant: never fabricate a citekey

`papers/DT-Simulation-Patterns/main.bib` (a sibling project on this same
machine) already contains entries a prior review marked
`WARNING: UNVERIFIABLE` -- fabricated placeholder references that made it
into a real paper. That is the failure mode this pipeline is built to
prevent.

Rule: a citekey may only be used if it was returned by
`src.retrieval.search()` (backed by `content/ledger.sqlite`, which is
populated only from what `python -m src.sync` actually read out of Zotero).
If a citation would help an argument but isn't in the synced library, say so
in prose to the user -- do not invent a key for it, and do not "fix" a gate
failure by making up a plausible-looking key instead of removing the claim
or sourcing a real one.

Every genre skill (`survey-writer`, `thesis-chapter-writer`, `tutorial-writer`
in `.claude/skills/`) must run `python -m src.citation_gate <file>` on its own
output and only present the draft once it exits 0. This is a gate, not a
lint suggestion -- treat a `FAIL` the same way you'd treat a failing test.

## Two-job split

- **Job 1 -- deterministic pipeline** (`python -m src.sync`): Zotero read ->
  ledger update -> PDF text extraction -> `.bib` export. No LLM calls, no
  judgment calls, idempotent. Safe to run unattended or on a schedule. If you
  change `src/zotero_reader.py`'s citekey algorithm, re-run `sync` and expect
  the ledger to pick up the new keys (old ones aren't renamed retroactively --
  check `content/ledger.sqlite` if that matters for a given task).
- **Job 2 -- generative drafting** (the three `.claude/skills/`): invoked on
  demand, reviewed by the user, genre-specific. These read the content layer;
  they never write to `content/ledger.sqlite` or `content/library.bib`
  directly (only `sync` does).

## Environment constraints on this host

No root/sudo, no Java, no TeX Live, no Pandoc; `pip install` outside a venv
is blocked (PEP 668). The core pipeline (`src/`) is stdlib-only and pdftotext
by design -- don't add a dependency to `src/` that isn't in the standard
library without checking whether it actually needs a venv first. Heavier
deps (sentence-transformers, chromadb, bertopic, bibtexparser) belong in
`docker/requirements-full.txt`, installed only inside the container in
`docker/`, which is scaffolded but has not been built or run in this
environment (no Docker daemon here) -- treat it as a draft to validate, not
a tested artifact.

Zotero itself is real and running on this host at `/home/TestUserDTaaS/Zotero`
(override via `ZOTERO_DATA_DIR`). No Better BibTeX plugin is installed --
`src/zotero_reader.py` reads `zotero.sqlite` directly (`mode=ro&immutable=1`,
safe even while Zotero is open) instead of relying on a BBT auto-export. If
BBT gets installed later its citekey convention (author+year+titleword) is
compatible with the one generated here, so nothing downstream needs to
change on that account.

## Retrieval is a placeholder

`src/retrieval.py` does keyword overlap, not embeddings -- there are 2 items
in the library right now, and installing a vector-embedding stack for that
would be overhead with nothing to show for it. Its `search(query, k)` return
type is the contract the genre skills use; if/when the corpus grows and the
Docker path's embedding stack is worth using, swap the implementation but
keep the signature so the skills don't need to change.

## Don't touch `papers/`

`papers/DT-Simulation-Patterns/` is a separate, already-written paper (not
this pipeline's input or output) -- it happens to sit on the same machine
and is referenced above only as a cautionary example and, in
`thesis-chapter-writer`, as a LaTeX structural reference. Don't ingest it
into `content/` or treat it as part of this repo's corpus.

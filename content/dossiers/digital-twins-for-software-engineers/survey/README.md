# Dossier: digital-twins-for-software-engineers/survey

The working state that produced `content/drafts/digital-twins-for-software-engineers/survey.md` -- what a later
session needs in order to revise it without re-running the drafting
pipeline. Genre: survey.

| File | What it holds |
|---|---|
| `scope.md` | reader, what the draft covers and excludes, glossary, corpus fingerprint |
| `evidence.md` | each citekey kept, why, and the supporting quote or paraphrase |
| `rejected.md` | candidates retrieved and turned down, with the reason |
| `sections.md` | section heading -> the citekeys cited under it |
| `steering.md` | what the user asked for in chat that the draft doesn't show |
| `revisions.md` | append-only log of what changed and why |

This directory is gitignored, like the draft it describes. Back it up and
restore it with:

    python3 -m src.dossier export digital-twins-for-software-engineers/survey
    python3 -m src.dossier restore <archive.tar.gz> --force

A bundle carries drafts and dossiers, not the corpus: `content/ledger.sqlite`
is regenerable with `python -m src.sync`, and `papers/bibliography.bib` is
your reference manager's export, which belongs in that tool's backup rather
than in a copy this pipeline keeps.

See `docs/DRAFT-ITERATION.md`.

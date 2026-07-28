---
name: thesis-chapter-writer
description: Drafts a thesis/dissertation chapter in LaTeX, with narrative framing tied to a specific research question, grounded in citekeys pulled from the Zotero-synced corpus (content/ledger.sqlite via src.retrieval.search()) -- never a fabricated one. Triggers when the user asks to write or draft a thesis chapter, dissertation section, or an RQ-driven narrative chapter. Outputs a standalone .tex fragment (\citep/\citet, no document preamble) intended to be \input by the user's own thesis document. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses if the ledger is empty until `python -m src.sync` has been run.
tags: [thesis, dissertation, latex, citation, zotero]
---

# thesis-chapter-writer

Genre-specific drafting agent for thesis-chapter output. Job 2 (generative,
on-demand, user-reviewed) in the two-job pipeline split -- distinct from
`python -m src.sync` (job 1, deterministic, unattended-safe).

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `bibliography.bib` (repo root) -- the source of truth for citekeys/metadata;
  point the thesis document's `\addbibresource` (biblatex) or `\bibliography`
  (bibtex) at this file directly rather than a copy
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- `search(query, k)` returns `SearchResult(citekey, title, score, snippet)`

If the ledger is empty or stale, run `python -m src.sync` first and report
what it found before drafting.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a thesis chapter / dissertation section tied to an RQ | Invoke this skill |
| User asks for a survey paper / lit review, not chapter-specific | Use `survey-writer` instead |
| User asks for teaching material | Use `tutorial-writer` instead |
| Ledger empty or stale | Run `python -m src.sync`, report, then proceed |

## Process

1. **Clarify the research question** the chapter serves, if not already given
   by the user. The chapter's narrative arc should argue toward/around this RQ,
   not just summarize papers in sequence.
2. **Retrieve.** Call `src.retrieval.search()` against the RQ and its component
   concepts. This is keyword overlap, not embeddings -- verify snippets
   yourself rather than trusting the ranking.
3. **Draft** as a LaTeX fragment (no `\documentclass`/`\begin{document}` --
   this is `\input`-ed into the user's existing thesis document):
   - Section/subsection structure that builds an argument toward the RQ
   - Citations via `\citep{key}` / `\citet{key}` — never a bare invented key
   - Where the existing `papers/DT-Simulation-Patterns/main.tex` /
     `IEEEtran.cls` in this workspace is structurally relevant as a formatting
     reference, follow its conventions for consistency; it is a *reference*,
     not something to copy content from
4. **Never write a citekey you didn't get from `search()`.** If a citation
   would strengthen the argument but isn't in the synced library, tell the
   user in prose rather than inventing a key. This project's own
   `papers/DT-Simulation-Patterns/main.bib` already has entries marked
   `WARNING: UNVERIFIABLE` from a past fabrication incident -- that failure
   mode is exactly what this rule exists to prevent.
5. **Log provenance.** Write `content/provenance/<slug>.json`:
   `{"section": "...", "citekeys": [...]}` per section, for later audit.
6. **Gate before presenting.**
   ```
   python -m src.citation_gate <draft.tex>
   ```
   Fix and re-run until `OK`. Never present a draft that hasn't passed.
7. This machine has no TeX Live installed -- do not attempt to compile the
   `.tex` output. Present it as source for the user to `\input` and compile
   themselves (or use the Docker path in `docker/` which does have LaTeX).

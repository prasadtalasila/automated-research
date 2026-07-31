---
name: thesis-chapter-writer
description: Drafts a thesis/dissertation chapter in LaTeX, with narrative framing tied to a specific research question, grounded in citekeys pulled from the synced corpus (content/ledger.sqlite via src.retrieval.search()) -- never a fabricated one. Triggers when the user asks to write or draft a thesis chapter, dissertation section, or an RQ-driven narrative chapter. Outputs a standalone .tex fragment (\citep/\citet, no document preamble) intended to be \input by the user's own thesis document, plus a rendered .md/.pdf preview when pandoc/pdflatex are available. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses if the ledger is empty until `python -m src.sync` has been run.
tags: [thesis, dissertation, latex, citation]
---

# thesis-chapter-writer

Genre-specific drafting agent for thesis-chapter output. Job 2 (generative,
on-demand, user-reviewed) in the two-job pipeline split -- distinct from
`python -m src.sync` (job 1, deterministic, unattended-safe).

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `papers/bibliography.bib` (gitignored, per-host) -- the source of truth for citekeys/metadata;
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
2. **Retrieve broadly, then filter.** Call `src.retrieval.search(query, k=15)`
   against the RQ and its component concepts -- over-fetch rather than
   assuming the top few hits are automatically the right ones. This is
   keyword overlap, not embeddings -- read each 500-character snippet and
   judge relevance yourself; a high score is a proxy, not a verdict. Keep
   only what actually supports part of the argument; write the kept set to
   `content/provenance/<slug>-evidence.json` (citekey + why it's relevant +
   the supporting quote/paraphrase) before drafting prose.
3. **Reformulate and re-search if a concept comes up thin.** Try synonyms
   or adjacent terms and search again before concluding the corpus doesn't
   cover something -- and if it genuinely doesn't after a real attempt, say
   so to the user rather than forcing a weak citation into the argument.
4. **Check for disagreement across kept sources.** If two sources conflict
   on a point relevant to the RQ, surface that explicitly in the chapter
   rather than silently picking a side.
5. **Draft** as a LaTeX fragment (no `\documentclass`/`\begin{document}` --
   this is `\input`-ed into the user's existing thesis document), citing
   only from your scored-evidence file:
   - Section/subsection structure that builds an argument toward the RQ
   - Citations via `\citep{key}` / `\citet{key}` — never a bare invented key
6. **Never write a citekey you didn't get from `search()`.** If a citation
   would strengthen the argument but isn't in the synced library, tell the
   user in prose rather than inventing a key -- see AGENTS.md's citekey
   invariant (fabricated placeholder references are exactly the failure
   mode this rule exists to prevent).
7. **Log provenance.** Write `content/provenance/<slug>.json`:
   `{"section": "...", "citekeys": [...]}` per section, for later audit (in
   addition to the evidence file from step 2).
8. **Gate before presenting.** Save the fragment as `content/drafts/<slug>.tex`
   (this remains the canonical deliverable -- the one meant to be `\input`-ed),
   then run:
   ```
   python -m src.citation_gate content/drafts/<slug>.tex
   ```
   Fix and re-run until `OK`. Never present a draft that hasn't passed.
9. **Render md and pdf previews.** The `.tex` fragment stays the canonical
   deliverable exactly as-is -- don't wrap it in a preamble or change its
   `\input`-able shape. In addition, render an `.md` and a `.pdf` preview
   from that same fragment (pandoc's LaTeX reader handles a preamble-less
   fragment fine):
   ```
   python3 -m src.heavy.render_output content/drafts/<slug>.tex --format md
   python3 -m src.heavy.render_output content/drafts/<slug>.tex --format pdf
   ```
   This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH -- don't
   assume either is present or absent without checking; probe (or just try
   the command and read the result) rather than assuming from a prior run
   on a different host. If either command reports `[missing-binary]` or
   `[error]`, print a one-line warning in chat with that message and
   continue anyway -- a rendering failure never blocks presenting the
   `.tex` fragment.

   Unlike the Markdown-native genre skills, don't run `python -m
   src.references` on this fragment and don't add a manual References
   section to it -- the fragment is designed to inherit the thesis's own
   document-wide `\addbibresource`/`\bibliography` (step 1's shared
   content layer), and a per-chapter list would duplicate that. The `.pdf`
   preview still gets a real bibliography for free: `--citeproc` resolves
   `\citep`/`\citet` against `bibliography.bib` and appends one
   automatically, same as before this feature existed.
10. Present the `.tex` fragment (the deliverable to `\input`) plus, if
    rendering succeeded, the `.md`/`.pdf` preview paths -- or the warning
    if it didn't.

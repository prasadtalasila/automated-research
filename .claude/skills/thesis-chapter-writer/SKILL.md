---
name: thesis-chapter-writer
description: Drafts a thesis/dissertation chapter in LaTeX, with narrative framing tied to a specific research question, grounded in citekeys pulled from the synced corpus (content/ledger.sqlite via src.retrieval.search()) -- never a fabricated one. Triggers when the user asks to write or draft a thesis chapter, dissertation section, or an RQ-driven narrative chapter. To change one that already exists in content/drafts/, use draft-reviser instead -- never re-run this skill to make a change. Outputs a standalone .tex fragment (\citep/\citet, no document preamble) intended to be \input by the user's own thesis document, plus a rendered .md/.pdf preview when pandoc/pdflatex are available. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses if the ledger is empty until `python -m src.sync` has been run.
tags: [thesis, dissertation, latex, citation]
---

# thesis-chapter-writer

Genre-specific drafting agent for thesis-chapter output. The drafting
layer (generative, on-demand, user-reviewed) -- distinct from
`python -m src.sync` (the corpus layer: deterministic, unattended-safe).

## Shared corpus layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `papers/bibliography.bib` (gitignored, per-host) -- the source of truth for citekeys/metadata;
  point the thesis document's `\addbibresource` (biblatex) or `\bibliography`
  (bibtex) at this file directly rather than a copy
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- `search(query, k)` returns `SearchResult(citekey, title, score, snippet)`

## The dossier: write down what produced the draft

The chapter is only half of what this run produces. The other half is the
judgment behind it -- the examiner you wrote for, the scope, the
terminology the chapter settled on, which candidates were kept and
**which were turned down and why** -- and it belongs on disk, not in this
conversation. Without it the next revision has to re-retrieve and
re-score the whole research question to change one paragraph.

`src/dossier.py` owns that state, in Markdown, one directory per draft at
`content/dossiers/<the draft's path, minus its suffix>/`. Create it before
you search (step 0) and fill it in as you go -- not at the end, when what
you rejected has already fallen out of your context. `docs/DRAFT-ITERATION.md`
is the full design.

This skill writes both a dossier and `content/provenance/<slug>.json`,
and keeps both: the provenance JSON is the machine-readable
section-to-citekey record used for audit, while the dossier is the
human-readable working state a later revision reads (reader, scope,
glossary, rejected candidates and why, steering).

**Read-only means read-only: never run `python -m src.sync`, and never
run `scripts/enrich.py` or any `src/enrich/*` stage.** Both belong to the
corpus layer, both take the pipeline's write lock, and either can run for
tens of minutes -- a first full-corpus parse, or building the embedding
index. They are the user's to run, not yours. If a semantic index would
help and none exists, say so and use `src.retrieval.search()`; do not
build one.

**If the ledger is empty, stop.** Check before drafting anything:

```bash
python3 -m src.ledger
```

If it reports no items, or none with status `parsed`, say so plainly --
name what you checked and what you found -- and stop there. Do not draft
around it, do not sync, do not cite. Tell the user to run
`.venv-full/bin/python -m src.sync` and come back.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a thesis chapter / dissertation section tied to an RQ | Invoke this skill |
| User asks for a survey paper / lit review, not chapter-specific | Use `survey-writer` instead |
| User asks for a textbook chapter / lecture notes | Use `textbook-chapter-writer` instead |
| User asks for a hands-on tutorial | Use `tutorial-writer` instead |
| User asks to change a chapter that **already exists** in `content/drafts/` | Use `draft-reviser` instead -- never re-run this skill to make a change |
| Ledger is empty, or nothing is `parsed` | Say so and stop. **Never** run `src.sync` yourself |

## Prose standards

Follow `docs/WRITING-STANDARDS.md` for the cross-genre rules: name the reader
before drafting, define terms once, state scope up front, active voice with a
named actor, ban "obviously/simply/just", and reread as the reader before
presenting. That file also carries the attribution for where these
principles come from -- Diátaxis, Last's *Technical Writing Essentials*, and
Google's Technical Writing courses, all CC-licensed and all requiring credit.
The genre-specific additions below layer on top of it.

### What a thesis chapter owes its reader

Its reader is an examiner: a domain expert reading adversarially, looking for
the claim that outruns its evidence. That shapes every rule below. Unlike the
survey genre, this chapter **does** take a position -- but every step of the
argument must be traceable to something cited, and the honest statement of a
limitation is worth more than the paragraph that hides it.

The genre boundary that matters most here: a chapter that only summarizes
papers in sequence is a survey with a chapter heading. If the argument toward
the RQ isn't visible in the section structure, the chapter isn't doing its
job -- see `docs/WRITING-STANDARDS.md` §5.

## Process

0. **Name the reader's starting point, and open the dossier.** Before
   searching, settle what an examiner in this subfield already knows, so
   background is recapped where it's genuinely needed and not where it's
   condescending. Settle too what the chapter will and won't cover, and
   the slug it will be saved under. Then create the dossier and record
   the same decisions there:
   ```
   python3 -m src.dossier init content/drafts/<slug>.tex --genre thesis-chapter
   ```
   Fill in `scope.md`'s **Reader**, **Covers**, **Does not cover** and
   **Glossary** now, while you are deciding them -- the glossary is where
   the chapter's terminology gets pinned, so a revision doesn't drift off
   it. `init` also stamps the corpus fingerprint, which is what lets a
   later revision tell whether the ledger has moved since.
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
   the supporting quote/paraphrase) before drafting prose. Record the same
   judgment in the dossier while the snippets are still in front of you --
   the kept citekeys into `evidence.md`, and every candidate you turned
   down into `rejected.md` with the query that surfaced it and a few words
   on why ("shares vocabulary only", "wrong domain", "superseded by X"),
   so the next revision doesn't re-judge the same papers.
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
8. **Map sections to citekeys in the dossier.** Fill in the dossier's
   `sections.md` -- one row per section heading with the citekeys cited
   under it -- so a later revision can tell which section owns a citation
   without reading the fragment.
   `python3 -m src.dossier sections content/drafts/<slug>.tex` prints the
   headings and their line ranges to build it from; it tracks
   `verbatim`/`lstlisting`/`minted`, so a
   `\section`-like line inside a code environment won't show up as a
   heading. This is the same mapping as step 7's provenance JSON, kept in
   the form a reviser reads.
9. **Gate before presenting.** Save the fragment as `content/drafts/<slug>.tex`
   (this remains the canonical deliverable -- the one meant to be `\input`-ed),
   then run:
   ```
   python -m src.citation_gate content/drafts/<slug>.tex
   ```
   Fix and re-run until `OK`. Never present a draft that hasn't passed.
10. **Render md and pdf previews.** The `.tex` fragment stays the canonical
    deliverable exactly as-is -- don't wrap it in a preamble or change its
    `\input`-able shape. In addition, render an `.md` and a `.pdf` preview
    from that same fragment (pandoc's LaTeX reader handles a preamble-less
    fragment fine):
    ```
    python3 -m src.render_output content/drafts/<slug>.tex --format md
    python3 -m src.render_output content/drafts/<slug>.tex --format pdf
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
    document-wide `\addbibresource`/`\bibliography` (the shared corpus
    layer above), and a per-chapter list would duplicate that. The `.pdf`
    preview still gets a real bibliography for free: `--citeproc` resolves
    `\citep`/`\citet` against `bibliography.bib` and appends one
    automatically, same as before this feature existed.

    Note the preview renders that bibliography in IEEE style, with numeric
    `[1]` markers, because that is what `render_output` now passes
    `--csl`. That styles the *preview only* -- the `.tex` fragment is
    unchanged, and the real thesis renders it in whatever style its own
    document class and `\bibliographystyle` specify. Don't rewrite
    `\citep`/`\citet` to match the preview.
11. **Read it once as the examiner** (`docs/WRITING-STANDARDS.md` §6, in its
    adversarial form). Check specifically for: a conclusion stated more
    strongly than its cited evidence supports, a section that summarizes
    rather than argues, notation or terminology that shifts mid-chapter, and
    any claim carrying no citation that isn't genuinely your own contribution.
    Where you find overreach, weaken the claim rather than adding a citation
    that doesn't quite support it.
12. **Record any steering.** If the user shaped this chapter in chat --
    "argue it harder against X", "the RQ is narrower than that", "cut the
    background recap" -- append it to the dossier's `steering.md`, dated.
    It is invisible in the prose and has nowhere else to live; a revision
    that doesn't know about it will undo it.
13. Present the `.tex` fragment (the deliverable to `\input`) plus, if
    rendering succeeded, the `.md`/`.pdf` preview paths -- or the warning
    if it didn't. Tell the user where the dossier is, that changes to this
    chapter should go through `draft-reviser` rather than another run of
    this skill, and that `content/drafts/` and `content/dossiers/` are
    gitignored -- so `python3 -m src.dossier export <slug>` is how a draft
    and its working state get backed up.

## Sources

The prose standards this skill inherits are not original to this project.

Full citations, licences and a per-principle attribution table are in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
All three works are openly licensed (CC-BY or CC-BY-SA) and require credit.

What bears on *this* genre specifically:

- **Google, *Technical Writing Courses* (CC-BY 4.0)** -- the curse of
  knowledge, consistent terminology, defining each term once. Step 0's "what
  does an examiner already know" is audience analysis in the form this genre
  needs it.
- **Last, *Technical Writing Essentials* (CC-BY 4.0)** -- scope and assumed
  background stated up front; the argument against passive voice.
- **Procida, *Diátaxis* (CC-BY-SA 4.0)** -- the genre-separation principle
  behind the warning that a chapter which only summarises papers in sequence
  is a survey with a chapter heading. A thesis chapter is not a Diátaxis
  quadrant; only that insight transfers.

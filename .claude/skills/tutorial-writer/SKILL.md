---
name: tutorial-writer
description: Drafts an undergraduate-level tutorial chapter -- learning objectives, motivation, worked examples, exercises -- a distinct pedagogical register from the survey/thesis genres. May cite grounding papers from the synced corpus (content/ledger.sqlite via src.retrieval.search()) for motivation/background, but is not citation-dense; most content is original worked examples and exercises. Triggers when the user asks to draft a tutorial chapter, lecture notes, teaching material, or worked-examples handout for students. Any citations it does include must pass `python -m src.citation_gate` before the draft is presented -- never a fabricated citekey.
tags: [tutorial, teaching, undergraduate, pedagogy]
---

# tutorial-writer

Genre-specific drafting agent for tutorial-chapter output -- the one genre
with no equivalent in typical academic-paper writing tools, since its register
(teaching, not persuading a reviewer) is fundamentally different from
`survey-writer` and `thesis-chapter-writer`. Job 2 (generative, on-demand,
user-reviewed) in the two-job pipeline split.

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/parsed/<citekey>.txt` -- extracted PDF text, useful for pulling a
  real worked example or dataset description from a paper if relevant
- `src/retrieval.py` -- `search(query, k, snippet_chars)` if you want to
  ground the motivation section in the corpus (citing the result is still
  optional -- see step 2)

Citations here are optional, not the point. Don't force them in.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a tutorial chapter / lecture notes / teaching material for students | Invoke this skill |
| User asks for a survey or lit review | Use `survey-writer` instead |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |

## Process

1. **Establish the learning objectives** first -- 3-5 concrete "by the end of
   this chapter, students will be able to..." statements. Let everything else
   serve these.
2. **Motivation.** A short section on why this topic matters, pitched at an
   undergraduate who has not read the literature. If you search the synced
   corpus for a motivating example, use the same retrieval discipline as
   the other skills: over-fetch (`src.retrieval.search(query, k=15)`), read
   each 500-character snippet yourself rather than trusting the score, and
   reformulate and search again if the first pass turns up nothing genuinely
   useful -- don't settle for a weak match just because it was the top hit.
   **Whether to cite at all stays optional here, unlike the other skills.**
   Finding a good example doesn't obligate a citation -- cite it (`[@citekey]`)
   only if attributing it to a specific paper actually helps the student
   (e.g. "this is a real system described in [@citekey]"); otherwise let it
   inform a well-chosen analogy without a reference. Don't manufacture a
   citation just to have one, and don't feel obligated to search at all if
   you already have a good example. Anything you do cite still must be a
   real citekey from a `search()` result -- never a fabricated one.
3. **Worked example(s).** Concrete, step-by-step, with enough detail a student
   could reproduce it. Prefer originally-constructed examples suited to the
   target course level over lifting directly from a paper's (likely more
   advanced) treatment.
4. **Exercises.** Include a mix of difficulty, and either solutions or hints
   -- state which. Exercises should exercise the stated learning objectives,
   not just the reading.
5. **Never write a citekey you didn't get from `search()`.** If you do include
   any citations, save the draft as `content/drafts/<slug>.md` and gate it:
   ```
   python -m src.citation_gate content/drafts/<slug>.md
   ```
   Fix and re-run until `OK` before presenting. If there are no citations at
   all, the gate step is unnecessary — just save to `content/drafts/<slug>.md`.
6. **Render tex and pdf.** Once saved (and gated, if it has citations), also
   render the other two formats:
   ```
   python3 -m src.heavy.render_output content/drafts/<slug>.md --format tex
   python3 -m src.heavy.render_output content/drafts/<slug>.md --format pdf
   ```
   This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH — no
   heavy venv required. If either command reports `[missing-binary]` or
   `[error]`, print a one-line warning in chat with that message and
   continue anyway — a rendering failure never blocks presenting the
   `.md` draft. Report the render outcome (paths to the `.tex`/`.pdf` if
   they succeeded, or the warning if not) alongside the draft.

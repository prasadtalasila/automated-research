---
name: survey-writer
description: Drafts a topic-clustered literature survey / background section / "state of the art" from the synced corpus, with a comparison table and a gap analysis. Every claim is grounded in a citekey pulled from content/ledger.sqlite via src.retrieval.search() -- never a fabricated one. Triggers when the user asks to write, draft, or update a survey paper, literature review, background section, or related-work section for a given topic. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses (and tells the user to run `python -m src.sync` first) if the ledger is empty.
tags: [survey, literature-review, citation]
---

# survey-writer

Genre-specific drafting agent for survey-style output. This is the "generative
drafting" half of the pipeline (the drafting layer) -- it runs on demand and its
output is reviewed by the user, unlike `python -m src.sync` (the corpus
layer: deterministic, safe to run unattended).

## Shared corpus layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `papers/bibliography.bib` (gitignored, per-host) -- the source of truth for citekeys/metadata;
  `sync` reads it, it is never regenerated
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- two-stage: `python3 -m src.retrieval triage "<q>" --k 15`
  to rule candidates out, then `... evidence "<q>" --citekey <key>` on the
  survivors. `search` (one-stage, 500-char snippets) still exists for a
  narrow lookup where you expect to keep most of what you get

## The dossier: write down what produced the draft

The draft is only half of what this run produces. The other half is the
judgment behind it -- the reader, the scope, the glossary, which
candidates were kept and **which were turned down and why** -- and it
belongs on disk, not in this conversation. Without it the next revision
has to re-retrieve and re-score the whole topic to change one paragraph.

`src/dossier.py` owns that state, in Markdown, one directory per draft at
`content/dossiers/<the draft's path, minus its suffix>/`. Create it before
you search (step 0) and fill it in as you go -- not at the end, when what
you rejected has already fallen out of your context. `docs/DRAFT-ITERATION.md`
is the full design.

**Read-only means read-only: never run `python -m src.sync`.** That command
belongs to the corpus layer, it takes the pipeline's write lock, and a
first full-corpus parse can run for tens of minutes. It is the user's to
run, not yours.

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
| User asks for a survey / lit review / background / related-work section on topic X | Invoke this skill |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |
| User asks for a textbook chapter / lecture notes / worked examples | Use `textbook-chapter-writer` instead |
| User asks for a hands-on tutorial the reader follows at a keyboard | Use `tutorial-writer` instead |
| User asks to change a survey that **already exists** in `content/drafts/` | Use `draft-reviser` instead -- never re-run this skill to make a change |
| Ledger is empty, or nothing is `parsed` | Say so and stop. **Never** run `src.sync` yourself |

## Prose standards

Follow `docs/WRITING-STANDARDS.md` for the cross-genre rules: name the reader
before drafting, define terms once, state scope up front, active voice with a
named actor, ban "obviously/simply/just", and reread as the reader before
presenting. That file also carries the attribution for where these
principles come from -- Diátaxis, Last's *Technical Writing Essentials*, and
Google's Technical Writing courses, all CC-licensed and all requiring credit.
The genre-specific additions below layer on top of it.

### What a survey owes its reader

A survey's job is to **organize a field and locate the gaps in it** -- not to
teach the topic and not to advocate a position. Two failure directions:

- **Drifting into a textbook chapter.** You start explaining the concepts for
  a reader who doesn't know them. A survey's reader is a researcher entering
  the area; they need the map, not the lesson. If the user actually wants the
  lesson, `textbook-chapter-writer` exists -- say so.
- **Drifting into an argument.** You start defending one approach as correct.
  Weighing alternatives is the deliverable here; picking a winner is the
  thesis chapter's job, and doing it in a survey hides the disagreement the
  reader came for.

Unlike the tutorial genre, **alternatives and options are the point**. Never
collapse them for the sake of a cleaner narrative.

## Process

0. **Name the reader and the scope, and open the dossier.** Before
   searching, settle who this survey is for (a thesis reader? a grant
   reviewer? a paper's related-work section?) and what it will and won't
   cover. Write the scope statement into the draft's opening paragraph --
   including what's deliberately excluded, so a reader can tell an
   omission from an oversight. Then create the dossier and record the same
   decisions there:
   ```
   python3 -m src.dossier init content/drafts/<slug>.md --genre survey
   ```
   Fill in `scope.md`'s **Reader**, **Covers**, **Does not cover** and
   **Glossary** now, while you are deciding them. `init` also stamps the
   corpus fingerprint, which is what lets a later revision tell whether
   the ledger has moved since.
1. **Triage broadly, on a window sized to reject.** Break the requested topic
   into 2-4 sub-themes if it's broad. For each:
   ```
   python3 -m src.retrieval triage "<sub-theme>" --k 15 --log content/drafts/<slug>.md
   ```
   Over-fetch on purpose -- pull more candidates than you expect to use. The
   snippet is short (160 characters) because **this stage exists to rule
   candidates out, not to accept them.** A title plus a short window is
   usually enough to see that a paper merely shares vocabulary with the
   query; it is never enough to cite from. This is a keyword-overlap ranker,
   not embeddings (unless `src/enrich/embed_index.py` has been built for this
   corpus) -- a high score is a proxy for relevance, not a judgment of it.

   `--log` records the call's size in the dossier's `retrieval.md`, which is
   what makes the cost of a run measurable instead of estimated. Pass it on
   every call.
2. **Fetch evidence for the survivors only, then score them.** For each
   candidate you could not rule out:
   ```
   python3 -m src.retrieval evidence "<sub-theme>" --citekey <key> --log content/drafts/<slug>.md
   ```
   This returns the passages of that document that actually bear on the
   query, chosen for the query rather than anchored wherever the first term
   happened to appear -- so it can reach a passage late in a long paper,
   which a ranked snippet cannot.

   **Reject hard at stage 1.** The two-stage read only comes out cheaper
   than the one-stage one if triage does most of the rejecting; if you pass
   most candidates through to `evidence`, you have paid for both windows on
   the same document and it costs *more*. `docs/RETRIEVAL.md` has the
   break-even arithmetic. Passing a candidate to stage 2 is a decision, not
   a default.

   Now decide, against the real passages: does this source actually support a
   claim about the sub-theme? Keep only the ones that pass. This is the same
   discipline PaperQA2 calls "gather evidence" (retrieve, then LLM-judge
   relevance, *then* write) -- the difference here is you're doing the judging
   inline as part of drafting, not via a second API call.

   **Never promote a candidate to evidence from a triage snippet.** If you
   did not run `evidence` on it, it is not evidence. Treat a citekey that
   didn't pass this filter as unused, however high it ranked.

   **Record both outcomes in the dossier before you start drafting prose**,
   while the passages are still in front of you:
   - what survives, into `evidence.md` -- one `## \`citekey\`` block with
     a `relevance:` line (why it supports the claim) and a `support:` line
     (the quote or paraphrase);
   - what doesn't, into `rejected.md` -- one table row per candidate:
     citekey, the query that surfaced it, and a few words on why it was
     turned down ("shares vocabulary only", "wrong domain", "superseded by
     X").

   The rejected list is the more valuable of the two and the easier to
   skip. It is what stops the next revision retrieving and re-judging the
   same twelve papers you just turned down -- the single most expensive
   piece of repeated work in this pipeline.
2a. **On a broad topic, put steps 1-2 behind a subagent.** Dispatch one
   `general-purpose` subagent per sub-theme, all in one message, each told to
   run the triage/evidence loop above and return **only** the kept-evidence
   packet plus the rejected list -- never the raw candidates.

   The reason is not parallelism, and this -- not the two-stage split -- is
   where the reliable token saving is. Anything you read yourself stays in
   your context and is re-sent on every later turn of the run; anything a
   subagent reads is paid for once. Four sub-themes retrieved inline is tens
   of thousands of characters you will then carry through clustering,
   drafting, gating and rendering, most of it material you already rejected.

   Skip this for a narrow topic with one sub-theme -- a subagent that returns
   almost everything it read saves nothing and costs a dispatch.
3. **Reformulate and re-search if a sub-theme comes up thin.** A single
   query wording is not the ceiling -- if scoring leaves you with little or
   nothing for a sub-theme, try synonyms, broader/narrower terms, or an
   adjacent concept, and triage again. Do this a few times before concluding
   "thin coverage" is real rather than a wording problem. Only after genuine
   reformulation attempts should you report a sub-theme as thin -- and then
   say so explicitly rather than padding it with uncited claims.
4. **Cluster by judgment.** With a small corpus there's no BERTopic step;
   group the surviving (scored, kept) citekeys into themes yourself based on
   what the evidence actually says.
5. **Check for disagreement across sources before writing.** If two kept
   pieces of evidence conflict on a claim, don't silently pick one side --
   note the disagreement explicitly in the draft (which source says what).
   Silently resolving a real contradiction is a worse failure than leaving
   it visible.
6. **Draft**, in Markdown, using Pandoc-style citations (`[@citekey]`,
   `[@key1; @key2]`), citing only from your scored-evidence file:
   - Framing paragraph for the overall topic
   - One subsection per theme, citing the papers that actually support each claim
   - A comparison table: columns for approach/paper, citekey, core idea,
     stated limitations
   - A gap-analysis paragraph: what the retrieved corpus does *not* cover
     (including sub-themes that stayed thin after reformulation, and any
     cross-source disagreement from step 5)
7. **Never write a citekey you didn't get from a retrieval result.** If you
   want to cite something you know about from general knowledge but that isn't
   in the ledger, say so in prose to the user instead ("X is commonly discussed
   in this area but isn't in your synced library yet") -- do not invent a key
   for it.
8. **Map sections to citekeys.** Fill in the dossier's `sections.md` --
   one row per section heading with the citekeys cited under it -- so a
   later revision can tell which section owns a citation without reading
   the draft. `python3 -m src.dossier sections content/drafts/<slug>.md`
   prints the headings and their line ranges to build it from.
9. **Gate before presenting.** Save the draft as `content/drafts/<slug>.md`
   (this is the canonical, source-of-truth format), then run:
   ```
   python -m src.citation_gate content/drafts/<slug>.md
   ```
   If it reports `FAIL`, fix the offending line(s) — either correct the citekey
   or remove the claim — and re-run until it reports `OK`. Never show the user
   a draft that hasn't passed.
10. **Build the References section.** Once the gate passes, generate it from
    exactly the gated citekeys rather than writing it by hand:
    ```
    python -m src.references content/drafts/<slug>.md
    ```
    Stdlib-only, like the citation gate — bare `python3`, no venv. Writes
    numbered IEEE-style entries (`[1] J. Doe and R. Roe, "A Paper," *IEEE
    Trans. Testing*, vol. 3, pp. 1–9, 2024. \`doe_paper_2024\``) from
    `content/ledger.sqlite`, ordered by first appearance so the numbers
    match the rendered PDF's. Each entry keeps its citekey in a trailing
    code span, so a reader can trace every `[@citekey]` marker in the body
    back to an entry by that same key.

    Leave the body's inline citations as `[@citekey]` — do **not**
    hand-number them to `[1]`. The literal key is what the gate and the
    hook verify; pandoc assigns the numbers at render time.
11. **Render tex and pdf.** Once the gate passes and the references section
    is built, also render the other three formats:
    ```
    python3 -m src.render_output content/drafts/<slug>.md --format tex
    python3 -m src.render_output content/drafts/<slug>.md --format pdf
    python3 -m src.render_output content/drafts/<slug>.md --format md
    ```
    The `md` output is a numbered copy in `content/rendered/` -- the same
    IEEE numbers as the PDF, for a reader who won't open one. The draft
    itself keeps its `[@citekey]` markers.

    This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH — no
    enrich group required. If either command reports `[missing-binary]` or
    `[error]`, print a one-line warning in chat with that message and
    continue anyway — a rendering failure never blocks presenting the
    `.md` draft.
12. **Read it once as the reader** (`docs/WRITING-STANDARDS.md` §6). Check
    specifically for: terms used before they're defined, a theme heading that
    doesn't match what the subsection actually argues, a comparison-table row
    that repeats prose already above it, and any paragraph whose first
    sentence doesn't carry its point.
13. **Record any steering.** If the user shaped this draft in chat --
    "don't lead with tooling", "shorter", "drop the adoption angle" --
    append it to the dossier's `steering.md`, dated. It is invisible in
    the prose and has nowhere else to live; a revision that doesn't know
    about it will undo it.
14. Present the draft plus a one-paragraph summary of thin-coverage areas and
    any unresolved cross-source disagreement, and report the render outcome
    (paths to the `.tex`/`.pdf` if they succeeded, or the warning if not).
    Tell the user where the dossier is, that changes to this draft should
    go through `draft-reviser` rather than another run of this skill, and
    that `content/drafts/` and `content/dossiers/` are gitignored -- so
    `python3 -m src.dossier export <slug>` is how a draft and its working
    state get backed up.

## Sources

The prose standards this skill inherits are not original to this project.

Full citations, licences and a per-principle attribution table are in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
All three works are openly licensed (CC-BY or CC-BY-SA) and require credit.

What bears on *this* genre specifically:

- **Last, *Technical Writing Essentials* (CC-BY 4.0)** -- audience and task
  analysis as a step before drafting, and the introduction checklist that
  asks for scope and the reader's assumed background. Step 0 is that
  checklist applied to a survey.
- **Google, *Technical Writing Courses* (CC-BY 4.0)** -- the curse of
  knowledge, defining each term once, and leading a paragraph with its
  point. The last matters more here than in any other genre, because a
  survey is read by skimming.
- **Procida, *Diátaxis* (CC-BY-SA 4.0)** -- the genre-separation principle
  behind "What a survey owes its reader". Note that a literature survey is
  **not** one of Diátaxis's four quadrants: Diátaxis describes software
  documentation, and its tutorial/how-to rules (single path, no options)
  would destroy a survey. Only the underlying insight transfers -- that a
  document trying to do two jobs does both badly.

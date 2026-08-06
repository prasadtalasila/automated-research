---
name: deep-research
description: Runs a multi-perspective, corpus-grounded deep-research pipeline over the synced bibliography + source-pdfs -- perspective discovery, parallel simulated interviews, contradiction mapping, outline, cited section writing, synthesis briefing, and self peer-review. Adapted from hadufer/claude-storm (MIT), itself an implementation of Stanford OVAL's STORM method (Shao et al., NAACL 2024) fused with Nav Toor's 4-prompt adaptation -- retooled here to cite only real citekeys from content/ledger.sqlite (never a URL, never invented) instead of live web sources. Triggers when the user asks for "deep research", a multi-perspective analysis, or an in-depth grounded report on a topic, as distinct from survey-writer's single-pass literature survey. Heavier and slower than survey-writer by design. Must run `python -m src.citation_gate` before presenting and refuses to invent a citekey. Stops and tells the user to run `python -m src.sync` if the ledger is empty, rather than syncing itself.
tags: [deep-research, multi-perspective, storm, citation]
---

# deep-research

Every claim must resolve to one of:

- a real **citekey** from `content/ledger.sqlite` (via `src.retrieval.search()`
  or `src.heavy.embed_index.search()` if that stack has been built), cited
  `[@citekey]`;
- a `source-pdfs` document, discussed in prose by title/doc_id and
  explicitly marked **not citable** (per AGENTS.md's invariant -- never
  given a citekey, never a formal citation); or
- stated plainly as "not found in the corpus" -- never invented, never
  smoothed over.

This is a heavier, slower alternative to `survey-writer` for when the user
wants genuine multi-perspective depth (contradiction mapping, ranked
findings, self peer-review) rather than a single-pass literature survey.
It reads the same shared corpus layer as the other genre skills.

## Shared corpus layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `papers/bibliography.bib` (gitignored, per-host) -- source of truth for citekeys/metadata
- `src/retrieval.py` -- `search(query, k, snippet_chars)`, keyword overlap
- `src/heavy/embed_index.py` -- `search(query, k, snippet_chars)`, semantic
  (if built for this corpus -- check `content/chroma/` first)
- `papers/pdfs/` (config.toml's `[source_pdfs].dir`) -- non-citable raw PDFs, see `src/heavy/corpus.py`

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
| User asks for "deep research", a multi-perspective analysis, or an in-depth report with contradiction mapping / peer review | Invoke this skill |
| User asks for a standard literature survey / background section | Use `survey-writer` instead -- faster, single-pass |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |
| User asks for a textbook chapter / lecture notes | Use `textbook-chapter-writer` instead |
| User asks for a hands-on tutorial | Use `tutorial-writer` instead |
| Ledger is empty, or nothing is `parsed` | Say so and stop. **Never** run `src.sync` yourself |

Tell the user up front that this is a heavy, multi-phase run before
starting -- it dispatches several subagents and does many retrieval calls.
Create a TodoWrite list with the 7 phases below and work through them in
order.

## Prose standards

Follow `docs/WRITING-STANDARDS.md` for the cross-genre rules, and its
"Sources and attribution" section for where they come from. Two apply with
particular force to a multi-agent pipeline, because parallel writers drift
apart in ways a single-author draft doesn't:

- **Terminology is fixed at outline time, not at polish time.** When you
  dispatch Phase 5 writers, hand each one the same glossary of terms and
  their agreed definitions. Reconciling four writers who each named the same
  concept differently is a Phase 6 problem you can avoid entirely here.
- **Scope is stated in the report, not just held in your head.** The Phase 6
  lead says what this report covers and what it doesn't -- including which
  sub-questions the corpus couldn't answer.

## Depth presets

| Depth | Perspectives | Interview rounds | Section writers |
|---|---|---|---|
| quick | 3 + basic | 2 | inline (no subagents) |
| **standard** (default) | **5 + basic** | **3** | parallel subagents |
| deep | 6-7 + basic | 4 | parallel subagents |

"+ basic" = always include the **Basic fact writer** generalist pass.

## Phase 1 -- Perspective discovery

Run 1-2 broad retrieval calls on the topic itself and skim what the corpus
actually returns -- titles, sub-fields, recurring angles. Derive 1-2
**corpus-specific** personas from what's actually there, for `standard`/
`deep` depth (skip for `quick`). Then map the remaining slots onto these
five lenses, **adapted and renamed to fit the topic** (drop one that
genuinely doesn't apply):

1. **The Practitioner** -- what does applying this in practice surface that
   the papers gloss over?
2. **The Academic** -- what does the retrieved literature actually claim,
   and where do sources in this corpus disagree with each other?
3. **The Skeptic** -- the strongest limitation the corpus itself admits to
   (or a gap it fails to address).
4. **The Adoption/Incentives analyst** -- who would use this and why; what
   incentives shape the work (adapt or drop if inapplicable).
5. **The Historian** -- what earlier approaches does this build on or react
   against.

Always add the **Basic fact writer**. State your final persona list before
dispatching.

## Phase 2 -- Multi-perspective grounded interviews (parallel)

Dispatch one `deep-research-interviewer` subagent per persona, **all in
parallel** (multiple Agent calls in a single message). If that subagent
type isn't available, use `general-purpose` and give it the protocol from
`reference.md` §3 plus the packet schema from
`.claude/agents/deep-research-interviewer.md` (or tell it to `Read` that
file).

Give each subagent: `TOPIC`, its `PERSPECTIVE` (name + focus), `ROUNDS` (per
depth). Each returns: core position, grounded key claims cited by real
citekey, an only-this-perspective insight, strongest evidence, open
questions, and the citekeys/doc_ids consulted.

No web fallback: if a perspective's searches turn up nothing relevant after
reasonable reformulation, that's a real "thin coverage" finding to report,
not something to paper over.

Citekeys need no de-duplication/global-renumbering step (unlike
claude-storm's URL-globalization algorithm) -- see `reference.md` §4 for
why a citekey is already the stable, project-wide identifier.

## Phase 3 -- Contradiction map

1. **Direct contradictions** -- where perspectives cite sources that
   disagree, with the specific conflicting claims (both sides, by citekey).
2. **Strongest vs weakest evidence** -- which perspective's claims are
   best/worst supported by what's actually in the corpus.
3. **The resolving question** -- what the corpus would need to answer to
   settle the biggest contradiction.
4. **Universal agreement** -- what every perspective's findings agree on.
5. **The blind spot** -- what no perspective's searches turned up at all.

## Phase 4 -- Outline

Sketch a draft outline from general topic knowledge, then refine using the
interview findings and contradiction map. No "Summary"/"Introduction"
heading (the lead comes in Phase 6).

Also fix, at this point, two things Phase 5 will otherwise get wrong in
parallel: **the reader** (who this report is for, one concrete sentence --
see `docs/WRITING-STANDARDS.md` §1) and **the glossary** (each recurring term
with the one definition every section writer must use). Pass both to every
dispatched writer alongside their section fragment and citekeys.

## Phase 5 -- Cited section writing (parallel)

For each top-level section, select the relevant kept citekeys from Phase
2's packets. For `standard`/`deep`, dispatch `deep-research-writer`
subagents **in parallel** (one per section) with `TOPIC`, the section
outline fragment, and the relevant citekeys plus supporting facts. If
unavailable, use `general-purpose` with
`.claude/agents/deep-research-writer.md`'s instructions. For `quick`, write
inline. Cap concurrency per `reference.md` §1.

Inline `[@citekey]` citations, neutral tone, every sentence grounded, no
per-section reference list. A writer may re-search a thin subpoint -- only
against this project's corpus, never inventing a citekey.

## Phase 6 -- Polish + synthesis briefing

**(a) Lead:** `## Summary`, <=4 cited paragraphs, opening with a scope
statement -- what this report covers, what it doesn't, and which
sub-questions the corpus couldn't answer. Remove repetition across sections.

**(a2) Reconcile across sections.** Parallel writers produce specific,
predictable seams; fix them here rather than leaving them for the reviewers:

- the same concept named two ways, or one name used for two concepts
- a term defined independently in two sections
- notation that shifts between sections
- the same finding stated at different strengths in two places
- a claim that section 3 assumes but only section 5 establishes

Then read the assembled draft once as the Phase 4 reader
(`docs/WRITING-STANDARDS.md` §6) -- a pass over the whole document, which no
individual section writer was in a position to do.

**(b) Synthesis briefing:** one-paragraph executive summary; 5 key findings
ranked by reliability (perspectives supporting/challenging each, cited by
citekey); the hidden connection visible only across perspectives combined;
the actionable insight for the user's role; the frontier question.

## Phase 7 -- Peer review + assembly

**(a) Peer review.** STORM's documented weakness is skipping self-critique
entirely; a single self-review pass (below, `quick` depth) is one fix, but
one voice reviewing its own work shares its own blind spots. For
`standard`/`deep`, use the panel described in `reference.md` §7 instead
(idea credited to
[Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills)'s
Stage-3 peer review -- see the README's Acknowledgements; nothing from that
repository's text is reused here, only the idea of an independent panel
plus an adversarial reviewer):

- Dispatch four `peer-reviewer` subagents **in parallel**, one per role --
  `domain-accuracy`, `methodology-rigor`, `clarity-completeness`,
  `devils-advocate` -- each given the full draft and nothing else (no
  reviewer sees another's critique). If that subagent type isn't
  available, use `general-purpose` with
  `.claude/agents/peer-reviewer.md`'s instructions for the assigned role.
- **Reconcile under the concession threshold** (this project's own rule,
  not upstream's): any `high`-severity concern from *any* reviewer, or any
  concern raised independently by *2 or more* reviewers, must be addressed
  before presenting -- either revise the claim/citation, or state the
  concern openly in the peer-review scorecard as an unresolved issue. It
  may not be silently dropped. `low`/single-reviewer `medium` concerns are
  logged in the scorecard but don't block presenting.
- Act as the reconciling editor yourself: read all four verdicts
  (`ready`/`needs revision`/`reject`), decide what the draft actually needs
  in light of them, revise where the threshold above requires it, and
  record the final scorecard.

For `quick` depth, do a single inline self-critique instead (no subagent
dispatch): confidence score (1-10) per key finding with justification;
weakest link and what would verify it; bias check (did one perspective's
sources dominate); a missing 6th perspective; overall grade.

**(b) Assemble** per `reference.md` §5's template: Title -> Summary ->
Synthesis briefing -> article body -> Contradiction map -> Peer-review
scorecard -> References (citekeys with title/year from the ledger, not URLs).

**(c) Log provenance and gate.** Write `content/provenance/<slug>.json`
covering every section's citekeys. Then:
```
python -m src.citation_gate <output-file>
```
Fix and re-run until `OK`. Never present a draft that hasn't passed.

**(d) Save and render.** Write to `content/drafts/deep-research-<slug>.md`
(the canonical, source-of-truth format). Then fill in the `## References`
section (reference.md §5's template) from exactly the gated citekeys,
rather than hand-assembling it:
```
python -m src.references content/drafts/deep-research-<slug>.md
```
Stdlib-only, like the citation gate -- bare `python3`, no venv. It writes
numbered IEEE-style entries; leave the body's inline citations as
`[@citekey]` rather than hand-numbering them to `[1]`, since pandoc
assigns the numbers at render time. Then render the other three formats:
```
python3 -m src.heavy.render_output content/drafts/deep-research-<slug>.md --format tex
python3 -m src.heavy.render_output content/drafts/deep-research-<slug>.md --format pdf
python3 -m src.heavy.render_output content/drafts/deep-research-<slug>.md --format md
```
The `md` output is a numbered copy in `content/rendered/` -- the same
IEEE numbers as the PDF, for a reader who won't open one. The draft
itself keeps its `[@citekey]` markers.

This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH — no heavy
venv required. If either command reports `[missing-binary]` or `[error]`,
print a one-line warning in chat with that message and continue anyway —
a rendering failure never blocks presenting the `.md` report. Give the
user: headline finding, the single most important contradiction, the
actionable insight, the overall grade, any unresolved peer-review concern
left in the scorecard, the citekey count, the saved path, and the render
outcome (paths to the `.tex`/`.pdf` if they succeeded, or the warning if
not).

## Guardrails

- **Grounded by default, closed-corpus.** Every claim traces to a real
  citekey, or is marked as found only in a non-citable `source-pdfs`
  document, or is stated as not found. Never fabricate a citekey, a quote,
  or a finding.
- **Parallelize, with a cap.** Dispatch same-phase subagents in one message;
  bound concurrency per `reference.md` §1.
- **Be honest about cost.** This is intentionally heavy and slower than
  `survey-writer` -- point users there if they want something faster.

## Sources

The prose standards this skill inherits are not original to this project.

Full citations, licences and a per-principle attribution table are in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
All three works are openly licensed (CC-BY or CC-BY-SA) and require credit.

What bears on *this* genre specifically:

- **Google, *Technical Writing Courses* (CC-BY 4.0)** -- using the same term
  for the same concept throughout is the direct ancestor of the Phase 4
  glossary. In a single-author document that rule is a style preference; in
  a pipeline dispatching parallel section writers it is the difference
  between one report and four stitched together, which is why it is
  enforced structurally at outline time rather than left to Phase 6 polish.
- **Last, *Technical Writing Essentials* (CC-BY 4.0)** -- the introduction
  checklist -- scope ("what will and will not be covered") plus the reader's
  assumed background -- behind Phase 6's scope statement.
- **Procida, *Diátaxis* (CC-BY-SA 4.0)** -- the genre-separation principle.
  A multi-perspective research report is not a Diátaxis quadrant, and none
  of the tutorial/how-to structural rules apply; what transfers is the
  requirement that the report know which single job it is doing.

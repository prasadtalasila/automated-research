---
name: deep-research
description: Runs a multi-perspective, corpus-grounded deep-research pipeline over the synced bibliography -- perspective discovery, parallel simulated interviews, contradiction mapping, outline, cited section writing, synthesis briefing, and self peer-review. Adapted from hadufer/claude-storm (MIT), itself an implementation of Stanford OVAL's STORM method (Shao et al., NAACL 2024) fused with Nav Toor's 4-prompt adaptation -- retooled here to cite only real citekeys from content/ledger.sqlite (never a URL, never invented) instead of live web sources. Triggers when the user asks for "deep research", a multi-perspective analysis, or an in-depth grounded report on a topic, as distinct from survey-writer's single-pass literature survey. Heavier and slower than survey-writer by design. Must run `python -m src.citation_gate` before presenting and refuses to invent a citekey. Stops and tells the user to run `python -m src.sync` if the ledger is empty, rather than syncing itself.
tags: [deep-research, multi-perspective, storm, citation]
---

# deep-research

Every claim must resolve to one of:

- a real **citekey** from `content/ledger.sqlite` (via `src.retrieval.search()`
  or `src.enrich.embed_index.search()` if that stack has been built), cited
  `[@citekey]`; or
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
- `src/enrich/embed_index.py` -- `search(query, k, snippet_chars)`, semantic
  (if built for this corpus -- check `content/chroma/` first)
- `src/enrich/corpus.py` -- builds the enrichment corpus from the ledger and
  nothing else, so every document it yields is citable, keyed by its citekey

## The dossier: write down what produced the draft

The report is only half of what this run produces. The other half is the
judgment behind it -- the reader, the scope, the glossary, which
candidates were kept, **which were turned down and why**, and where the
perspectives found the corpus disagreeing with itself -- and it belongs
on disk, not in this conversation. Without it, changing one paragraph
next month means running seven phases and a dozen subagents again.

`src/dossier.py` owns that state, in Markdown, one directory per draft at
`content/dossiers/<the draft's path, minus its suffix>/`. Create it before
Phase 1's first retrieval call and fill it in as you go -- not at the end,
when what you rejected has already fallen out of your context.
`docs/DRAFT-ITERATION.md` is the full design.

**The main run owns the dossier. A subagent never writes it.** Treat that
as a rule of this skill, not a preference. `deep-research-interviewer`,
`deep-research-writer` and `peer-reviewer` each run in their own context,
hand back a packet, and then that context is gone -- and each of their
definitions in `.claude/agents/` tells them to return their output rather
than write a file. So anything of theirs worth keeping is yours to
transcribe, in the phase that dispatched them, before you move on:

- **After Phase 2**, each interviewer packet's kept claims and their
  citekeys go into `evidence.md`, and the citekeys the packet lists as
  *discarded during filtering* go into `rejected.md` -- one row each with
  the query that surfaced it and a few words on why it was turned down.
  That discarded list exists nowhere but the packet, and re-retrieving and
  re-judging those same papers is the single most expensive thing a later
  session repeats.
- **After Phase 3**, record the contradictions themselves -- the conflict,
  both sides, both citekeys -- next to the citekeys they concern in
  `evidence.md`, so a revision can see the disagreement without rebuilding
  the map from scratch.
- **After Phase 5**, every writer's `### Sources added` block goes into
  `evidence.md` and its `### Candidates discarded` block into
  `rejected.md`. A citekey found by a writer re-searching a thin subpoint
  is otherwise cited in the report and recorded nowhere, and one it turned
  down is lost entirely. At `quick` depth you are that writer -- record
  what you find the same way.

The `peer-reviewer` packets are the one exception, and only because Phase
7(a)'s reconciled scorecard is already their durable record: it ships in
the report itself, including the concerns logged but left unaddressed.
Keep that row honest and the reviewers need no separate transcription;
drop the below-threshold concerns from it and four reviews die with the
run.

Do not ask a subagent to write into `content/dossiers/`, and do not treat
a returned packet as durable because you can still see it. If you haven't
transcribed it, it is gone when the phase closes.

**Then dispatch from the file rather than from your context.** Phase 5
hands each section writer a command that reads its rows back:

```bash
python3 -m src.dossier brief content/drafts/deep-research-<slug>.md --section "<heading>"
```

Pasting the same claims into four dispatch prompts spends them as
*output*, which costs five times what a cached input token costs and is
spent once per writer; a pointer costs about forty tokens and the writer
reads the rows inside its own context, which is discarded when it exits.
`docs/TOKENS.md` has the arithmetic. This does not shrink what you are
already carrying -- nothing can, a context is append-only between
compactions -- so treat the transcription as the durability half and this
as the cost half, and note the second only works if you did the first.
Which is the other reason to prefer it: `brief` exits non-zero and names
the citekey when a block is missing, so a transcription you skipped
surfaces here instead of silently.

**The dossier is not the provenance JSON, and neither replaces the
other -- this skill writes both.** `content/provenance/<slug>.json`
(Phase 7c) is the machine record of section -> citekey, for tooling. The
dossier is the human-readable working state: reader, scope, glossary,
kept evidence, rejected candidates and why, contradictions, and the
user's steering.

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
| User asks for "deep research", a multi-perspective analysis, or an in-depth report with contradiction mapping / peer review | Invoke this skill |
| User asks for a standard literature survey / background section | Use `survey-writer` instead -- faster, single-pass |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |
| User asks for a textbook chapter / lecture notes | Use `textbook-chapter-writer` instead |
| User asks for a hands-on tutorial | Use `tutorial-writer` instead |
| User asks to change a report that **already exists** in `content/drafts/` | Use `draft-reviser` instead -- never re-run this skill to make a change |
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

**Before any retrieval, name the reader and the scope, and open the
dossier.** Settle who this report is for (a research group? a decision
the user has to make? a chapter's background?) and what it will and won't
cover, then create the dossier:

```
python3 -m src.dossier init content/drafts/deep-research-<slug>.md --genre deep-research
```

Give it the same path Phase 7(d) will save to -- the dossier mirrors its
draft's path, and one opened under a different name is found by nothing
later. Fill in `scope.md`'s **Reader**, **Covers**, **Does not cover** and
**Glossary** now, while you are deciding them; Phase 4 fixes the final
reader sentence and glossary and updates that same file. `init` also
stamps the corpus fingerprint, which is what lets a later revision tell
whether the ledger has moved since. It only creates files that are
missing, so re-running it can't overwrite what you've filled in.

Then run 1-2 broad retrieval calls on the topic itself and skim what the corpus
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
questions, and the citekeys consulted.

**Transcribe every packet into the dossier before starting Phase 3** --
kept claims and their citekeys into `evidence.md`, the packet's discarded
citekeys into `rejected.md` with the query and the reason. The
interviewers cannot do this for you, and six packets sitting in your
context are not a record.

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

Record the contradictions in `evidence.md` beside the citekeys they
concern, and the blind spot in `scope.md`'s **Does not cover**. Both are
findings of this run that the report's own prose states only in passing,
and a revision that doesn't know about them will smooth them over.

## Phase 4 -- Outline

Sketch a draft outline from general topic knowledge, then refine using the
interview findings and contradiction map. No "Summary"/"Introduction"
heading (the lead comes in Phase 6).

Also fix, at this point, two things Phase 5 will otherwise get wrong in
parallel: **the reader** (who this report is for, one concrete sentence --
see `docs/WRITING-STANDARDS.md` §1) and **the glossary** (each recurring term
with the one definition every section writer must use). Pass both to every
dispatched writer alongside their section fragment and citekeys.

Update `scope.md`'s **Reader** and **Glossary** with what you settle on
here, over the provisional versions from Phase 1, and hand the writers the
glossary from that file. One glossary, in one place: a second copy kept
only in this conversation is the drift Phase 4 exists to prevent.

**Then write the plan into `sections.md`** -- one row per outline section
with the kept citekeys that section will stand on, chosen from Phase 2's
transcribed evidence. This is the decision you would otherwise make
inside the Phase 5 dispatch prompt, and putting it in the file is what
lets that prompt be one line: `dossier brief --section` resolves a
section name through these rows. A section you haven't assigned evidence
to yet gets a row with an empty citekey cell rather than no row at all --
an empty cell is a gap to fill, a missing row reads as a mistyped section
name, and Phase 5 has to be able to tell those apart. Phase 7(e)
reconciles the file against what the finished report actually cites, so
this is a plan now and a record then.

## Phase 5 -- Cited section writing (parallel)

Phase 4 already chose each section's citekeys and wrote them into
`sections.md`. Before dispatching, check that those rows actually
resolve to transcribed evidence:

```bash
python3 -m src.dossier brief content/drafts/deep-research-<slug>.md \
  --section "<section heading>" --check
```

`--check` prints how many of the row's citekeys have a block and names
any that don't, without printing the blocks -- so you find a missed
transcription without reading the evidence back into your own context.
Fix a gap now: a writer dispatched against an empty brief writes an
ungrounded section that reads exactly like a grounded one.

For `standard`/`deep`, dispatch `deep-research-writer` subagents **in
parallel** (one per section), each given `TOPIC`, `READER`, `GLOSSARY`,
its section outline fragment, and the one line that stands in for the
evidence:

```
Your evidence: python3 -m src.dossier brief content/drafts/deep-research-<slug>.md --section "<heading>"
```

**Do not paste the kept claims into the prompt.** That is the whole of
this phase's cost saving, and it is in the output pool -- see "The
dossier" above and `docs/TOKENS.md`. If a writer needs something the
rows don't carry (a term, a constraint from the user's steering), give it
that, not the evidence it can read for itself.

If `deep-research-writer` is unavailable, use `general-purpose` with
`.claude/agents/deep-research-writer.md`'s instructions -- the command
line goes in the prompt either way. For `quick`, write inline: you are
the writer, the packets are already in your context, and running `brief`
against yourself would only add tokens. Cap concurrency per
`reference.md` §1.

Inline `[@citekey]` citations, neutral tone, every sentence grounded, no
per-section reference list. A writer may re-search a thin subpoint -- only
against this project's corpus, never inventing a citekey.

When the writers return, copy each `### Sources added` block into
`evidence.md` yourself, with why the writer kept it, and each
`### Candidates discarded` block into `rejected.md`. These are citekeys
that never passed through Phase 2, so nothing else in the run has them.

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
covering every section's citekeys. This is the machine record, and it is
not the dossier: the JSON maps section -> citekey for tooling, while the
dossier holds the working state a human or a later revision reads. Write
both. Then:
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
python3 -m src.render_output content/drafts/deep-research-<slug>.md --format tex
python3 -m src.render_output content/drafts/deep-research-<slug>.md --format pdf
python3 -m src.render_output content/drafts/deep-research-<slug>.md --format md
```
The `md` output is a numbered copy in `content/rendered/` -- the same
IEEE numbers as the PDF, for a reader who won't open one. The draft
itself keeps its `[@citekey]` markers.

This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH — no enrich
group required. If either command reports `[missing-binary]` or `[error]`,
print a one-line warning in chat with that message and continue anyway —
a rendering failure never blocks presenting the `.md` report.

**(e) Close the dossier.** Two things are still only in this conversation:

- **The section map.** Reconcile `sections.md` against the finished
  report: Phase 4 wrote the *plan* there, and the writers moved off it --
  a citekey one of them added by re-searching (its `### Sources added`
  block) is cited and unlisted, and one it was handed but never used is
  listed and uncited. Correct the rows to what the report actually cites,
  so a later revision can tell which section owns a citation without
  reading the report.
  `python3 -m src.dossier sections content/drafts/deep-research-<slug>.md`
  prints the headings and their line ranges to check against. It is the
  same section -> citekey relation as (c)'s provenance JSON, written for
  the reviser rather than for tooling.
- **The steering.** If the user shaped this run in chat -- "drop the
  adoption perspective", "shorter", "deep depth", "don't lead with
  tooling" -- append it to `steering.md`, dated. It is invisible in the
  prose and has nowhere else to live; a revision that doesn't know about
  it will undo it.

**(f) Present.** Give the user: headline finding, the single most
important contradiction, the actionable insight, the overall grade, any
unresolved peer-review concern left in the scorecard, the citekey count,
the saved path, and the render outcome (paths to the `.tex`/`.pdf` if they
succeeded, or the warning if not). Then tell them where the dossier is,
that changes to this report should go through `draft-reviser` rather than
another run of this skill -- seven phases and a dozen subagents is the
wrong price for an edit -- and that `content/drafts/` and
`content/dossiers/` are gitignored, so
`python3 -m src.dossier export deep-research-<slug>` is how the report and
its working state get backed up.

## Guardrails

- **Grounded by default, closed-corpus.** Every claim traces to a real
  citekey, or is stated as not found. Never fabricate a citekey, a quote,
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

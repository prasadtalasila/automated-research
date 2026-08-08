# Genres

Status: **reference.** Written 2026-08-08, describing `.claude/skills/`
as it stands.

Which skill writes what, how to pick between them, and what each one
refuses to do. Six skills live in `.claude/`: five that write a new draft
and one that changes an existing one.

You do not invoke any of them by name. Each has a `description` in its
frontmatter that names its triggers, and asking for the thing in ordinary
words -- "write a survey section on X", "draft a thesis chapter on Y" --
is what selects it. This document is for the two cases where that isn't
enough: when you want to know which genre you are actually asking for,
and when you want to know why the one that ran refused something.

Related reading:

- [WRITING-STANDARDS.md](WRITING-STANDARDS.md) -- the prose rules all six
  share, and where in the technical-communication literature they come
  from.
- [DRAFT-ITERATION.md](DRAFT-ITERATION.md) -- the dossier every skill
  writes, and why `draft-reviser` exists.
- [TOKENS.md](TOKENS.md) -- what a run costs, and why re-running a genre
  skill to make a change is the most expensive mistake available here.
- [ARCHITECTURE.md](ARCHITECTURE.md) -- the drafting layer these sit in,
  and the corpus layer they all read and none of them writes.

## Table of contents

- [Picking one](#picking-one)
- [At a glance](#at-a-glance)
- [The five drafting genres](#the-five-drafting-genres)
- [Revising: draft-reviser](#revising-draft-reviser)
- [What all six have in common](#what-all-six-have-in-common)
- [The boundaries, and why they are enforced](#the-boundaries-and-why-they-are-enforced)
- [Genres this project does not have](#genres-this-project-does-not-have)

## Picking one

Two questions settle it almost always.

**Does the draft already exist in `content/drafts/`?** Then the answer is
`draft-reviser`, whatever the genre. Never re-run the skill that wrote
it.

**Otherwise: what is the reader doing while they read?**

| The reader is... | Genre | Skill |
|---|---|---|
| entering a field and needs the map of it, and the gaps | organising literature | `survey-writer` |
| an examiner, reading adversarially for the claim that outruns its evidence | arguing toward a research question | `thesis-chapter-writer` |
| a student, studying the topic -- reading, not typing | explaining with worked examples | `textbook-chapter-writer` |
| a learner at a keyboard, following you to a working result | a hands-on lesson | `tutorial-writer` |
| someone who needs several perspectives on the topic reconciled, and where the corpus disagrees with itself | multi-perspective research report | `deep-research` |

The pair that gets confused is the teaching pair, and the skills say so
themselves: if the request is "write something teaching X", the question
to ask is *will the reader be reading this, or doing it?* Reading is
`textbook-chapter-writer`; doing is `tutorial-writer`. They have opposite
rules about explanation -- digression into *why* is a feature of one and a
defect in the other -- so a wrong guess produces a document that fails at
both.

## At a glance

| | Output | Citation density | Subagents | Cost |
|---|---|---|---|---|
| `survey-writer` | `content/drafts/<slug>.md` | every claim | none | one run |
| `thesis-chapter-writer` | `content/drafts/<slug>.tex` fragment | every claim | none | one run |
| `textbook-chapter-writer` | `content/drafts/<slug>.md` | sparse -- background only | none | one run |
| `tutorial-writer` | `content/drafts/<slug>.md` | closing section only | none | one run, plus running the lesson |
| `deep-research` | `content/drafts/deep-research-<slug>.md` | every claim | 6 interviewers, N writers, 4 reviewers | heaviest by design |
| `draft-reviser` | edits an existing draft in place | inherits the draft's | none | cheapest path there is |

All five drafting skills also write `content/dossiers/<draft path minus
suffix>/`; `deep-research` and `thesis-chapter-writer` additionally write
`content/provenance/<slug>.json`. Nothing under `content/` is tracked by
git -- see [DRAFT-ITERATION.md](DRAFT-ITERATION.md#backup-and-restore)
for how a draft and its dossier get backed up.

## The five drafting genres

### `survey-writer`

Topic-clustered literature survey, background section, related-work
section, "state of the art". Retrieves broadly across two to four
sub-themes, judges every candidate, clusters what survives by theme, and
closes with a comparison table and a gap analysis.

Its job is to **organise a field and locate the gaps in it**, and it has
two named failure directions. Drifting into a textbook chapter -- starting
to teach the concepts, when the reader is a researcher who needs the map
rather than the lesson. And drifting into an argument -- defending one
approach as correct, which is the thesis chapter's job and which here
hides the disagreement the reader came for. Alternatives are the
deliverable, so they are never collapsed for a cleaner narrative.

This is the skill whose retrieval pass dominates its own token cost, and
the one whose economics are worked through in
[TOKENS.md](TOKENS.md#example-1-one-rejected-paper-followed-to-the-end-of-the-run).

### `thesis-chapter-writer`

A chapter tied to a specific research question, written for an examiner:
a domain expert reading adversarially. Unlike a survey, this genre **does
take a position** -- but every step of the argument has to trace to
something cited, and an honestly stated limitation is worth more than the
paragraph that hides it.

The output is a standalone `.tex` fragment using `\citep`/`\citet`, with
no document preamble, meant to be `\input` by your own thesis document.
It also renders `.md` and `.pdf` previews when pandoc and pdflatex are
present.

Its genre boundary: a chapter that only summarises papers in sequence is
a survey with a chapter heading. If the argument toward the research
question isn't visible in the section structure, the chapter isn't doing
its job.

### `textbook-chapter-writer`

An undergraduate chapter: learning objectives, motivation, worked
examples, exercises. Diátaxis-wise this is *explanation with worked
application*.

It is the least citation-dense of the five. Most of its content is
original worked examples; the corpus is cited for motivation and
background, and the citation gate still applies to whatever it does cite.

Its discipline is the **curse of knowledge**: you know this material and
the student does not, and the specific danger is the step that feels too
obvious to state. Terms are defined once at first use and then used
consistently; "obviously", "simply", "just" and "of course" mark a
sentence as a candidate for expansion rather than for the chapter.
Concrete instance first, generalise from it -- which matters more in this
genre than in any other.

### `tutorial-writer`

A Diátaxis tutorial: a lesson a learner follows at a keyboard, start to
finish, to a working result they can see. The governing analogy in the
skill is a driving lesson -- the point is not to get from A to B, the
point is that the student can drive afterwards. The route is a pretext.

Its rule, from which everything else follows: **a tutorial that doesn't
work is worse than no tutorial**, because a learner who follows your
instructions exactly and gets an error concludes that they are the
problem. So the lesson is single-path with no options or branches, every
value is concrete rather than a placeholder, every step that produces
output says what the learner should see, and the whole thing is run end
to end before it is presented. Citations appear only in a closing "Where
to go next", never mid-lesson.

Its two failure directions are drifting into explanation (the lesson
stalls while you explain the architecture) and drifting into a how-to
(you start offering alternatives, which a learner who doesn't yet know
the domain cannot evaluate).

### `deep-research`

Seven phases and a dozen subagents: perspective discovery, parallel
grounded interviews, contradiction mapping, outline, parallel cited
section writing, synthesis briefing, and a peer-review panel. It is
adapted from `hadufer/claude-storm`'s implementation of Stanford OVAL's
STORM method, retooled to cite only real citekeys from this project's
corpus instead of live web sources.

What you get that no other genre produces: a **contradiction map** --
where sources in your corpus disagree, both sides by citekey -- a blind
spot naming what no perspective's searches turned up at all, findings
ranked by how well the corpus actually supports them, and a peer-review
scorecard from four independent reviewers including a dedicated
adversarial one.

Three depth presets trade cost against breadth:

| Depth | Perspectives | Interview rounds | Section writers |
|---|---|---|---|
| `quick` | 3 + basic | 2 | inline, no subagents |
| `standard` (default) | 5 + basic | 3 | parallel subagents |
| `deep` | 6-7 + basic | 4 | parallel subagents |

It is the heaviest skill here and says so before it starts. If what you
want is a single-pass literature survey, `survey-writer` is faster and
the skill will point you there. Its own cost structure -- and the one
remaining thing that could reduce it -- is
[TOKENS.md](TOKENS.md#example-2-six-interview-packets-from-phase-3-to-phase-7f).

## Revising: `draft-reviser`

Not a genre. The skill that changes a draft one of the five already
wrote, including in a session that has never seen it.

It reads the dossier instead of the corpus: `scope.md` and `steering.md`
bound what the revision may change, `sections.md` maps the change onto
line ranges so only the affected sections are read and edited, and
`rejected.md` is consulted before any new retrieval so the same
candidates are not re-judged. A request that contradicts the recorded
scope is a scope change and gets said out loud rather than quietly
applied.

It also runs the other way round, from the corpus rather than from a
request. When `dossier status --all` reports that a sync removed a paper
a draft cites, `draft-reviser` re-grounds it: reads the drift report as
JSON, repairs the broken citations in the sections that carry them,
weighs only the new candidates that bear on the sub-theme in play, leaves
previously declined papers declined unless their recorded reason has
stopped holding, and re-stamps the corpus fingerprint once the gate
passes. What that promises is no *missing* citations, not an empty
candidate list -- see
[DRAFT-ITERATION.md](DRAFT-ITERATION.md#re-grounding-after-the-corpus-moves).

Drafts written before `src/dossier.py` existed have no dossier, and so do
hand-written ones. It bootstraps rather than refusing -- `dossier init`,
then fill in what the draft itself can tell you -- and says in chat that
`evidence.md` and `rejected.md` are empty, so the first revision may have
to re-retrieve for a sub-theme a real dossier would have answered from
disk. It gets cheaper from the second revision on. It does not invent
evidence entries to fill the file: an empty `evidence.md` is honest, and
a fabricated one is the same failure class as a fabricated citekey.

**Every one of the five drafting skills routes here for changes.** Each
carries the same row in its own "When to invoke" table: *user asks to
change something that already exists -> use `draft-reviser`, never re-run
this skill*. [TOKENS.md](TOKENS.md) is why.

## What all six have in common

These are not per-skill choices. They are the same rules restated in six
files, and a skill that broke one would be the bug.

**One invariant.** A citekey may only be used if it appears in your `.bib`
export *and* was picked up into the ledger by a real parse of a real PDF.
No skill fabricates one, ever, and none may "fix" a gate failure by
inventing a plausible-looking key -- it corrects the key or removes the
claim.

**The gate is the only exit.** `python -m src.citation_gate` runs on the
skill's own output, and no draft is presented until it reports `OK`.

**The corpus is read-only.** No skill runs `python -m src.sync` or any
`scripts/enrich.py` stage. Both take the pipeline's write lock and can
run for tens of minutes; they are yours to run. If the ledger is empty or
nothing is `parsed`, the skill says exactly what it checked and what it
found and stops, rather than drafting around it.

**Every run writes a dossier**, created before the first retrieval call
and filled in as the run goes -- not at the end, when what was rejected
has already fallen out of context.

**Shared prose standards.** Name the reader before drafting, define terms
once, state scope up front, active voice with a named actor, no
"obviously/simply/just", reread as the reader before presenting.
[WRITING-STANDARDS.md](WRITING-STANDARDS.md) holds them and the
attribution -- Diátaxis, Last's *Technical Writing Essentials*, Google's
Technical Writing courses, all CC-licensed and all requiring credit.

**Rendering never blocks.** `.tex`/`.pdf` rendering that fails for a
missing binary prints a one-line warning and the `.md` draft is presented
anyway.

## The boundaries, and why they are enforced

Every skill carries a "When to invoke" table whose rows are mostly
*other* skills. That looks like duplication and is not: the genre
boundary is the thing most likely to be crossed, because crossing it
feels helpful at the time.

The cost of crossing is that the rules are not merely different but
**opposite**. A tutorial's "one path, no branches" would delete a
survey's deliverable, because alternatives are what a survey is for. A
survey's "weigh both sides without picking a winner" would gut a thesis
chapter, whose job is to take a position. A textbook chapter's
digressions into *why* are the exact defect that stalls a tutorial. So a
skill that drifts into the neighbouring genre does not produce a slightly
off document -- it produces one that fails at both jobs, and the failure
is invisible until a reader tries to use it.

This is [WRITING-STANDARDS.md](WRITING-STANDARDS.md) §5 -- don't let a
document do two jobs -- enforced at the point where it is easiest to
break.

## Genres this project does not have

Named here so a skill can tell you plainly rather than writing the wrong
thing:

| Genre | Reader's state | What happens |
|---|---|---|
| **How-to guide** | already competent, has a specific goal | No skill. Say so, and write it as a short procedure |
| **Reference** | needs a fact, fast | No skill |

Both are Diátaxis quadrants this repository has deliberately not built.
`tutorial-writer` names them explicitly so that a request for one is
answered with a short procedure rather than with a tutorial the asker
does not need.

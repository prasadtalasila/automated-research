# Rejection

Status: **reasoning document.** Written 2026-08-07.

Why turning a source *down* is the load-bearing judgment in this
pipeline; why a two-stage retrieval read built to make rejection cheaper
was withdrawn before it shipped; and what was kept from it.

This is a record of a decision that went the other way, kept because the
reasoning is reusable and the mistake is an easy one to make twice.

Related reading:

- [TOKENS.md](TOKENS.md) -- where the tokens go. This document assumes
  its two-pool framing, and the accounting below is worked through there
  with the caching multipliers applied.
- [DRAFT-ITERATION.md](DRAFT-ITERATION.md) -- the dossier: what it holds,
  and how a draft is revised without re-running the pipeline.
- [RETRIEVAL.md](RETRIEVAL.md) -- how the corpus is ranked, what a
  snippet contains, and what `evidence` does.
- [PERFORMANCE.md](PERFORMANCE.md) -- **measured** costs. Nothing here
  belongs there: every token figure below is derived from file sizes and
  documented defaults, and is labelled an estimate each time it appears.

## Table of contents

- [Why rejection and not selection](#why-rejection-and-not-selection)
- [What the two-stage read costs](#what-the-two-stage-read-costs)
- [The bug the split exposed](#the-bug-the-split-exposed)
- [Why it does not generalise across genres](#why-it-does-not-generalise-across-genres)
- [Was it worth building at all?](#was-it-worth-building-at-all)
- [Recording how a rejection was made](#recording-how-a-rejection-was-made)
- [Why deep-research keeps the boundary alone](#why-deep-research-keeps-the-boundary-alone)
- [Should any of this be configurable?](#should-any-of-this-be-configurable)
- [What was decided](#what-was-decided)

## Why rejection and not selection

A genre skill retrieves fifteen candidates per query and keeps about
three. Almost all of its retrieval work is therefore *rejection*, and
rejection has three properties that selection does not.

**It is invisible in the output.** A source wrongly kept shows up as a
citation someone can check -- `citation_gate` verifies the citekey,
`citation_provenance` scores the claim against the source, a reader can
disagree. A source wrongly rejected leaves no trace in the draft at all.
Nothing downstream can find it, because there is nothing to find.

**It is sticky.** [DRAFT-ITERATION.md](DRAFT-ITERATION.md) records
rejected candidates in `rejected.md` precisely so a later revision does
not re-search and re-judge the same papers -- that repeat is the single
most expensive thing a fresh session does. `draft-reviser` is instructed:
if a candidate is listed there with a reason, **do not retrieve and
re-judge it**. That instruction is what makes revision cheap, and it is
also what makes a rejection permanent. Acceptances get revisited every
time the draft is edited; rejections do not get revisited at all.

**It propagates into claims.** A survey's deliverable includes what the
corpus does *not* cover. `deep-research` reports a "blind spot" and is
explicitly told that "no appropriate answer can be formulated from this
corpus" is a valid, honest output. Both of those turn a rejection into an
assertion about the world. A false rejection does not merely omit a
source -- it manufactures a gap.

So the question this document is really about is not "how do we retrieve
more cheaply" but **"how much evidence should stand behind the one
decision we never revisit?"**

## What the two-stage read costs

As built, it split retrieval into `triage` (a short window, for ruling
candidates out) and `evidence` (query-scored passages, for the survivors
only). What matters here is the arithmetic, because it is easy to get
backwards -- these are the numbers that were supposed to justify it.

Per sub-theme at `k=15`, in characters of payload reaching the caller:

| | Payload | vs one-stage |
|---|---|---|
| One-stage `search` | 15 x 500 = **7,500** | -- |
| Two-stage, 3 survive triage | 2,400 + 3 x 1,200 = **6,000** | -20% |
| Two-stage, 5 survive | 2,400 + 5 x 1,200 = **8,400** | +12% |
| Two-stage, 8 survive | 2,400 + 8 x 1,200 = **12,000** | +60% |

Three things follow, and all three are uncomfortable.

**The saving is conditional on rejecting hard.** Two-stage beats
one-stage below about five survivors and loses above eight. So the
economics depend on a skill following an instruction -- and models are
agreeable, so the natural failure is passing candidates through, which is
exactly the direction that makes it worse than what it replaced. A design
whose benefit rests on discipline, and whose failure mode is silent, is
fragile.

**It is a constant factor on a cost that was already removed.** The
original analysis behind [#65](https://github.com/prasadtalasila/chitragupta/pull/65)
and [#66](https://github.com/prasadtalasila/chitragupta/pull/66) put it
plainly: fix the *structural* cost first (no revision path at all, so
changing a paragraph meant re-running the pipeline), and the
context-trimming second, because it is "a constant factor on a run you'd
no longer need to make". The dossier removed the structural cost. This is
the lesser half, and it was always billed as such.

**The reliable reduction is somewhere else entirely.** The two cost pools
are orchestrator-resident (re-sent every remaining turn) and subagent
one-shot (paid once). Two-stage reduces *bytes retrieved*. The subagent
boundary changes *which pool those bytes land in* -- and that is the
larger and more certain effect, requires no retrieval change at all, and
works identically for every genre.

## The bug the split exposed

`_snippet` used to anchor its window on the first occurrence of whichever
query term came out of the term set first. `terms` is a Python `set`, and
string hashing is randomised per process, so **the same query on the same
document returned a different snippet from one run to the next** --
confirmed across four hash seeds, four different windows.

At a 500-character window this was a quality wobble; you got enough
context either way. Once `triage` cut the window to 160 characters and
made it the sole basis for rejecting a candidate, the same defect meant
an **irreproducible rejection**: run the same triage twice, discard a
different set of papers. And because `rejected.md` is trusted rather than
re-checked, whichever set won got entrenched for every later revision.

There was a second, related error of ordering. `evidence` scored its
windows properly by distinct-term coverage; `triage` inherited the
arbitrary snippet. The stage whose decision *cannot be undone* was
reading the worse evidence, and the stage that only ever runs on
candidates already accepted got the better machinery. `search` and
`evidence` now share one deterministic, best-covering chooser -- the part
of this work that outlived the split, and the reason plain `search` is
better than it was before any of this started.

Two lessons worth keeping, independent of two-stage:

- **Shrinking a window promotes the quality of window selection from a
  nicety to a correctness property.** The bug predated this work and sat
  in `search()` on `main` for everyone; making the window smaller is what
  made it matter.
- **Put the better machinery under the irreversible decision.** The
  instinct is to spend care on the thing you keep. The thing you discard
  is where care is unrecoverable.

## Why it does not generalise across genres

Two-stage was designed against `survey-writer`'s profile -- over-fetch at
`k=15`, keep about three -- and its saving is conditional on exactly that
shape.

| Genre | Citekeys in the shipped example | Fit | Dominant problem |
|---|---|---|---|
| `tutorial-writer` | 1 | **Poor** | Pure overhead |
| `textbook-chapter-writer` | 6 | Neutral | Neither helps nor hurts |
| `survey-writer` | 36 | **Best** | A false rejection becomes a false *gap claim* |
| `thesis-chapter-writer` | 26 | Mixed | Discards the sources that qualify an argument |
| `deep-research` | 11 | **Worst** | Solves a problem it does not have; breaks one it does |

**`tutorial-writer`** cites only in a closing "Where to go next", never
mid-lesson -- one to three pointers. The machinery is overhead against a
saving of a few hundred characters, on a skill whose stated virtue is a
single clean path. Its actual bottleneck is verifying the lesson runs,
which retrieval does not touch.

**`textbook-chapter-writer`** cites for motivation: the "this is a real
problem people care about" claim, which is what an abstract states, so a
short window is already sufficient. Least harmed, and least helped -- the
saving is noise against a handful of citations.

**`survey-writer`** is the genre it fits. The specific exposure is the
gap analysis and the comparison table: a false rejection converts covered
ground into a *reported gap*, which is a wrong claim in the output rather
than a missing one.

**`thesis-chapter-writer`** builds an argument tied to a research
question, so it needs sources weighed against each other -- including the
ones that complicate the claim. Those qualifications live in discussion
and limitations sections. Triage favours sources that state their
position early, which biases a chapter toward agreement and quietly
strengthens its argument by discarding friction.

**`deep-research`** is covered in its own section below.

## Was it worth building at all?

The fair answer is: **the split was not, and most of what was built
alongside it was.** Those are separable, and conflating them is what made
the question hard to answer.

What is valuable regardless of whether retrieval is split in two:

- **The window chooser.** `_windows` -- anchored on every occurrence of
  every term, scored by distinct-term coverage, de-overlapped,
  deterministic. It replaced a snippet that was both arbitrary and
  irreproducible, and it now serves one-stage `search()` too. Every
  caller in the repository is better off, including callers that never
  triage.
- **The retrieval CLI.** `python3 -m src.retrieval` replaced
  `python3 -c "from src import retrieval; ..."` one-liners whose output
  shape was whatever each skill's author happened to write, and which had
  nowhere to hang a flag.
- **`evidence` as a command.** "Show me the passages of this paper that
  bear on this query" is a useful operation on its own -- for a drafter
  checking a claim, for a reviser, for a human. It does not need a triage
  stage in front of it.
- **`--log` and `retrieval.md`.** Measurement is what turns the estimates
  in this document into numbers ([#76](https://github.com/prasadtalasila/chitragupta/issues/76)).
  Without it, the break-even claim stays an argument forever.

What is *not* carrying its weight:

- **`triage` as the default drafting path.** Its saving is conditional,
  narrow, unmeasured, and applies cleanly to one genre out of five. It
  buys an estimated 20% of one cost pool in the best case, and costs 60%
  more in the plausible bad case. Against that it introduces an
  irreversible, entrenched decision made on a third of the evidence, and
  doubles the retrieval instructions in every skill that adopts it.

**The split was removed before merging.** Keeping it as an available
mode was considered and rejected in turn: an option nobody defaults to is
an option nobody tests, its presence would keep inviting the
"light/thorough" toggle that
[the configuration section](#should-any-of-this-be-configurable) argues
against, and it is cheap to reinstate from this document if
[#76](https://github.com/prasadtalasila/chitragupta/issues/76) ever
measures a case for it. `search()` plus the subagent boundary is the
drafting path.

Two smaller things also came out of building it, which are worth stating
because they are the kind of return that does not show up in a diff. The
non-determinism bug was found only because shrinking the window made it
load-bearing; it had been shipping in `search()` for every caller. And
the arithmetic table above exists because an earlier version of the
defaults (3 windows of 700 characters) lost to one-stage in *every*
scenario and nobody had checked -- the check now lives in
`tests/test_retrieval.py::TestTwoStageCost`, and it is why the claim
cannot silently invert again.

## Recording how a rejection was made

`rejected.md` currently records *that* a candidate was turned down and
*why*, but not *on what evidence*. Those differ by nearly an order of
magnitude:

| Rejection made at | Evidence behind it | Invalidated by |
|---|---|---|
| **scope** ("adoption economics is out of scope") | none needed -- it is about the draft, not the paper | a scope change |
| **triage** | ~160 characters, one window | nothing, currently |
| one-stage `search` | ~500 characters | nothing, currently |
| **evidence** | ~1,200 characters, two query-scored passages | the claim changing |

A scope rejection is permanently valid until the scope moves, and
re-fetching it is pure waste. A triage rejection is the weakest judgment
the system makes, and is currently indistinguishable from the strongest.

Recording the stage would let `draft-reviser` apply a tiered rule instead
of a flat one: never re-fetch a scope rejection (re-read `scope.md`
instead), trust an evidence rejection, and **re-check triage rejections
when the change being made touches that sub-theme**. Revision stays cheap
by default and gets careful exactly where it is risky. It would also let
a survey's gap analysis be honest -- "12 of 19 candidates were ruled out
on a 160-character window and never read" is a real caveat on a claimed
gap -- and give `dossier status` a health signal worth printing.

The design tension is who writes the label. A model filling in one more
column can fill it in wrong, and a wrong label is worse than none because
it is trusted. Two alternatives: **derive** it by joining `rejected.md`
to `retrieval.md` on the query that surfaced the candidate (mechanical,
but joining on free text is fragile), or **have the tool write it** via a
`dossier reject` subcommand (most reliable, but trades away the
"Markdown a human can freely edit" property the dossier rests on).

## Why deep-research keeps the boundary alone

`deep-research` is the worst fit for the split, for reasons that are
independent of each other.

**It has already paid for the mechanism that matters.** Its interviewers
run in subagents, so everything they read is already one-shot rather than
orchestrator-resident. Two-stage inside a subagent optimises the cheap
pool. Meanwhile it adds cost the boundary was buying out: each `evidence`
call is a separate process (ledger open, index read), and at standard
depth -- 6 personas x 3 rounds x up to 3 reformulations, roughly 54
triage calls -- five survivors each means on the order of 270 further
process starts. Each is also a *turn* inside a subagent with its own
context budget, so more turns means more chance it exhausts context or
drifts before producing its packet.

**Contradiction mapping needs the disagreement that triage discards.**
Finding where sources conflict is the skill's whole point, and
disagreement is stated in discussion and limitations sections rather than
near a keyword hit. Best-covering-the-query-terms is still not "where
this paper qualifies its claim". A paper whose abstract agrees and whose
discussion dissents is either kept for the wrong reason or dropped before
anyone reads the dissent.

**It turns honest findings into artifacts of the screen.** The skill is
told that "no appropriate answer can be formulated from this corpus" is a
valid output, and it reports a blind spot and thin-coverage areas. Under
hard triage those statements silently mean "nothing survived a
160-character screen" -- a materially weaker claim presented as a strong
one. That is a truthfulness problem, not an efficiency one.

**Persona diversity is the method.** STORM's measured gain comes from
perspectives finding *different* things. "Reject hard at triage" is a
uniform pressure across all six personas, narrowing each toward the same
high-scoring core -- the homogenisation the multi-perspective design
exists to prevent.

The right way to cut `deep-research`'s cost is where it actually spends:
the interview packets held resident across Phases 3-7
([#74](https://github.com/prasadtalasila/chitragupta/issues/74)), not the
retrieval inside Phase 2.

## Should any of this be configurable?

There is precedent for judgment thresholds in `config.toml` --
`PROVENANCE_WEAK_SCORE` and `PROVENANCE_GOOD_SCORE` are exactly that. So
the instinct to move `160`, `500`, `600` and `2` there is reasonable. It
is still the wrong call, for four reasons.

**They are not independent knobs.** The break-even is a *function* of
`TRIAGE_CHARS`, `EVIDENCE_CHARS`, `EVIDENCE_WINDOWS` and `k` together.
Exposing them separately invites combinations that silently invert the
design's purpose -- which is not hypothetical: the 3-windows-of-700
default did exactly that. A test pins the documented arithmetic today; a
config value cannot be pinned by a test.

**The flexibility already exists at a better granularity.** `--chars`,
`--windows` and `--k` are per-call: explicit at the call site, visible in
`retrieval.md`, and not silently global. Config would make the same
choice *invisible*.

**`config.toml` is gitignored per-host data.** A dossier's rejections
would then depend on a setting recorded nowhere and differing per
machine. This repository has become careful about precisely this --
[ARCHITECTURE.md](ARCHITECTURE.md#what-is-reproducible-and-what-is-not)
and the `bench/repro_check.py` work that found two runs of the *same*
configuration are not exempt.

**One global toggle re-creates the mistake.** The right regime is
genre-dependent. A single value every skill reads is one-size-fits-all
again.

### On switching regimes between runs

The two-stage read is gone, so this is no longer a live proposal. It is
kept because the argument applies to *any* future setting that changes
how much evidence a rejection rests on.

The appealing version of this is a dial: a light revision uses two
stages, a thorough one uses a single stage. Note first that this has the
economics backwards -- one-stage is a flat 7,500 characters; two-stage at
three survivors is 6,000. Two-stage is not the thorough option. It is
cheap *when you reject hard* and expensive when you do not, and
"thorough" means keeping more survivors, which makes it cost more. A
boolean labelled light/thorough would mislabel its own cost model. What
the dial actually wants to be is a **depth preset** -- the shape
`deep-research` already uses (`quick`/`standard`/`deep`), where one name
moves several parameters coherently.

The real objection is what switching does to the dossier:

**A mixed dossier ratchets downward, and the weakest setting wins
permanently.** `rejected.md` accumulates across runs and is trusted by
later revisions. Run one light pass and its 160-character rejections sit
in the file forever; a later thorough revision reads them and skips
re-judging. So a cheap run's decisions contaminate every careful run that
follows. Rejections are sticky and acceptances are not, so the mixture
does not average -- it degrades to the least careful setting ever used.

That makes [recording the stage](#recording-how-a-rejection-was-made) a
**prerequisite** for configurability rather than a companion feature. It
also destroys the comparability `retrieval.md` exists to provide: if the
regime varies per run and is not recorded, its totals cannot distinguish
a cheap run from a cheap setting.

**Recommendation.** Do not add the four numbers to config. If a dial is
wanted, add named profiles (`thorough` = one-stage at 500; `frugal` =
triage/evidence) chosen *per genre by default* and overridable per
invocation -- and land the stage column first, so a dossier can say which
regime produced each entry. Revisit once
[#76](https://github.com/prasadtalasila/chitragupta/issues/76) has
measured whether the window sizes matter on a real corpus. Tuning knobs
on unmeasured defaults is premature.

## What was decided

- **The window chooser, the CLI, `evidence` and `--log` stay.** They are
  the durable value here and none of them depends on the split.
- **`_snippet` and `evidence` share one deterministic chooser**, so the
  rejection decision is reproducible and reads the best-covering passage.
- **The broad rollout is cancelled.**
  [#71](https://github.com/prasadtalasila/chitragupta/issues/71) ("wire
  the remaining four genre skills to the dossier *and* two-stage
  retrieval") is closed as not planned: it bundled a good idea with a bad
  one. The dossier half survives and needs re-planning on its own, since
  `draft-reviser` is unusable for four of five genres without it.
- **`triage` is removed**, along with `TRIAGE_CHARS`, its CLI subcommand,
  and the reject-hard instruction in every skill. `search()` plus the
  subagent boundary is the drafting path. `evidence` remains as a lookup
  for deepening an acceptance.
- **Nothing here is measured.** Every token figure is an estimate derived
  from file sizes and documented defaults. `retrieval.md` records
  characters, not tokens, and nothing records what a drafting turn costs.

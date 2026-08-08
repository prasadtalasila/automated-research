# Iterating on a draft

Status: **implemented.** Written 2026-08-06.

Why drafting costs what it costs, and how a draft is revised weeks later
without re-running the pipeline that produced it.

Related reading:

- [TOKENS.md](TOKENS.md) -- where a run's tokens go, the two-pool
  framing this document assumes, and how to measure any of it. The
  arithmetic that used to be in "Where the tokens go" below.
- [ARCHITECTURE.md](ARCHITECTURE.md) -- the three layers this sits inside.
- [RETRIEVAL.md](RETRIEVAL.md) -- how the corpus is ranked, and what a
  snippet actually contains.
- [REJECTION.md](REJECTION.md) -- why turning a source down is the
  load-bearing judgment here, and the accounting behind a retrieval change
  that was built and then withdrawn.
- [CITATION-PROVENANCE.md](CITATION-PROVENANCE.md) -- the review aid that
  answers "does the cited paper actually say this?", which is a different
  question from anything here.
- [PERFORMANCE.md](PERFORMANCE.md) -- **measured** costs of the corpus
  layer. Nothing in this document belongs there: the figures below are
  derived from file sizes and documented defaults, and are labelled as
  estimates every time they appear.

## Table of contents

- [The asymmetry](#the-asymmetry)
- [Where the tokens go](#where-the-tokens-go)
- [The dossier](#the-dossier)
- [Drift across every dossier](#drift-across-every-dossier)
- [Revising a draft](#revising-a-draft)
- [Backup and restore](#backup-and-restore)
- [What this deliberately does not do](#what-this-deliberately-does-not-do)

## The asymmetry

Half of this pipeline already survives a session ending, and half of it
doesn't.

`src/citation_gate.py`, `src/references.py`, `src/render_output.py` and
`src/citation_provenance.py` are all stateless with respect to *how* a
draft was written. Hand any of them a `.md` file from last month and they
work: the gate re-checks its citekeys, `references` rebuilds the
bibliography, `render_output` produces the PDF, `citation_provenance`
scores each claim against its source. None of them needs to know what
searches were run or which candidates were turned down.

The drafting layer is the exception. A genre skill's real product is not
only the draft -- it is also the judgment that produced it: which
sub-themes the topic was broken into, which of the fifteen retrieved
candidates were worth keeping, why the other twelve weren't, who the
reader is, which definition of a contested term the draft settled on, and
what the user asked for in chat that the prose doesn't show. Before this
module, all of that lived in one conversation and died with it.

So "shorten section 3" cost a full re-run: retrieve, score every
candidate again, re-cluster, rewrite. **That is a structural cost, not a
constant factor.** Everything else in this document follows from removing
it.

## Where the tokens go

**Moved.** The token accounting that was here now lives in
[TOKENS.md](TOKENS.md), together with the same argument from
[REJECTION.md](REJECTION.md) and the two worked examples that were in
neither. It is one subject and was being told in three places.

The part this document depends on, in one paragraph: costs split into
two pools, **orchestrator-resident** (re-sent on every remaining turn of
the run, and so multiplied by everything that comes after it) and
**subagent one-shot** (paid once, because the context is discarded when
the subagent returns). Four things load the first pool -- retrieved
candidates that are rejected but stay resident, fan-out packets held
across phases, whole-file rewrites, and **no revision path at all**.

The fourth is the one this module exists to remove, and it is the only
one of the four that is *structural* rather than a constant factor: before
`src/dossier.py` and the `draft-reviser` skill, no genre skill had a
branch for "an existing draft plus a change request", so the only way to
alter a paragraph was to run Phase 1 through Phase 7 again.

## The dossier

One dossier per draft, mirroring the draft's own path:

```
content/drafts/dt-for-engineers/survey.md
   -> content/dossiers/dt-for-engineers/survey/
```

That rule is mechanical, needs no registry, and handles both layouts the
repository actually contains -- the flat `content/drafts/<slug>.md` the
genre skills describe and the `content/drafts/<topic>/<genre>.md` the
shipped example content uses.

| File | What it holds | Status before this |
|---|---|---|
| `scope.md` | genre, reader, what the draft covers and excludes, glossary, corpus fingerprint | in the transcript only |
| `evidence.md` | each kept citekey, why it was kept, supporting quote or paraphrase | **specified** (survey-writer step 2) but written as JSON |
| `rejected.md` | candidates retrieved and turned down, with the reason | **nowhere** |
| `sections.md` | section heading -> the citekeys cited under it | **specified** (survey-writer step 8) but written as JSON |
| `steering.md` | what the user asked for in chat that the draft doesn't show | **nowhere** |
| `revisions.md` | append-only log of what changed and why | **nowhere** |
| `retrieval.md` | every retrieval call and the size of what it returned | **nowhere** |

Two of those were already specified and simply weren't durable. The
two that were missing entirely are the two that matter most:

- **`rejected.md`.** Without it, the next revision re-searches and
  re-judges the same papers. That single omission *is* the Phase-1-to-5
  re-run described above.
- **`steering.md`.** "Don't lead with tooling", "shorter", "drop the
  adoption angle" -- guidance that shaped the draft, is invisible in the
  prose, and had nowhere on disk to live.

### Why Markdown

Everything a dossier holds is read by a model or by a human, both of
which read Markdown natively. Nothing in it is a data structure another
module consumes -- `src/dossier.py` parses only two things out of it (the
corpus fingerprint line and backticked citekeys), and both degrade to
"unavailable" rather than to an error if a human has been editing freely.
A restored tarball is also legible on its own a year later, without this
code.

The cost of that choice is real: there is no schema, so nothing validates
that `evidence.md` is well-formed. This is accepted deliberately, on the
same principle as `src/citation_provenance.py` -- a check that blocked on
something it cannot verify exactly would train people to work around it.
A malformed dossier makes the next revision less efficient. It cannot
make a draft wrong, because the citation gate still stands between any
draft and the user.

### Why several files rather than one

So that a revision loads only what it needs. `scope.md` and `sections.md`
are small and almost always relevant; `rejected.md` is the largest and is
only needed when a change opens a sub-theme up for re-searching. One
combined file would have to be read whole every time, which is the cost
this module exists to avoid.

### Why not merge the provenance JSON into it

`thesis-chapter-writer` and `deep-research` also write
`content/provenance/<slug>.json`. Both artifacts are kept, and neither
replaces the other, because they answer different questions for different
readers:

| | `content/provenance/*.json` | `content/dossiers/<draft>/` |
|---|---|---|
| Shape | JSON, machine-readable | Markdown, human-readable |
| Holds | section -> citekey, and why that source supports that claim | reader, scope, glossary, kept evidence, **rejected candidates and why**, steering |
| Read by | tooling, and a reviewer auditing one claim | `draft-reviser`, and a human months later |
| Lost if absent | an audit trail for a finished draft | the ability to revise without re-running the whole topic |

The overlap is one column of `sections.md`. Collapsing them would mean
either putting prose a human needs into JSON, or putting a machine record
into Markdown that nothing parses -- so they stay separate, and the two
skills that produce both write both.

### The corpus fingerprint

`scope.md` records how many citekeys the ledger held when the draft was
written, plus a 12-character digest of that set:

```
- corpus: 501 citekeys, digest `a1b2c3d4e5f6`
```

`python3 -m src.dossier status` recomputes it. If it differs, the corpus
has moved, and the command names the citekeys that appear nowhere in the
dossier -- neither kept nor rejected -- so a reviser can see what was
never considered rather than just that a number changed.

The ledger is opened read-only with `timeout=0`, exactly as
`python -m src.ledger` does: this is an inspection, and it must not take
a write lock, run a migration, or block behind a sync that is mid-run.
**Drift is not itself a reason to redraft.** It is a reason to re-search
if, and only if, the change being made touches a sub-theme the new papers
could bear on.

## Drift across every dossier

`status <draft>` answers "did the corpus move under this one draft?",
which is only useful if you already suspect it did. The corpus half of
the bottleneck is the one nobody is watching: `src.sync` adds papers and
`--remove-stale` drops them, and every draft written before that moment
silently drifts. `status --all` is the sweep that makes it visible --
one report over every dossier under `content/dossiers/`, always exiting
0, because "some drafts have drifted" is the normal state of a live
corpus and not a build failure.

### Two findings, and they are not the same kind of thing

| Finding | What it is | What it costs to ignore |
|---|---|---|
| **Missing** -- a cited citekey that has left the ledger | a defect | the draft cites a paper the corpus no longer has; the citation gate will fail on it |
| **Candidate** -- a newly reachable paper the dossier never weighed | a decision | nothing, until the sub-theme it bears on is revised |

Collapsing the two into one "drift" number was the thing worth avoiding.
A missing citekey is work that has to happen; a candidate is an offer
that is usually correct to decline. Reported together as a count, the
first hides inside the second.

**Missing is computed from `evidence.md` and `sections.md` only, not from
`rejected.md`.** A paper the draft *stands on* vanishing is a defect; a
paper the draft *turned down* vanishing is a non-event, and reporting the
second as the first would make every `--remove-stale` run look like a
pile of broken drafts. Each missing citekey is reported with the
`sections.md` sections that cite it, because the reviser's next question
is always "which prose leans on this?" and reading the draft to find out
is exactly the cost this document exists to remove.

**Candidates are matched against the queries in `retrieval.md`.** That
file was added to measure what a run cost -- how many characters each
retrieval call returned. It turns out to be the only record of *what
this draft went looking for*, which is what makes "the corpus grew"
answerable as "and here is the part of the growth this draft would have
wanted". Each recorded query is re-run against the current corpus, and
anything landing in its top 15 that the dossier has never mentioned is a
candidate. Fifteen is not arbitrary: it is `survey-writer`'s own
`search(sub_theme, k=15)`, so the report surfaces a paper if and only if
the draft's original search would have put it in front of the writer.

**Everything in `rejected.md` is subtracted first.** Re-offering a
candidate the draft already read and declined would cost precisely the
re-judging that `rejected.md` exists to prevent -- the report would
recreate the bottleneck it is meant to expose.

### The third list, and why it is not drift

Subtracting the rejections throws away something worth keeping, though.
"Turned down because the corpus had nothing better on this sub-theme"
and "turned down because it is about a different field" age completely
differently, and only the first is worth revisiting once the corpus
grows. Membership in `rejected.md` cannot tell those apart; the reason
column can.

So a declined paper that the dossier's queries *still* reach is reported
as a third list, `reconsider`, carrying the recorded reason. A citekey
that is both cited and listed as rejected is treated as cited -- that is
a stale `rejected.md` row, not an open question, and offering it back
would send a reviser to re-decide something the draft already acts on.

**It deliberately does not count as drift.** A rejection that still
matches its query was true before the corpus moved and will be true on
every sweep after it. Letting it mark a dossier stale would mean every
dossier that ever declined a paper is permanently stale, which destroys
the one signal this command exists to give. So `reconsider` never makes
a dossier "drifted", and the terminal output prints it only alongside a
real finding -- context for a re-grounding pass that is already
warranted, rather than a standing reminder. `--json` always carries it,
because there the consumer reads it at the moment it acts.

### Why the new papers are not found with `search()`

The obvious implementation -- call `src.retrieval.search(query, k=15)`
for each recorded query -- is the one thing this could not do, for two
reasons that are both about a report having no business changing what it
reports on:

- `search()` reaches the ledger through `ledger.connect()`, which mkdirs
  `content/`, executes the schema and runs migrations. That is a write
  connection, and the whole point of `status` opening the ledger
  `mode=ro, timeout=0` is that an inspection must not take a write lock
  or block behind a sync that is mid-run.
- `search()` builds its index through `retrieval._load_index`, which
  calls `_save_cache()` whenever any document's fingerprint has moved.
  After the sync that caused the drift being reported, that is not an
  edge case -- it is guaranteed. The scan would rewrite
  `content/retrieval_index.json` every time it ran.

**The BM25 index was never in the ledger to begin with**, which is what
makes the alternative easy. The ledger holds bibliographic rows and a
`parsed_path`; the index is a separate `content/retrieval_index.json`
mapping each citekey to `{fingerprint, length, term_freqs}`, derived from
the parsed text. And the two halves of `src.retrieval` that matter --
`_tokenize_item` and `_bm25_scores` -- are pure; the only thing that
persists is the cache write sitting between them.

So the drift scan composes those two halves and skips the middle. It
reads the existing cache with `_load_cache()` (which only reads), reuses
every entry whose fingerprint still matches, tokenizes the rest into
memory, ranks against that, and drops the whole thing when the scan
returns. The ledger is read once and the index built once and lazily, so
a sweep over dossiers that logged no queries never builds one at all.
Nothing is written back, and the smoke test pins exactly that:
`content/ledger.sqlite` and `content/retrieval_index.json` are both
byte-identical after a scan.

The cost of throwing the index away is that a cold-cache sweep re-does
work the next `search()` will do again. That is accepted deliberately.
The alternative is a read-only command that writes to the corpus layer
as a side effect, which is a much worse thing to owe the reader.

**What that costs is measured, not assumed** -- and unlike the estimates
in [Where the tokens go](#where-the-tokens-go), it is a stopwatch figure,
so it lives in
[PERFORMANCE.md](PERFORMANCE.md#what-a-drift-sweep-costs) with the rest
of the measurements. The load-bearing result, on this project's own
corpus: sweeping 50 dossiers costs **0.24s more than sweeping one**
(2.368s vs 2.126s cold), because the tokenization is shared. Had it been
per dossier, 50 would have taken nearly two minutes. A warm cache is
5.4-9.6x faster again. The first draft of this section called a warm
sweep "nearly free"; the measurement says about 0.2-0.4s warm and ~2.1s
cold -- cheap enough to run after every sync, but not nothing, and the
wording here was corrected to match.

### Unknown is not the same as absent

A dossier on a machine with no readable ledger produces no findings, and
reporting that as "current" would be the one way this command could
actively mislead -- it would assert the result of a check that never ran.
The sweep says so explicitly and still exits 0.

### `--json`, and who it is for

`--all --json` emits the same report as data: per dossier, the recorded
and current fingerprints, `missing` as citekey -> citing sections,
`candidates` as citekey, title and the queries that surfaced it, and
`reconsider` as the same plus the recorded rejection reason. This
exists because the consumer is not only a human. A re-grounding pass has
to swap the missing citations, triage the candidates, and edit only the
affected sections -- and having it re-parse a report written for a
terminal would be a fragile way to hand over structured facts it already
knows. Exiting 0 regardless is part of the same contract: the caller
branches on the contents, not on the status code.

## Revising a draft

The `draft-reviser` skill reads the dossier instead of the corpus. Its
loop:

1. `python3 -m src.dossier status <draft>` -- what is on disk, and has
   the corpus moved?
2. Read `scope.md` and `steering.md`. These bound what the revision may
   change: a request that contradicts the recorded scope is a scope
   change, and gets said out loud rather than silently applied.
3. `python3 -m src.dossier sections <draft>` -- heading to line range.
4. Read *only* the affected sections, at those line ranges, and edit
   inside them.
5. Re-search only if the change genuinely opens new ground, consulting
   `rejected.md` first so the same candidates aren't re-judged.
6. Update `evidence.md` / `rejected.md` / `sections.md` for whatever
   actually changed, append to `revisions.md` and `steering.md`.
7. Re-gate (`python -m src.citation_gate`), rebuild references, re-render.

Steps 3 and 4 are where the output-token saving lives: a scoped edit
inside one section replaces an estimated ~4.6k-token whole-file rewrite.
Steps 1, 2 and 5 are where the input-token saving lives: no retrieval
pass at all in the common case.

### Section anchors

`sections` extracts the outline from the draft itself rather than from
stored state, so it cannot go stale, and it survives a draft that was
hand-edited outside this pipeline.

It skips code first, which is not a nicety. The shipped example
`tutorial.md` is mostly shell and Python, and a `# Step 1: ...` comment
inside a fenced block is indistinguishable from a Markdown heading to
anything that doesn't track fences -- an outline built without that
reports sections that don't exist and hands a reviser line ranges that
cut a code block in half. Markdown fences (``` and `~~~`) and LaTeX
`verbatim`/`lstlisting`/`minted` environments are both tracked, since
`thesis-chapter-writer` emits `.tex`.

## Backup and restore

`content/dossiers/` is gitignored, like `content/drafts/` and
`content/rendered/` before it. That is a deliberate choice, not an
oversight: `evidence.md` quotes passages from copyrighted sources, and
this project already treats per-host content as the user's own to keep.
Nothing under `content/dossiers/` is tracked, and no example one ships --
a dossier is a record of a real run, and one assembled to be looked at
would be a reconstruction wearing a record's clothes.

What replaces version control is an explicit bundle:

```bash
# everything
python3 -m src.dossier export

# one topic, including rendered PDFs
python3 -m src.dossier export digital-twins-for-software-engineers --with-rendered

# restore -- a dry run that reports what it would write
python3 -m src.dossier restore drafts-all-2026-08-06.tar.gz
python3 -m src.dossier restore drafts-all-2026-08-06.tar.gz --force
```

Three properties worth knowing:

- **Archive paths are relative to `content/`, not to the repo root**, so
  a bundle restores correctly into a checkout whose `[content].dir`
  points somewhere else.
- **Restore is a dry run unless `--force`.** It is the only destructive
  operation in the module, and the case it exists for -- "I need last
  month's draft back" -- is exactly the case where the working copy might
  be something you would rather not lose to a mistyped archive name.
- **An unsafe member refuses the whole archive**, rather than being
  skipped. A member is unsafe if it is not a regular file or directory
  (a symlink, a device node), if it escapes the extraction directory, or
  if its top-level directory is not one of `drafts/`, `dossiers/`,
  `rendered/`. A partially extracted backup is worse than none, because
  it looks like it worked.

### What a bundle does not carry

`content/ledger.sqlite` and `papers/bibliography.bib`. The ledger is
regenerable with `python -m src.sync`, and the bib file is your reference
manager's export -- AGENTS.md's invariant is that the bib file is the
source of truth *and not this pipeline's to own*, so a bundle does not
start keeping copies of it. Back it up where you back up that tool.

The practical consequence: restore a bundle onto a machine with no
corpus and the drafts and dossiers are all there and readable, but the
citation gate cannot verify anything until `sync` has run. That is the
correct failure -- the gate refusing to confirm a citekey it cannot see
beats a gate that passes because there is nothing to check against.

## What this deliberately does not do

**It is not a gate and it takes no lock.** Nothing in `src/dossier.py`
blocks a draft, and nothing in it writes to the corpus layer. A dossier
that is missing, stale or hand-edited degrades the next revision's
efficiency and can never make a draft wrong.

**It does not verify that a dossier matches its draft.** `sections.md`
can disagree with the draft's actual headings if someone edits by hand.
The reviser rebuilds the section map from the draft rather than trusting
the file, and `src/citation_provenance.py` already reconciles a draft
against its sources independently.

**It does not itself cut what enters the orchestrator's context.** That is
the other half of the problem, and the answer turned out not to be
trimming what retrieval returns -- see [REJECTION.md](REJECTION.md) for
why a cheaper first read was built and then withdrawn. What does work is
the subagent boundary: a genre skill on a broad topic dispatches one
subagent per sub-theme and keeps only the kept-evidence packet, so the
candidates it discarded are paid for once instead of sitting resident for
the whole run. The dossier's job is the *structural* cost -- not
re-running the pipeline at all -- and the two are complementary: the
cheapest retrieval pass is still more expensive than the one you didn't
have to make.

**It does not measure token counts directly.** `retrieval.md` records the
character payload of each retrieval call, not tokens, and nothing records
what the drafting turns themselves cost. That is enough to compare one
run against another on a real corpus; it is not enough to put a number on
a whole draft. The estimates in [TOKENS.md](TOKENS.md) remain estimates,
and are labelled as such there.

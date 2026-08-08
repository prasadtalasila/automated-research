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

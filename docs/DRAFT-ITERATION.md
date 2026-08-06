# Iterating on a draft

Status: **implemented.** Written 2026-08-06.

Why drafting costs what it costs, and how a draft is revised weeks later
without re-running the pipeline that produced it.

Related reading:

- [ARCHITECTURE.md](ARCHITECTURE.md) -- the three layers this sits inside.
- [RETRIEVAL.md](RETRIEVAL.md) -- what `search()` returns and how it ranks.
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

**Every figure in this section is an estimate**, derived from file sizes
in `content/drafts/` and the defaults documented in the genre skills.
There is no token telemetry in this repository yet; adding it is
[future work](#what-this-deliberately-does-not-do). Read the ratios, not
the absolute numbers.

The useful split is between two pools, because they are billed
differently:

| Pool | Billed | Examples |
|---|---|---|
| **Orchestrator-resident** | once per turn, for every remaining turn of the run | retrieval snippets read inline, interview packets, the assembled draft |
| **Subagent one-shot** | once | anything read or written inside a dispatched subagent |

Prompt caching blunts the first pool; it does not remove it, and it
expires. Resident tokens are the expensive kind, and everything below is
about them.

### 1. Retrieved candidates that never leave context

`survey-writer` step 1 calls `search(sub_theme, k=15)` for two to four
sub-themes, over-fetching on purpose. Each `SearchResult` carries a
citekey, a title, a score and a 500-character snippet -- **an estimated
~150 tokens each**, so 30-60 results is an estimated **4.5k-9k tokens per
retrieval pass**. Step 3 then tells you to reformulate and search again
when a sub-theme comes up thin.

The sharp part is what happens next. `reference.md` §1 sets "results kept
per query ≈ top 3" out of fifteen. **The roughly 80% that get rejected
cost exactly what the kept ones cost, and then stay resident for the rest
of the run anyway.** Rejecting a candidate saves no tokens at all; it only
saves you from citing it.

### 2. Fan-out results held across phases

`deep-research` Phase 2 dispatches six interviewers and holds their
packets through Phases 3, 4, 5, 6 and 7 -- the contradiction map, the
outline, the section writers, the polish pass and the peer-review
reconciliation all read them. An estimated ~1k tokens per packet is ~6k
tokens re-sent across the longest stretch of the run.

### 3. Whole-file rewrites

`content/drafts/digital-twins-for-software-engineers/survey.md` is 18.3
KB, an estimated **~4.6k output tokens to write once**. Output tokens are
the expensive direction, and a draft that is rewritten whole for each
revision pays that every time -- including for a gate failure that
touches one citekey.

### 4. No revision path at all

This was the big one, and it is what `src/dossier.py` plus the
`draft-reviser` skill exist to remove. Before them, no genre skill had a
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

Two of those six were already specified and simply weren't durable. The
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

### Why six files rather than one

So that a revision loads only what it needs. `scope.md` and `sections.md`
are small and almost always relevant; `rejected.md` is the largest and is
only needed when a change opens a sub-theme up for re-searching. One
combined file would have to be read whole every time, which is the cost
this module exists to avoid.

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
The example dossier under
`content/dossiers/digital-twins-for-software-engineers/` is in git only
because it was force-added as example content, exactly as the example
drafts were.

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

**It does not cut what enters the orchestrator's context in the first
place.** The two-pool analysis above says the largest single constant
factor is retrieved-and-rejected candidates sitting resident for a whole
run. Fixing that means a two-stage retrieve -- a cheap reject-only triage
pass, then a full-context fetch for survivors only -- and moving
retrieval behind a subagent boundary. That is a change to
`src/retrieval.py` and to the genre skills, and it is separate work from
this.

**There is no token telemetry.** Every figure in
[Where the tokens go](#where-the-tokens-go) is an estimate. A thin
wrapper around `search()` recording `{query, k, chars_returned, n_kept}`
into the dossier would turn all of them into measurements essentially for
free, since the dossier is being written anyway. It belongs with the
retrieval work above, not here.

---
name: draft-reviser
description: Revises an existing draft in content/drafts/ from its dossier (content/dossiers/<same path>/) instead of re-running the genre skill that produced it -- reads the recorded scope, reader, glossary, kept evidence and rejected candidates, edits only the affected sections, and logs what changed. Triggers when the user asks to revise, shorten, expand, restructure, re-target or correct a draft that already exists, including in a session that did not write it. Also handles re-grounding after the corpus moves -- triggers on "re-ground", "the corpus moved", "a cited paper left the corpus", "this draft has drifted", or a `dossier status --all` report naming a draft, and consumes that report as JSON to propose a scoped fix rather than a re-draft. Use the genre skill (survey-writer, thesis-chapter-writer, textbook-chapter-writer, tutorial-writer, deep-research) for a NEW draft, never for a change to an existing one. Must pass `python -m src.citation_gate` before presenting and never invents a citekey.
tags: [revision, dossier, citation]
---

# draft-reviser

Revising a draft by re-running the genre skill that wrote it is the most
expensive mistake available in this repository. A fresh run re-retrieves,
re-scores every candidate, re-clusters and rewrites the whole file --
to change one paragraph. This skill exists so that never has to happen.

The reason it can work is that the judgment behind a draft is on disk:
`content/dossiers/<draft path minus suffix>/` holds the reader, the
scope, the glossary, the kept evidence, the rejected candidates and the
steering the user gave in chat. See `docs/DRAFT-ITERATION.md` for why it
is shaped that way.

## When to invoke

| Situation | Action |
|---|---|
| User asks to shorten, expand, restructure, re-target, correct or update an existing draft | Invoke this skill |
| User asks for a **new** draft on a topic | Use the matching genre skill |
| The draft exists but has no dossier | Bootstrap one (below), then continue here |
| A sync moved the corpus, or `dossier status --all` names this draft | Re-grounding mode (below), not the ordinary loop |
| User asks for a different genre of the same topic | That's a new draft -- use the genre skill |
| Ledger is empty or absent | Revise anyway if the change touches no citations; say so. **Never** run `src.sync`. In re-grounding mode, stop instead -- the ledger *is* the request |

**Read-only over the corpus layer.** Never run `python -m src.sync` and
never run `scripts/enrich.py`. Both take the pipeline's write lock and
can run for tens of minutes; they are the user's to run.

## Prose standards

`docs/WRITING-STANDARDS.md` applies unchanged. Two of its rules bind
harder here than in a fresh draft, because a revision is the moment they
break:

- **The reader is already fixed.** `scope.md` names them. A revision that
  quietly writes for someone else produces a draft with two audiences.
- **Terminology is already fixed.** `scope.md`'s glossary is the
  definition the rest of the draft uses. Introducing a second name for a
  concept in the one section you touched is the exact seam a reader
  notices.

## The loop

### 1. Locate the draft and read its state

```bash
python3 -m src.dossier status content/drafts/<path>
```

This prints which dossier files are filled in, the draft's section count,
and whether the corpus has moved since the draft was written. It never
fails on a missing ledger or a missing dossier -- it reports.

Then read `scope.md` and `steering.md`. **Always both, always first.**
They are small, and they are what stops a revision from undoing an
earlier decision the user already made.

### 2. Check the request against the recorded scope

If the change contradicts `scope.md`'s "Covers"/"Does not cover", say so
in one sentence and ask -- do not silently widen the draft. "You asked
for adoption economics; scope.md excludes it. Add it and update the scope
statement, or leave it out?" A scope change is a legitimate answer; a
scope change made without saying so is not.

### 3. Map the change onto sections

```bash
python3 -m src.dossier sections content/drafts/<path>
```

Read **only** the sections the change touches, using the printed line
ranges (`Read` with `offset=<start>`, `limit=<lines>`). Do not read the
whole draft to change one section. Consult `sections.md` when you need to
know which section owns a citation without reading anything.

The exception: a change that alters the draft's argument (restructuring,
re-targeting, a claim that other sections lean on) needs a read of the
whole draft. Recognise that case and pay for it deliberately, rather than
defaulting to it.

### 4. Decide whether you need to search at all

Most revisions don't. Before any retrieval call:

- Check `evidence.md` -- the supporting quote may already be recorded.
- Check `rejected.md` -- if a candidate is listed there with a reason,
  **do not retrieve and re-judge it**. That list exists precisely to stop
  the most expensive repeated work in the pipeline.

Search only when the change opens genuinely new ground. If it does:

```bash
python3 -m src.retrieval search "<query>" --k 15 --log content/drafts/<path>
python3 -m src.retrieval evidence "<query>" --citekey <key> --log content/drafts/<path>
```

(or `src.enrich.embed_index.search()` in place of `search` where the
embedding stack has been built). `evidence` is optional -- reach for it
when a snippet is not enough to decide on a source you are minded to
cite. Score what you keep as `survey-writer` step 2 describes, and record
both outcomes: kept into `evidence.md`, turned down into `rejected.md`.

`--log` keeps `retrieval.md` honest about what this revision actually
cost, which is the number that tells you whether revising from the
dossier is paying off.

If `status` reported corpus drift, read the named citekeys only if they
bear on the sub-theme you are changing. **Drift is not itself a reason to
redraft**, and a revision request is not a mandate to refresh the whole
draft against a corpus that grew. That holds for a corpus that *gained*
papers. It does not hold for one that lost a paper the draft cites --
that is a broken citation, the gate will fail on it, and it is fixed
whether or not anyone asked. See "Re-grounding after the corpus moves".

### 5. Edit in place, inside the section

Use `Edit` on the specific passage. Do not `Write` the whole file: a
whole-file rewrite of a survey-length draft costs thousands of output
tokens, re-runs the citation-gate hook over everything, and produces a
diff the user cannot review.

Never write a citekey that isn't already in the draft, in `evidence.md`,
or in a `search()` result you just read. AGENTS.md's invariant is
unchanged here: **a fabricated citekey is the one failure this whole
pipeline exists to prevent.**

### 6. Write the dossier back

Update only what actually changed:

- `evidence.md` -- new kept citekeys, with relevance and support
- `rejected.md` -- anything newly retrieved and turned down. Bear in mind
  that a later revision is told to trust this file rather than re-judge
  what is in it, so a reason worth reading later is worth writing now
  (`docs/REJECTION.md`)
- `retrieval.md` -- nothing by hand; `--log` appends to it for you
- `sections.md` -- if headings or their citations moved
- `scope.md` -- only if the user agreed to a scope change in step 2
- `steering.md` -- append the instruction that prompted this revision,
  dated. This is the part with nowhere else to live; skipping it is how
  the next session loses the thread.
- `revisions.md` -- append one entry: date, what changed, which sections,
  and why.

### 7. Gate, reference, render

```bash
python -m src.citation_gate content/drafts/<path>
python -m src.references content/drafts/<path>          # .md drafts
python3 -m src.render_output content/drafts/<path> --format tex
python3 -m src.render_output content/drafts/<path> --format pdf
python3 -m src.render_output content/drafts/<path> --format md
```

Fix and re-run until the gate reports `OK`. **Never present a draft that
hasn't passed.** A `[missing-binary]` or `[error]` from `render_output`
is a one-line warning in chat and does not block presenting.

## Re-grounding after the corpus moves

When `python -m src.sync` adds papers or drops stale ones, every existing
draft moves with it and nothing says so. `dossier status --all` is what
notices; this is what acts on it. It is the same loop entered from a
report instead of a request, so steps 5, 6 and 7 above still apply
verbatim -- what changes is how the work is found.

### R1. Read the report as data

```bash
python3 -m src.dossier status content/drafts/<path> --json
```

Or take the payload from a `--all --json` sweep the user already has. The
envelope is always `{"dossiers": [...]}`, so a single draft comes back as
a one-element list: read `.dossiers[0]`, not a bare object.

### R2. Branch on the payload, never on the exit code

This command exits 0 almost unconditionally -- that is deliberate, so the
caller reads the contents rather than a status. Two cases to check before
anything else:

- **`corpus_available` is `false`.** The ledger could not be read, so
  every finding list is empty because the check never ran, not because
  there is nothing to find. Say what you checked, point the user at
  `python -m src.sync`, and stop. Do not report the draft as current.
- **The dossier does not exist.** `--json` returns an almost-empty entry
  and still exits 0. Go to "When there is no dossier", bootstrap, and
  come back.

### R3. Act on the three lists -- they are not the same kind of thing

Flattening them into one list of "papers to look at" is the failure mode
this section exists to prevent.

**`missing` is a defect.** The draft stands on a paper the corpus no
longer has; `citation_gate` already disagrees with the draft. Always
actioned, whatever else the revision is about. Each entry maps a citekey
to the sections citing it, and `python3 -m src.dossier sections
content/drafts/<path>` turns those into line ranges, so the edit stays as
scoped as any other. Look for the replacement in this order, and stop at
the first that supports the claim:

1. `evidence.md` -- another paper you already kept may support it, at no
   retrieval cost at all.
2. The report's own `candidates` -- a paper that arrived matching the
   same query that once produced the broken citation is the likeliest
   replacement there is.
3. `search "<the claim>" --k 15 --log content/drafts/<path>` -- here a
   fresh search *is* right, unlike the candidate path, because a claim
   left unsupported is genuinely new ground.

If none of the three supports it, **remove the claim** and say so. Not a
reworded sentence that keeps the assertion and quietly drops the
citation. Never leave the citekey in place, and never replace it with a
key you have not seen in the ledger.

**`candidates` are a decision, not a defect.** New papers that this
dossier's own recorded queries reach. Pursue only the ones whose
`queries` touch the sub-theme actually in play; the rest are reported to
the user and left in the report for the next revision to weigh. Do not
work through the list.

For the ones you do pursue, go straight to the passage. The report
already carries the citekey, the title and the query that surfaced it, so
re-running `search` for that query pays for fifteen snippets to be handed
back the same fifteen citekeys:

```bash
python3 -m src.retrieval evidence "<the query from the report>" \
    --citekey <candidate> --log content/drafts/<path>
```

What the report lacks is text to judge on, and that is what `evidence` is
for. Keep `search "<query>" --k 15 --log ...` for the case where the
revision opens ground the dossier never covered -- a query not already in
`retrieval.md`, which by definition could not have produced a candidate.

**`reconsider` is not re-judged.** These are papers the draft already
read and turned down, which its queries still reach. `rejected.md` has
already been subtracted from `candidates`; these are carried separately
*with the recorded reason* so you can weigh the reason without paying to
re-judge the paper. Report citekey, title and reason. Re-open one only
when the recorded reason no longer holds -- typically a scope change the
user agreed to in step 2. Re-judging these by default is precisely the
cost `rejected.md` exists to prevent (`docs/REJECTION.md`).

### R4. Edit, write back, and re-stamp

Step 5 unchanged. Step 6 unchanged except for two of its bullets, which
were written for a revision someone asked for:

- **`steering.md` -- usually nothing.** A re-grounding pass has no
  instruction to record; the corpus moved, the user did not steer.
  Append only if they actually said something here. Inventing a steering
  entry to fill the file is the same failure as inventing an evidence
  one.
- **`scope.md` -- the fingerprint line only.** The rule that the scope
  statement changes only by agreement is untouched; the corpus line
  below is bookkeeping, and is the one thing this mode always writes.

Then two things specific to this mode.

**Re-stamp the corpus fingerprint** in `scope.md`, after the gate passes,
from the report's `current` field -- it is the record that this draft was
re-grounded against that corpus:

```
- corpus: 503 citekeys, digest `f6e5d4c3b2a1`
```

Rewrite the line; do not reshape it. Anything that no longer matches the
recorded form makes `recorded_corpus()` return nothing, and the dossier
silently downgrades to "records no corpus fingerprint" instead of
erroring.

**Append a `revisions.md` entry** naming this as a re-grounding: what was
swapped, what was dropped, what was added, and -- with the same weight --
which candidates were reported and *not* pursued. The Guardrails rule
about reporting what you didn't do binds hardest here, because the user
did not ask for this revision and cannot infer its edges.

### R5. The gate is the exit, not the report

Finish with step 7 and change nothing about it. `missing` is computed
from the dossier's own `evidence.md` and `sections.md`, not from the
draft body, so a citekey the draft cites that was never recorded in the
dossier will not appear in the report at all. `python -m
src.citation_gate` is the check that reads the draft, and it is what
decides the draft is presentable. A clean drift report never does.

Expect the draft to keep showing candidates in the next sweep, and say
so. A query returns fifteen hits and a revision accepts one or two;
"still has candidates" is the normal state of a healthy draft. What
re-grounding promises is that the *missing* list is empty. Do not clear
the candidate list by writing unpursued papers into `rejected.md` -- a
rejection recorded from a title alone is a judgment you did not make, and
`rejected.md` is trusted permanently by every revision after this one.

## When there is no dossier

Drafts written before `src/dossier.py` existed have none, and so do
drafts written by hand. Bootstrap rather than refusing:

```bash
python3 -m src.dossier init content/drafts/<path> --genre <genre>
```

Then fill in what the draft itself can tell you -- `sections.md` from
`python3 -m src.dossier sections`, and `scope.md`'s reader/covers/excludes
from the draft's own scope paragraph if it has one. Leave `evidence.md`
and `rejected.md` empty and **say so in chat**: the first revision of a
bootstrapped draft cannot check a claim against recorded evidence, and
may have to re-retrieve for a sub-theme that a real dossier would have
answered from disk. It gets cheaper from the second revision on.

Do not invent evidence entries to fill the file. An empty `evidence.md`
is honest; a fabricated one is the same failure class as a fabricated
citekey.

## Guardrails

- **Never re-run the genre skill to make a change.** If the request truly
  needs a new draft, say that and hand off explicitly.
- **Never run `python -m src.sync` or `scripts/enrich.py`.**
- **Never fabricate a citekey**, and never "fix" a gate failure by
  inventing a plausible-looking key -- correct it or remove the claim.
- **Never silently change scope, reader or terminology.**
- **Never record a rejection you did not make.** Writing an unpursued
  candidate into `rejected.md` to tidy a drift report turns a title into
  a permanent judgment that every later revision trusts.
- **Report what you didn't do.** If the change requires re-searching a
  sub-theme and you judged it out of scope for this revision, say so
  rather than leaving a half-updated draft that looks finished.

## Sources

The prose standards this skill inherits are documented, with per-principle
attribution, in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
What bears on revision specifically is Google's *Technical Writing
Courses* (CC-BY 4.0) rule that one concept keeps one name: in a fresh
draft that is a style preference, but a revision touching one section of
a document written weeks ago is exactly where a second name for an
existing concept gets introduced, which is why `scope.md`'s glossary is
read before anything is edited rather than checked afterwards.

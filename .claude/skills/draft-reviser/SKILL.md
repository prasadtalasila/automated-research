---
name: draft-reviser
description: Revises an existing draft in content/drafts/ from its dossier (content/dossiers/<same path>/) instead of re-running the genre skill that produced it -- reads the recorded scope, reader, glossary, kept evidence and rejected candidates, edits only the affected sections, and logs what changed. Triggers when the user asks to revise, shorten, expand, restructure, re-target or correct a draft that already exists, including in a session that did not write it. Use the genre skill (survey-writer, thesis-chapter-writer, textbook-chapter-writer, tutorial-writer, deep-research) for a NEW draft, never for a change to an existing one. Must pass `python -m src.citation_gate` before presenting and never invents a citekey.
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
| User asks for a different genre of the same topic | That's a new draft -- use the genre skill |
| Ledger is empty or absent | Revise anyway if the change touches no citations; say so. **Never** run `src.sync` |

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

Search only when the change opens genuinely new ground. If it does, use
`src.retrieval.search()` (or `src.enrich.embed_index.search()` where
built), score candidates as `survey-writer` step 2 describes, and record
both outcomes -- kept into `evidence.md`, turned down into `rejected.md`.

If `status` reported corpus drift, read the named citekeys only if they
bear on the sub-theme you are changing. **Drift is not itself a reason to
redraft**, and a revision request is not a mandate to refresh the whole
draft against a corpus that grew.

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
- `rejected.md` -- anything newly retrieved and turned down
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

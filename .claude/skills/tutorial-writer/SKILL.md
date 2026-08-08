---
name: tutorial-writer
description: Drafts a Diataxis-style tutorial -- a hands-on lesson that a learner follows at a keyboard, start to finish, to a working result they can see. Concrete, single-path, minimally explained, and verified to actually run before it is presented. Not a textbook chapter and not a how-to guide; if the reader is studying rather than doing, use `textbook-chapter-writer`, and if they already know what they want and just need the steps, say so rather than writing a tutorial. May cite the synced corpus (content/ledger.sqlite via src.retrieval.search()) but only in a closing "Where to go next" section, never mid-lesson. Triggers when the user asks for a tutorial, a hands-on lesson, a getting-started walkthrough, a lab exercise, or a "teach someone X by having them build Y" document. To change a tutorial that already exists in content/drafts/, use draft-reviser instead -- never re-run this skill to make a change. Any citation must pass `python -m src.citation_gate` before the draft is presented -- never a fabricated citekey.
tags: [tutorial, diataxis, hands-on, lesson, teaching]
---

# tutorial-writer

Genre-specific drafting agent for tutorial output, in the Diataxis sense: a
**lesson**, in which a learner does something under your guidance and comes
out with skill and confidence they didn't have before. The drafting layer
(generative, on-demand, user-reviewed).

The governing analogy is a driving lesson. The point of a driving lesson is
not to get from A to B; the point is that the student can drive afterwards.
The route is a pretext. Everything in a tutorial is chosen for what it
teaches, not for what it produces -- and the instructor, not the student, is
responsible for the student arriving safely.

That responsibility is the whole discipline of this genre. **A tutorial that
doesn't work is worse than no tutorial**, because a learner who follows your
instructions exactly and gets an error concludes they are the problem. Every
rule below follows from that.

## What this genre is not

| Genre | Reader's state | Skill |
|---|---|---|
| **Tutorial** (this one) | Doesn't know what they don't know; needs a guided first success | `tutorial-writer` |
| **Textbook chapter** | Studying the topic; reading, not typing | `textbook-chapter-writer` |
| **How-to guide** | Already competent; has a specific goal in mind | Not a skill here -- say so, and write it as a short procedure |
| **Reference** | Needs a fact, fast | Not a skill here |
| **Survey / thesis chapter** | Academic reader | `survey-writer` / `thesis-chapter-writer` |

Changing a tutorial that **already exists** in `content/drafts/` is not a
genre question at all: use `draft-reviser`, never another run of this
skill. Re-running it rewrites a lesson that already works, and throws
away the dossier that recorded why each step is the way it is.

The two failure directions, both common:

- **Drifting into explanation.** You get anxious that the learner should
  *know* things, and start explaining the architecture mid-step. The lesson
  stalls. Minimal inline explanation, link or defer the rest.
- **Drifting into a how-to.** You start offering options ("you could also use
  X"), covering edge cases, and handling alternate environments. A learner who
  doesn't yet know the domain cannot evaluate an option; every choice you
  offer is a place to get lost. **One path. No branches.**

If the user actually wants either of those, tell them so and write that
instead. Writing a tutorial when a how-to was wanted wastes everyone's time.

## Prose standards

`docs/WRITING-STANDARDS.md` holds the cross-genre rules -- name the reader,
define terms once, active voice, ban "obviously/simply/just", reread as the
reader. They all apply here.

Where this genre departs from it: §5's "don't let a document do two jobs" is
strictest in this skill, and the structural rules below (single path, no
options, minimal explanation) are *tutorial-only*. Do not carry them into any
other genre -- in a survey they'd delete the deliverable.

## Shared corpus layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- `search(query, k, snippet_chars)`

**Read-only means read-only: never run `python -m src.sync`, and never
run `scripts/enrich.py` or any `src/enrich/*` stage.** Both belong to the
corpus layer, both take the pipeline's write lock, and either can run for
tens of minutes -- a first full-corpus parse, or building the embedding
index. They are the user's to run, not yours. If a semantic index would
help and none exists, say so and use `src.retrieval.search()`; do not
build one.

If `python3 -m src.ledger` reports an empty ledger, say so before you
start. Citations are optional in this genre, so the draft is still
possible -- but it will carry none, and that is the user's call to make,
not something to discover at the end. Ask whether to proceed uncited or to
sync first, and wait for the answer.

**Citations are rare in this genre and belong only in the closing "Where to go
next" section.** A `[@citekey]` inside a step is a distraction from the task at
hand -- the learner is typing, not evaluating literature. If corpus material
shapes the lesson (a real system worth imitating, a dataset worth using), let
it inform your choices silently and point at it at the end.

## The dossier: write down what produced the draft

The lesson is only half of what this run produces. The other half is the
design behind it -- who the learner is, what they were assumed to already
know, the one happy path you chose, **which alternative paths you turned
down and why**, and the environment the steps were actually verified
against. A tutorial is single-path by definition, so the branches you
refused are precisely the judgment the finished prose cannot show.
Without them on disk, the next revision has to re-derive the whole lesson
design in order to change one step.

`src/dossier.py` owns that state, in Markdown, one directory per draft at
`content/dossiers/<the draft's path, minus its suffix>/`. Create it in
step 1, before you write anything, and fill it in as you go -- not at the
end, when the alternatives you rejected have already fallen out of your
context. `docs/DRAFT-ITERATION.md` is the full design.

None of this depends on the draft citing anything. A tutorial with zero
citations still gets a dossier, and an empty `evidence.md` is honest --
the lesson design is the part worth keeping either way.

## Process

1. **Establish the destination artifact, and open the dossier.** Decide the
   one concrete thing the learner will have working at the end -- small, real,
   and visibly functioning. "A working X that does Y when you run it," not "an
   understanding of X." If you can't name it in a sentence, the tutorial isn't
   scoped yet. Ask the user rather than inventing one, if it wasn't given.

   Then, once the artifact and the draft's path are settled and before any
   retrieval or drafting, create the dossier:
   ```
   python3 -m src.dossier init content/drafts/<slug>.md --genre tutorial
   ```
   Fill in `scope.md` now, while you are deciding these things rather than
   reconstructing them later:
   - **Reader** -- the learner in one concrete sentence, including what they
     are assumed to know already. Step 3's prerequisites follow from this.
   - **Covers** -- the destination artifact, and the capability the lesson
     leaves the learner with.
   - **Does not cover** -- the variations, edge cases and alternate
     environments you are deliberately refusing, so a later session can tell
     a scope decision from an oversight.
   - **Glossary** -- each recurring term with the one definition the whole
     lesson uses.

   `init` also stamps the corpus fingerprint, which is what lets a later
   revision tell whether the ledger has moved since.

2. **Do a task analysis.** Walk the entire path yourself first, actually
   running it (see step 8), and write down every command, file and decision
   the path requires -- including the ones you'd normally do without noticing.
   The steps you perform automatically are exactly the ones your draft will
   omit and your learner will fail on.

   Record the outcome in the dossier while you have it: the single happy path
   you settled on, and every alternative you walked away from. Add a
   `## Rejected paths` section to `rejected.md` with its own two-column table
   -- `alternative | why not chosen` -- and leave the citekey table above it
   for retrieved candidates. "Poetry instead of venv: one more install before
   the lesson starts." "Docker instead of a local interpreter: hides the thing
   being taught." This is the most valuable entry a tutorial's dossier holds,
   because the prose can only show the path you kept; a revision without this
   list re-argues every branch you already decided.

3. **Write the front matter the learner needs before starting:**
   - **What you'll build** -- one or two sentences, ideally with the end
     result shown up front (output, screenshot description, sample response).
     Seeing the destination is what makes someone willing to start.
   - **What you'll learn** -- phrased as capability, not curriculum.
   - **What you need** -- exact prerequisites: versions, installed tools,
     accounts, prior tutorials. Be specific ("Python 3.11+, Docker 24+"), not
     vague ("a recent Python").
   - **How long it takes** -- an honest estimate.

4. **Write the steps.** Rules, in priority order:
   - **Every step is an action the learner takes.** If a step has no verb the
     learner performs, it's explanation; move it or cut it.
   - **Start each step with an imperative verb.** One action per step.
   - **Be concrete, never abstract.** Real filenames, real values, real
     commands -- never `<your-project-name>` where a literal `demo-app` would
     do. Placeholders make the learner make a decision, and decisions are
     where they stall.
   - **Show the expected result after every step that produces one.** "You
     should see `Listening on port 8080`." This is the learner's only way to
     know they're still on the path, and the single highest-value thing you
     can add to a draft.
   - **Guarantee results.** Nothing may depend on the learner's environment,
     prior state, or judgement. If something can vary, pin it (a version, a
     seed, a container).
   - **No options, no alternatives, no "depending on your setup".** Choose for
     them.
   - **Minimal explanation inline.** One clause where it prevents confusion
     ("we use HTTPS here because it's safer"), then move on. Park the real
     explanation in step 6.
   - **Repetition is fine.** Don't refactor the lesson for elegance; a
     learner benefits from doing a thing three times.
   - **Warnings go before the step they concern, not after.** A caution the
     learner reads after destroying their state is not a caution.
   - **Never say "simply", "just", "obviously", or "easy".** When it isn't,
     the learner concludes the failure is theirs.

5. **Land the ending.** Close by restating what the learner just built and
   what they can now do -- explicitly, tied back to step 3's promises. A
   tutorial that stops at the last command leaves the learner unsure whether
   they succeeded.

6. **"Where to go next".** This is where deferred explanation, alternatives,
   and further reading live. Link the concepts you passed over quickly, name
   the how-to guides for the variations you refused to cover, and -- if the
   corpus genuinely has something -- cite it here.
   Same retrieval discipline as the other skills if you do search:
   over-fetch (`src.retrieval.search(query, k=15)`), read each 500-character
   snippet yourself rather than trusting the score, and reformulate and
   search again rather than settling for a weak top hit. Citing remains
   optional; a tutorial with zero citations is the normal case, not a
   deficiency. Anything you do cite must be a real citekey from a `search()`
   result -- never a fabricated one.
   If you did search, record both outcomes in the dossier before you draft
   the section: what you keep into `evidence.md`, one ``## `citekey` `` block
   with a `relevance:` line and a `support:` line; what you retrieved and
   turned down into `rejected.md`'s citekey table, with the query that
   surfaced it and a few words on why.

7. **Budget the length.** A tutorial should be completable in one sitting.
   If the path is outgrowing that, split it into a sequence of tutorials with
   explicit prerequisites rather than shipping one the learner abandons
   halfway.

8. **Run it. This step is not optional.**
   Execute every command in the draft, in order, in as clean an environment as
   you can reach (a fresh directory at minimum; a container if the tutorial
   involves installs). Confirm each stated expected result actually appears.
   Fix the draft, then run it again from the top -- a fix in step 4 routinely
   breaks step 7.
   If you genuinely cannot execute part of it in this environment, **say so
   explicitly in chat** when presenting, naming which steps are unverified.
   Never present an unrun tutorial as if it were tested; an untested tutorial
   is the exact artifact this genre exists to avoid.

   Then write what you actually ran against into `scope.md`, under a
   `## Verified environment` heading you add: the exact versions this run
   used ("verified 2026-08-07 on Python 3.11.9, Docker 24.0.7"), not the
   range step 3 advertises. The front matter states what the tutorial
   supports; the dossier states what was executed, which is what a revision
   months later needs in order to tell a rotted step from a mistyped one.
   Name the steps you could not verify there too -- that is the durable half
   of the disclosure you make in chat.

9. **Reread as the beginner.** One pass as someone who has never seen the
   topic. Flag: undefined terms, steps that assume a prior action you never
   instructed, any point where the learner must decide something, any step
   with no way to tell whether it worked.

10. **Map the lesson's outline into the dossier.** Save the draft to
    `content/drafts/<slug>.md` first if you haven't already -- `sections`
    reads the file and reports `No such draft` if it isn't on disk yet. Then
    fill in `sections.md` from:
    ```
    python3 -m src.dossier sections content/drafts/<slug>.md
    ```
    which prints every heading with its line range. The citekey column is
    thin in this genre by design: citations live only in "Where to go next",
    so usually that one section carries every key in the file, and often
    there are none at all. Write "no citations" against a section rather than
    leaving the table blank -- an empty table can't be told apart from a
    template nobody filled in. The outline is the real payload here. It is
    what lets `draft-reviser` repair one step of the lesson, at its recorded
    line range, without reading the whole thing.

11. **Gate any citations.** Save the draft as `content/drafts/<slug>.md`. If
    it contains any `[@citekey]`, run:
    ```
    python -m src.citation_gate content/drafts/<slug>.md
    ```
    Fix and re-run until `OK` before presenting. If there are no citations at
    all, the gate step is unnecessary -- just save the file.
    Note: the gate blanks fenced code, inline code spans and LaTeX verbatim
    environments before extracting citekeys, so `@dataclass`, `@property` and
    similar tokens in your worked code are not false positives. Don't mangle
    real teaching code to appease it.

12. **Build the References section**, only if the draft cites anything:
    ```
    python -m src.references content/drafts/<slug>.md --heading "Further reading"
    ```
    Stdlib-only, bare `python3`, no venv. Entries are numbered IEEE-style;
    leave the inline citations as `[@citekey]` rather than hand-numbering
    them. `--heading "Further reading"` suits this genre better than the
    bare `## References` default; use whatever heading the draft's own
    "Where to go next" section flows into. Skip entirely if there are no
    citations.

    One consequence of a non-default heading: `render_output` only strips
    a section headed `References` before handing the draft to pandoc, so a
    `Further reading` list stays in the rendered `.tex`/`.pdf` *and*
    citeproc appends its own numbered bibliography below it. That is
    usually fine here -- the curated list is the point of the section, and
    the tutorial genre cites lightly. Pass the default heading instead if
    a single bibliography matters more for a given tutorial.

13. **Render tex, pdf, and numbered md.**
    ```
    python3 -m src.render_output content/drafts/<slug>.md --format tex
    python3 -m src.render_output content/drafts/<slug>.md --format pdf
    python3 -m src.render_output content/drafts/<slug>.md --format md
    ```
    The `md` output is a numbered copy in `content/rendered/` -- the same
    IEEE numbers as the PDF, for a reader who won't open one. The draft
    itself keeps its `[@citekey]` markers.

    Bare `python3` plus `pandoc`/`pdflatex` on PATH -- no enrich group. If
    either reports `[missing-binary]` or `[error]`, print a one-line warning
    in chat with that message and continue anyway; a rendering failure never
    blocks presenting the `.md` draft.

14. **Record any steering.** If the user shaped this lesson in chat -- "use
    FastAPI, not Flask", "no Docker", "keep it under twenty minutes", "assume
    they've never opened a terminal" -- append it to the dossier's
    `steering.md`, dated. In this genre it is usually what fixed the single
    path in the first place, it is invisible in the prose, and it has nowhere
    else to live; a revision that doesn't know about it will undo it.

15. **Present**, reporting: the draft path, the render outcome (or warning),
    and -- explicitly -- whether step 8 verification passed in full, in part,
    or not at all. Then say where the dossier is, that changes to this
    tutorial should go through `draft-reviser` rather than another run of
    this skill, and that `content/drafts/` and `content/dossiers/` are
    gitignored -- so `python3 -m src.dossier export <slug>` is how a lesson
    and its working state get backed up.

## Self-check before presenting

Every one of these should be answerable "yes":

- [ ] Can the learner see, at the top, what they will have at the end?
- [ ] Does every step start with a verb they perform?
- [ ] Does every step that produces output state what they should see?
- [ ] Is there exactly one path -- no options, no "if you prefer"?
- [ ] Are all values concrete rather than placeholders?
- [ ] Is every warning placed before its step?
- [ ] Has the whole thing been run end to end, and does it work?
- [ ] Is all substantive explanation in "Where to go next", not in the steps?
- [ ] Would a beginner who follows it exactly succeed, without judgement calls?

## Sources

The principles in this file are not original to this project. Full
citations, licences and a per-principle attribution table are in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
In short: the genre model is Procida's Diátaxis, the audience and
clarity discipline is Google's Technical Writing courses and Last's
*Technical Writing Essentials*. All three are openly licensed and require
attribution.

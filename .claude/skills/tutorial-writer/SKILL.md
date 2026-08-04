---
name: tutorial-writer
description: Drafts a Diataxis-style tutorial -- a hands-on lesson that a learner follows at a keyboard, start to finish, to a working result they can see. Concrete, single-path, minimally explained, and verified to actually run before it is presented. Not a textbook chapter and not a how-to guide; if the reader is studying rather than doing, use `textbook-chapter-writer`, and if they already know what they want and just need the steps, say so rather than writing a tutorial. May cite the synced corpus (content/ledger.sqlite via src.retrieval.search()) but only in a closing "Where to go next" section, never mid-lesson. Triggers when the user asks for a tutorial, a hands-on lesson, a getting-started walkthrough, a lab exercise, or a "teach someone X by having them build Y" document. Any citation must pass `python -m src.citation_gate` before the draft is presented -- never a fabricated citekey.
tags: [tutorial, diataxis, hands-on, lesson, teaching]
---

# tutorial-writer

Genre-specific drafting agent for tutorial output, in the Diataxis sense: a
**lesson**, in which a learner does something under your guidance and comes
out with skill and confidence they didn't have before. Job 2 (generative,
on-demand, user-reviewed) in the two-job pipeline split.

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

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- `search(query, k, snippet_chars)`

**Citations are rare in this genre and belong only in the closing "Where to go
next" section.** A `[@citekey]` inside a step is a distraction from the task at
hand -- the learner is typing, not evaluating literature. If corpus material
shapes the lesson (a real system worth imitating, a dataset worth using), let
it inform your choices silently and point at it at the end.

## Process

1. **Establish the destination artifact.** Decide the one concrete thing the
   learner will have working at the end -- small, real, and visibly
   functioning. "A working X that does Y when you run it," not "an
   understanding of X." If you can't name it in a sentence, the tutorial isn't
   scoped yet. Ask the user rather than inventing one, if it wasn't given.

2. **Do a task analysis.** Walk the entire path yourself first, actually
   running it (see step 8), and write down every command, file and decision
   the path requires -- including the ones you'd normally do without noticing.
   The steps you perform automatically are exactly the ones your draft will
   omit and your learner will fail on.

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

9. **Reread as the beginner.** One pass as someone who has never seen the
   topic. Flag: undefined terms, steps that assume a prior action you never
   instructed, any point where the learner must decide something, any step
   with no way to tell whether it worked.

10. **Gate any citations.** Save the draft as `content/drafts/<slug>.md`. If
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

11. **Build the References section**, only if the draft cites anything:
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

12. **Render tex, pdf, and numbered md.**
    ```
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format tex
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format pdf
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format md
    ```
    The `md` output is a numbered copy in `content/rendered/` -- the same
    IEEE numbers as the PDF, for a reader who won't open one. The draft
    itself keeps its `[@citekey]` markers.

    Bare `python3` plus `pandoc`/`pdflatex` on PATH -- no heavy venv. If
    either reports `[missing-binary]` or `[error]`, print a one-line warning
    in chat with that message and continue anyway; a rendering failure never
    blocks presenting the `.md` draft.

13. **Present**, reporting: the draft path, the render outcome (or warning),
    and -- explicitly -- whether step 8 verification passed in full, in part,
    or not at all.

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

---
name: textbook-chapter-writer
description: Drafts an undergraduate textbook chapter -- learning objectives, motivation, worked examples, exercises -- for a student who is studying the topic, not yet doing it. Diataxis-wise this is explanation with worked application, not a tutorial; if the user wants a hands-on lesson the reader follows at a keyboard, use `tutorial-writer` instead. May cite grounding papers from the synced corpus (content/ledger.sqlite via src.retrieval.search()) for motivation/background, but is not citation-dense; most content is original worked examples and exercises. Triggers when the user asks to draft a textbook chapter, lecture notes, course reader, teaching material, or worked-examples handout for students. Any citations it does include must pass `python -m src.citation_gate` before the draft is presented -- never a fabricated citekey.
tags: [textbook, teaching, undergraduate, pedagogy, explanation]
---

# textbook-chapter-writer

Genre-specific drafting agent for undergraduate textbook-chapter output. Job 2
(generative, on-demand, user-reviewed) in the two-job pipeline split.

Its register is teaching, not persuading a reviewer, which is what separates
it from `survey-writer` and `thesis-chapter-writer`. Its reader is *studying*
-- sitting with the text, following an argument, working problems -- which is
what separates it from `tutorial-writer`, whose reader is at a keyboard
producing a working result. Both are teaching genres; they are not
interchangeable, and the most common failure is writing this genre when the
user asked for the other one. See "When to invoke".

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/parsed/<citekey>.txt` -- extracted PDF text, useful for pulling a
  real worked example or dataset description from a paper if relevant
- `src/retrieval.py` -- `search(query, k, snippet_chars)` if you want to
  ground the motivation section in the corpus (citing the result is still
  optional -- see step 3)

Citations here are optional, not the point. Don't force them in.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a textbook chapter / course reader / lecture notes / worked-examples handout | Invoke this skill |
| User asks for a hands-on lesson the reader follows step by step to a working result | Use `tutorial-writer` instead |
| User asks for a survey or lit review | Use `survey-writer` instead |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |

If the request is genuinely ambiguous ("write something teaching X"), ask one
question: *will the reader be reading this, or doing it?* Reading is this
skill; doing is `tutorial-writer`. Don't guess -- the two genres have opposite
rules about explanation, and a wrong guess produces a document that fails at
both.

## Prose standards

`docs/WRITING-STANDARDS.md` holds the cross-genre rules and all of them apply.
The genre-specific additions are below.

Where this genre departs from `tutorial-writer`: explanation is welcome here
and belongs here. Digression into *why* is a feature of a textbook chapter and
a defect in a tutorial.

## Audience first

Before drafting anything, write down -- in your own working notes, not
necessarily in the chapter -- who the reader is and what they already know.
Everything downstream depends on it: what can go unexplained, which
prerequisites need a recap, how much notation is safe.

Then check yourself against the **curse of knowledge**: you know this material
and the student does not, and the specific danger is the step that feels too
obvious to state. Every term you introduce gets defined once, at first use, and
then used consistently -- never two names for the same concept, never the same
name for two concepts. If you catch yourself writing "obviously", "simply",
"just", or "of course", that sentence is a candidate for expansion, not a
candidate for the chapter.

## Process

1. **Establish the learning objectives** first -- 3-5 concrete "by the end of
   this chapter, students will be able to..." statements, each with an
   observable verb (*derive*, *compare*, *implement*, *predict*), not
   *understand* or *appreciate*, which can't be assessed. Let everything else
   in the chapter serve these, and drop anything that serves none of them.
2. **State scope and prerequisites** near the top: what this chapter covers,
   what it deliberately doesn't, and what the reader is assumed to know
   already. A student who can't tell whether they're equipped for a chapter
   will either bounce off it or waste an hour discovering they were missing
   background.
3. **Motivation: establish the need before the mechanism.** A short section on why this topic
   matters, pitched at an undergraduate who has not read the literature. A
   chapter that presents mechanism without ever answering "why would anyone
   need this" produces students who can follow the steps and can't transfer
   them.
   If you search the synced corpus for a motivating example, use the same
   retrieval discipline as the other skills: over-fetch
   (`src.retrieval.search(query, k=15)`), read each 500-character snippet
   yourself rather than trusting the score, and reformulate and search again
   if the first pass turns up nothing genuinely useful -- don't settle for a
   weak match just because it was the top hit.
   **Whether to cite at all stays optional here, unlike the other skills.**
   Finding a good example doesn't obligate a citation -- cite it
   (`[@citekey]`) only if attributing it to a specific paper actually helps
   the student (e.g. "this is a real system described in [@citekey]");
   otherwise let it inform a well-chosen analogy without a reference. Don't
   manufacture a citation just to have one, and don't feel obligated to
   search at all if you already have a good example. Anything you do cite
   still must be a real citekey from a `search()` result -- never a
   fabricated one.
4. **Diversify sources within a section.** Citing at all stays optional
   (step 3), but once a section ends up citing more than one paper, don't
   let a single citekey carry every paragraph in it just because it was the
   first good hit. When `search()` turns up more than one paper that
   plausibly supports a paragraph, actually compare them and prefer whichever
   adds a distinct angle, rather than defaulting to whichever key you already
   used a paragraph or two ago. Before reusing the same citekey a third time
   within one section, do one more `search()` pass specifically to check
   whether a different paper in the corpus covers the same point -- if it
   does, cite that one instead (or alongside it) so the section's point of
   view doesn't narrow to a single author's framing. It's fine for one source
   to genuinely be the only one that covers a niche point -- don't force in a
   second citation where none fits -- but repeated, unexamined reuse of the
   same key across a whole section is the failure mode to watch for, not
   deliberate reliance on a source that really is the best fit every time.
5. **Worked example(s), then faded ones.** Concrete, step-by-step, with
   enough detail a student could reproduce it, and with the *reasoning*
   visible at each step -- why this move, not just what the move is. A worked
   example that shows only the steps teaches imitation; one that shows the
   choice behind each step teaches the method.
   Prefer originally-constructed examples suited to the target course level
   over lifting a paper's (likely more advanced) treatment.
   Where the chapter has room for more than one, **fade the support**: the
   first example fully worked, the next with one step left to the reader, the
   last posed as a problem. Dropping a student straight from a fully worked
   example to an unaided exercise is the standard cliff, and fading is the
   standard fix.
6. **Exercises.** Include a mix of difficulty, and either solutions or hints
   -- state which. Each exercise should map to a stated learning objective
   (step 1); say which one, at least in your own notes, and cut any exercise
   that maps to none. Exercises should exercise the objectives, not just
   recall the reading.
7. **Close the loop.** End with a short summary of what the chapter
   established, tied back to the objectives it opened with, plus pointers to
   where a student who wants more should go next -- including, where it fits,
   the corpus papers you consulted but didn't need to cite inline.
8. **Read it once as the student.** Before presenting, reread the draft as
   the reader defined in "Audience first" -- not as yourself. Flag anywhere a
   term arrives undefined, a step skips reasoning, or notation changes
   meaning mid-chapter. This pass catches more real problems than any other
   single step here.
9. **Never write a citekey you didn't get from `search()`.** If you do include
   any citations, save the draft as `content/drafts/<slug>.md` and gate it:
   ```
   python -m src.citation_gate content/drafts/<slug>.md
   ```
   Fix and re-run until `OK` before presenting. If there are no citations at
   all, the gate step is unnecessary -- just save to
   `content/drafts/<slug>.md`.
10. **Build the References section.** Once the gate passes, generate it from
    exactly the gated citekeys rather than writing it by hand:
    ```
    python -m src.references content/drafts/<slug>.md
    ```
    Stdlib-only, like the citation gate -- bare `python3`, no venv. Writes
    numbered IEEE-style entries from `content/ledger.sqlite`, ordered by
    first appearance so the numbers match the rendered PDF's, each keeping
    its citekey in a trailing code span so a reader can trace every
    `[@citekey]` marker in the body back to an entry by that same key.
    Leave the body's inline citations as `[@citekey]` -- do **not**
    hand-number them to `[1]`; pandoc assigns the numbers at render time,
    and the literal key is what the gate verifies. If this chapter's
    other section headings are manually numbered (e.g. `## 6. Challenges and
    Open Issues`), pass `--heading "N. References"` with the next number so
    the new section matches the draft's own numbering instead of the bare
    `## References` default. Skip this step entirely if there are no
    citations at all -- same as the gate step.
11. **Render tex and pdf.** Once saved (and gated/referenced, if it has
    citations), also render the other two formats:
    ```
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format tex
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format pdf
    ```
    This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH -- no
    heavy venv required. If either command reports `[missing-binary]` or
    `[error]`, print a one-line warning in chat with that message and
    continue anyway -- a rendering failure never blocks presenting the
    `.md` draft. Report the render outcome (paths to the `.tex`/`.pdf` if
    they succeeded, or the warning if not) alongside the draft.

## House style for this genre

Beyond `docs/WRITING-STANDARDS.md` §4:

- Prefer a concrete instance over an abstract statement of the general case,
  then generalize from it -- students build the general rule from instances,
  not the reverse. This matters more here than in any other genre.
- Notation is introduced once, with a worked instance beside it, and never
  silently reused with a changed meaning in a later section.

## Sources

The principles in this file are not original to this project. Full
citations, licences and a per-principle attribution table are in
[`docs/WRITING-STANDARDS.md`](../../../docs/WRITING-STANDARDS.md#sources-and-attribution).
In short: the genre model is Procida's Diátaxis, the audience and
clarity discipline is Google's Technical Writing courses and Last's
*Technical Writing Essentials*. All three are openly licensed and require
attribution.

---
name: textbook-chapter-writer
description: Drafts an undergraduate textbook chapter -- learning objectives, motivation, worked examples, exercises -- for a student who is studying the topic, not yet doing it. Diataxis-wise this is explanation with worked application, not a tutorial; if the user wants a hands-on lesson the reader follows at a keyboard, use `tutorial-writer` instead. May cite grounding papers from the synced corpus (content/ledger.sqlite via src.retrieval.search()) for motivation/background, but is not citation-dense; most content is original worked examples and exercises. Triggers when the user asks to draft a textbook chapter, lecture notes, course reader, teaching material, or worked-examples handout for students. To change one that already exists in content/drafts/, use draft-reviser instead -- never re-run this skill to make a change. Any citations it does include must pass `python -m src.citation_gate` before the draft is presented -- never a fabricated citekey.
tags: [textbook, teaching, undergraduate, pedagogy, explanation]
---

# textbook-chapter-writer

Genre-specific drafting agent for undergraduate textbook-chapter output. The
drafting layer (generative, on-demand, user-reviewed).

Its register is teaching, not persuading a reviewer, which is what separates
it from `survey-writer` and `thesis-chapter-writer`. Its reader is *studying*
-- sitting with the text, following an argument, working problems -- which is
what separates it from `tutorial-writer`, whose reader is at a keyboard
producing a working result. Both are teaching genres; they are not
interchangeable, and the most common failure is writing this genre when the
user asked for the other one. See "When to invoke".

## Shared corpus layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/parsed/<citekey>.txt` -- extracted PDF text, useful for pulling a
  real worked example or dataset description from a paper if relevant
- `src/retrieval.py` -- `search(query, k, snippet_chars)` if you want to
  ground the motivation section in the corpus (citing the result is still
  optional -- see step 3)

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

Citations here are optional, not the point. Don't force them in.

## The dossier: write down what produced the draft

The chapter is only half of what this run produces. The other half is the
teaching judgment behind it -- who the student is, what they are assumed to
know already, which definition each term was pinned to, which worked example
was chosen and **which candidates were tried and dropped, and why** -- and it
belongs on disk, not in this conversation. Without it the next revision has to
reconstruct the whole pedagogical design in order to change one exercise.

`src/dossier.py` owns that state, in Markdown, one directory per draft at
`content/dossiers/<the draft's path, minus its suffix>/`. Create it before you
draft anything (step 0) and fill it in as you go -- not at the end, when the
example you abandoned has already fallen out of your context.
`docs/DRAFT-ITERATION.md` is the full design.

What the dossier is worth here is not what it is worth in the citation-dense
genres. This chapter is mostly original worked examples and exercises, so
`evidence.md` stays thin and may well be empty -- there is no long evidence
table to build, and padding one out is not the job. The weight sits instead in
`scope.md` (the reader, the prior knowledge assumed, the covers /
does-not-cover line, the glossary that keeps notation stable across a
revision) and in `rejected.md`, which in this genre records **pedagogical**
rejects rather than bibliographic ones: the worked example that turned out too
advanced for the course level, the analogy that broke down one step in, the
exercise cut because it mapped to no objective. That file has no schema and
nothing parses it beyond backticked citekeys, so a row naming an example
rather than a paper is exactly what it is for.

None of this depends on citing anything. **A chapter that carries no citations
at all still gets a dossier** -- including one drafted after the user was asked
about an empty ledger and said to proceed uncited. The reader, the scope, the
glossary and the rejected examples are what a later revision needs, and they
exist whether or not a single `[@citekey]` does.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a textbook chapter / course reader / lecture notes / worked-examples handout | Invoke this skill |
| User asks for a hands-on lesson the reader follows step by step to a working result | Use `tutorial-writer` instead |
| User asks for a survey or lit review | Use `survey-writer` instead |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |
| User asks to change a chapter that **already exists** in `content/drafts/` | Use `draft-reviser` instead -- never re-run this skill to make a change |

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

Before drafting anything, write down -- in the dossier's `scope.md` (step 0),
not necessarily in the chapter itself -- who the reader is and what they
already know. Everything downstream depends on it: what can go unexplained,
which prerequisites need a recap, how much notation is safe.

Then check yourself against the **curse of knowledge**: you know this material
and the student does not, and the specific danger is the step that feels too
obvious to state. Every term you introduce gets defined once, at first use, and
then used consistently -- never two names for the same concept, never the same
name for two concepts. If you catch yourself writing "obviously", "simply",
"just", or "of course", that sentence is a candidate for expansion, not a
candidate for the chapter.

## Process

0. **Name the reader and the scope, and open the dossier.** Before drafting
   and before any retrieval, settle who this chapter is for and what they
   already know ("Audience first" above), and what it will and won't cover.
   Then create the dossier and record those decisions there:
   ```
   python3 -m src.dossier init content/drafts/<slug>.md --genre textbook-chapter
   ```
   Fill in `scope.md`'s **Reader**, **Covers**, **Does not cover** and
   **Glossary** now, while you are deciding them -- the glossary especially,
   since it is what stops a later revision renaming a concept this chapter has
   already defined once, which is the failure a student notices fastest. Where
   a ledger is present, `init` also stamps the corpus fingerprint, which is
   what lets a later revision tell whether the ledger has moved since. Do this
   even if the chapter will carry no citations.
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
   recall the reading. Note each cut in the dossier's `rejected.md` -- one row
   naming the exercise and why it went -- so a later revision doesn't
   reintroduce a problem you already judged and dropped. The same goes for a
   worked example you drafted and abandoned.
7. **Close the loop.** End with a short summary of what the chapter
   established, tied back to the objectives it opened with, plus pointers to
   where a student who wants more should go next -- including, where it fits,
   the corpus papers you consulted but didn't need to cite inline.
8. **Read it once as the student.** Before presenting, reread the draft as
   the reader defined in "Audience first" -- not as yourself. Flag anywhere a
   term arrives undefined, a step skips reasoning, or notation changes
   meaning mid-chapter. This pass catches more real problems than any other
   single step here.
9. **Map the chapter's sections.** Fill in the dossier's `sections.md` -- one
   row per section heading, with any citekeys cited under it -- so a later
   revision can find the section that owns a change without reading the whole
   chapter. Once the draft is saved,
   `python3 -m src.dossier sections content/drafts/<slug>.md` prints the
   headings and their line ranges to build the table from, skipping fenced
   code so a `# Step 1` comment inside an example listing isn't mistaken for
   a heading. A chapter with no citations still gets this map: leave the
   citekeys column empty, since the outline is what a reviser navigates by
   either way.
10. **Never write a citekey you didn't get from `search()`.** If you do include
    any citations, save the draft as `content/drafts/<slug>.md` and gate it:
    ```
    python -m src.citation_gate content/drafts/<slug>.md
    ```
    Fix and re-run until `OK` before presenting. If there are no citations at
    all, the gate step is unnecessary -- just save to
    `content/drafts/<slug>.md`.
11. **Build the References section.** Once the gate passes, generate it from
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
12. **Render tex and pdf.** Once saved (and gated/referenced, if it has
    citations), also render the other three formats:
    ```
    python3 -m src.render_output content/drafts/<slug>.md --format tex
    python3 -m src.render_output content/drafts/<slug>.md --format pdf
    python3 -m src.render_output content/drafts/<slug>.md --format md
    ```
    The `md` output is a numbered copy in `content/rendered/` -- the same
    IEEE numbers as the PDF, for a reader who won't open one. The draft
    itself keeps its `[@citekey]` markers.

    This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH -- no
    enrich group required. If either command reports `[missing-binary]` or
    `[error]`, print a one-line warning in chat with that message and
    continue anyway -- a rendering failure never blocks presenting the
    `.md` draft.
13. **Record any steering.** If the user shaped this chapter in chat --
    "second-years, not first-years", "assume no probability", "more exercises
    and fewer worked examples", "drop the compiler example" -- append it to
    the dossier's `steering.md`, dated. It is invisible in the prose and has
    nowhere else to live; a revision that doesn't know about it will undo it,
    and pedagogical steering is the kind that undoes most quietly, because
    nothing in the finished chapter shows that an easier example was ever on
    the table.
14. **Present the draft** plus a short note on what it assumes as prior
    knowledge, what it deliberately leaves out, and where a student is meant
    to go next -- and report the render outcome (paths to the `.tex`/`.pdf`
    if they succeeded, or the warning if not). Tell the user where the
    dossier is, that changes to this chapter should go through
    `draft-reviser` rather than another run of this skill, and that
    `content/drafts/` and `content/dossiers/` are gitignored -- so
    `python3 -m src.dossier export <slug>` is how a draft and its working
    state get backed up.

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

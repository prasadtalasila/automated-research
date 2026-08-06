# Writing standards for the drafting layer

Shared prose standards for every skill in `.claude/skills/`. Each SKILL.md
points here rather than restating them, and adds only the rules specific to
its own genre.

These are drawn from the technical-communication literature -- primarily the
[Diátaxis](https://diataxis.fr/) framework, Google's
[Technical Writing courses](https://developers.google.com/tech-writing), and
Suzan Last's *Technical Writing Essentials* (BCcampus). What follows is the
part that transfers across all five genres. **Diátaxis's genre-specific rules
do not all transfer** -- "one path, no options, minimal explanation" is
correct for `tutorial-writer` and actively wrong for `survey-writer`, where
weighing alternatives *is* the deliverable. Take the audience discipline and
the sentence-level craft from these sources; take the structural rules only
from your own SKILL.md.

## 1. Decide the audience before drafting

*Source: Google, Technical Writing One ("Just enough writing" / audience
analysis); Last, TWE §7.7 "Do a careful audience and task analysis".*

Write down -- in working notes, not necessarily in the document -- who the
reader is and what they already know. Every downstream decision depends on
it: what can go unexplained, which background needs a recap, how much
notation is safe, how much hedging is appropriate.

A named reader beats a category. "A second-year undergraduate who has taken
one programming course" is usable; "students" is not. "A thesis examiner in
this subfield" is usable; "an academic audience" is not.

## 2. The curse of knowledge is the default failure

*Source: Google, Technical Writing One, which names the curse of knowledge
as the central hazard for engineer-writers. The term itself is Camerer,
Loewenstein and Weber (1989), popularised by Steven Pinker.*

You know the material and the reader does not. The specific danger is the
step that feels too obvious to state -- which is exactly the step you will
omit and the reader will fail on.

Concrete guards:

- Define each term once, at first use, then use it consistently. Never two
  names for one concept, never one name for two concepts.
- Expand an acronym at first use, then use the acronym. Don't reintroduce the
  expansion later, and don't drift back to the long form.
- Treat **"obviously", "simply", "just", "of course", "clearly", "easy"** as
  defect markers. When the thing isn't obvious, the reader concludes the
  failure is theirs. Delete the word; if the sentence then looks like it's
  asserting too much, that's the actual problem showing through.

## 3. State scope up front

*Source: Last, TWE §7.7, whose introduction checklist asks for the scope --
"what will and will not be covered" -- and the reader's assumed background.*

Say what the document covers, what it deliberately does not, and what the
reader is assumed to know already. A reader who can't tell whether they're
equipped for a document will either bounce off it or waste an hour finding
out they were missing background.

## 4. Sentence-level craft

*Source: Google, Technical Writing One (short sentences, active voice,
consistent terms, defining terms once); Last, TWE §2.2 "Communicating with
Precision" and §7.7 "Writing Style" on the passive-voice failure.*

- Short sentences, one idea each. If a sentence must be reread to parse,
  split it.
- Active voice with a named actor: "the scheduler discards the packet", not
  "the packet is discarded".
- Prefer a concrete instance over an abstract statement of the general case,
  then generalize from it. Readers build general rules from instances, not
  the reverse.
- Lead each paragraph with its point. A reader skimming only first sentences
  should still get the argument.
- Cut hedging that carries no information. "It may be argued that X is
  possibly a factor" says less than "X is a factor", and the difference is
  not caution -- it's noise. Real uncertainty gets stated, once, precisely.

## 5. Don't let a document do two jobs

*Source: Procida, Diátaxis -- the four-quadrant model and its claim that
each kind of documentation "needs to be written in a different way".*

This is Diátaxis's actual portable insight. A document that tries to be both
a survey and a tutorial is worse at each than either would be alone, because
the two have opposite obligations: a survey must present alternatives and
weigh them, a tutorial must eliminate every choice.

If, while drafting, you find yourself writing material that belongs to a
different genre, **stop and say so to the user** rather than absorbing it.
The routing tables in each SKILL.md's "When to invoke" section exist for
exactly this moment.

## 6. Read it once as the reader

*Source: Last, TWE §7.7, which names as a requirement of effective
instructions the "willingness to test your instructions on the kind of
person you wrote them for".*

Before presenting anything, reread the draft as the reader defined in §1 --
not as yourself. Flag every point where a term arrives undefined, a step
skips reasoning, notation changes meaning mid-document, or a claim assumes
something never established.

This pass catches more real problems than any other single step. The
technical-communication literature's stronger form of it is a usability test
with a real member of the audience; that isn't available inside a drafting
run, so this reread is the substitute -- and it should be a genuine second
pass, not a skim of what you just wrote.

## 7. Say what you don't know

Every genre here shares one rule from AGENTS.md's citekey invariant: a gap
stated plainly is always better than a gap papered over. Thin corpus
coverage, an unresolved contradiction, a step you couldn't verify -- all of
these get reported to the user in prose. None of them get smoothed.

## Sources and attribution

Three openly licensed works supply the principles above. All three require
attribution under their licences; this section is that attribution.

1. **Daniele Procida, *Diátaxis: A systematic approach to technical
   documentation authoring***. <https://diataxis.fr/> — source repository
   <https://github.com/evildmp/diataxis-documentation-framework>.
   Licensed CC-BY-SA 4.0.
   Supplies: the four-quadrant genre model (tutorial / how-to / reference /
   explanation); the definition of a tutorial as a lesson in which the
   instructor is responsible for the learner's success; the driving-lesson
   analogy; the diagnosis of explanation-overload in tutorials; the
   narrative-of-expectations technique. `tutorial-writer` is built on this
   source more than any other.

2. **Suzan Last, *Technical Writing Essentials***, University of Victoria /
   BCcampus, 2019.
   <https://pressbooks.bccampus.ca/technicalwriting/> — §7.7 "Writing
   Instructions" is the chapter drawn on most. Licensed CC-BY 4.0. That
   chapter is itself adapted from David McMurrey's *Online Technical
   Writing* (<https://mcmassociates.io/textbook/>, CC-BY 4.0), which is
   therefore credited transitively.
   Supplies: audience and task analysis as a preliminary step; scope and
   assumed-background statements in the introduction; equipment and
   supplies lists; the placement and function of note/warning/caution
   notices; command-verb phrasing and the argument against passive voice in
   instructions; testing instructions on a real member of the audience.

3. **Google, *Technical Writing Courses for Engineers***.
   <https://developers.google.com/tech-writing>. Licensed CC-BY 4.0.
   Supplies: the curse of knowledge as the defining failure mode; defining
   each term once and using it consistently; expanding an acronym at first
   use and then not reverting; short sentences with one idea each; active
   voice with a named actor; leading a paragraph with its point.

### What is original here

The prose in this file and in every `.claude/skills/*/SKILL.md` is written
from scratch. A verbatim n-gram check against all three sources above
(107,272 words of source; the algorithm is `scripts/verbatim_check.py`'s
`cmd_overlap`) reports **0% overlap at an 8-word threshold**, and nothing
above five consecutive shared words anywhere. What is borrowed is the
*ideas*, credited above; what is added is their translation into
operational rules for this pipeline's five genres, the decision about which
principles transfer across genres and which do not (§5's warning), and the
handling of failure modes specific to multi-agent drafting.

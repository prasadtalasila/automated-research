# Revisions

<!-- Append-only, newest last. One entry per revision session: what
     changed, which sections, and why. -->

## 2026-08-06 -- initial draft (reconstructed)

The draft itself predates this dossier: it shipped as example content,
and this entry was written afterwards from the finished text rather than
during the run that produced it. See `README.md`. The counts are real --
36 citekeys cited, 19 more in the ledger that the draft does not use --
and the corpus digest `5e9e732e92b7` is this checkout's, not the one the
draft was originally written against.

## 2026-08-06 -- §2 opening leads with its point

**Request:** "§2's first paragraph doesn't carry its point -- the heading
promises familiar shapes and one unfamiliar edge, but the paragraph opens
on the state of the field."

**Changed:** §2 only, lines 46-51 of the previous draft. Reordered the
opening paragraph so the claim comes first and the field's status follows
it.

**Not changed:** no new retrieval. All three citekeys
(`ferko_architecting_2022`, `tekinerdogan_systems_2020`,
`lehner_pattern_2023`) were already in `evidence.md` with the support this
paragraph needs, so nothing was searched, scored, or added. `evidence.md`
and `sections.md` are unaffected -- the same sources carry the same claims
in the same section.

**Cost:** one `Edit` inside one section. The alternative -- re-running
`survey-writer` -- would have re-retrieved four sub-themes, re-scored
roughly 55 candidates and rewritten all 18 KB of the draft to move two
sentences.

**Generalised:** logged in `steering.md` as a whole-draft rule rather than
a one-paragraph fix, since the same fault is likely elsewhere.

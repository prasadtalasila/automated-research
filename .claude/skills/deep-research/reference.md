# deep-research -- Reference (exact protocol, adapted)

Source of truth for the `deep-research` skill's phases. Adapted from
[hadufer/claude-storm](https://github.com/hadufer/claude-storm) (MIT
License), which itself encodes the Stanford STORM algorithm
(`github.com/stanford-oval/storm`, NAACL 2024 / arXiv:2402.14207) fused with
Nav Toor's 4-prompt adaptation. The adaptation for this project: **closed,
citekey-grounded corpus instead of the live web** -- see SKILL.md's "one
deliberate, load-bearing deviation" note before reading further.

---

## 1. Defaults (mirrored from claude-storm/STORM)

| Parameter | Value | Note |
|---|---|---|
| Generated perspectives | 5 + Basic fact writer | adapt names to the topic; drop one if it genuinely doesn't fit an academic corpus |
| Interview rounds / persona | 2 / **3** / 4 (quick/standard/deep) | |
| Search queries / question | up to 3 | reformulations, not parallel identical calls |
| Results kept / query | ~top 3 after relevance filtering | filter like `survey-writer` step 2, not raw top-k |
| Concurrency cap | ~8-10 subagents in flight at once | batch the rest sequentially |
| Lead/summary length | <=4 paragraphs | |

Module order: **Perspective discovery -> Multi-perspective interviews ->
Contradiction map -> Outline -> Cited writing -> Polish/synthesis -> Peer
review**. STORM's own paper reports multi-perspective articles ~25% more
organized and ~10% broader in coverage than single-prompt baselines --
that's the gain the perspective fan-out buys, and why this skill exists
alongside the faster single-pass `survey-writer`.

---

## 2. The personas (Phase 1)

Always include the generalist; default the rest to the five lenses in
SKILL.md, adapted per topic. STORM's own persona discovery works by
surveying related reference material and inventing personas that match
recurring structure in it -- operationalized here as: run 1-2 broad
retrieval calls on the topic itself (not the sub-questions yet) and skim
what actually comes back before finalizing the persona list, for
`standard`/`deep` depth. Skip for `quick`.

---

## 3. Interview protocol (Phase 2) -- what each `deep-research-interviewer` does

Per round (repeat for ROUNDS):

1. **Ask one persona-guided question** -- never repeat a prior question in
   this interview; go deeper each round.
2. **Question -> up to 3 search-query reformulations** against this
   project's corpus (`src.retrieval.search()`, or `src.heavy.embed_index.search()`
   if that stack has been built for this corpus).
3. **Retrieve and filter**: run the queries, read the actual snippets
   (500 chars by default from both `search()` functions), keep only what
   passes a relevance judgment -- same discipline as `survey-writer`'s
   step 2, not "top-k, done."
4. **Answer grounded only in what survived filtering**, every sentence
   cited by real citekey. If nothing relevant survives after reformulating,
   say so -- "no appropriate answer can be formulated from this corpus" is
   a valid, honest output, not a failure to route around.

No web fallback exists in this adaptation. A `source-pdfs/` hit may be
discussed in prose (by title/doc_id) but is never a citation -- see
CLAUDE.md's citekey invariant.

The packet schema each interviewer returns is defined in
`.claude/agents/deep-research-interviewer.md`.

---

## 4. Why no citation-globalization algorithm is needed here

claude-storm's `reference.md` §4 de-duplicates and globally renumbers
citations because raw web URLs from independent parallel searches need it
(different subagents might fetch the same URL, or none at all, with no
shared numbering). This project doesn't have that problem: a citekey is
already a single, stable, project-wide identifier -- the same paper always
has the same citekey from every subagent's `search()` call, straight from
`content/ledger.sqlite`. Just use citekeys directly everywhere (interview
packets, contradiction map, synthesis, peer review, final references) and
skip local-to-global renumbering entirely.

---

## 5. Final report template (Phase 7b)

```markdown
# <Topic> -- Deep Research Report

> Multi-perspective, corpus-grounded research. Method adapted from
> hadufer/claude-storm (MIT) / Stanford OVAL STORM (Shao et al., NAACL 2024).
> Depth: <quick|standard|deep> - Perspectives: <n> - Citekeys: <N> - Date: <date>

## Summary
<=4 cited paragraphs -- the standalone lead.>

## Synthesis Briefing
**Executive summary:** <one paragraph.>

**Key findings (ranked by reliability):**
1. **<finding>** -- Reliability <x>/10. Supported by <perspectives>;
   challenged by <perspectives>. [@citekey]
2. ...

**Hidden connection:** <...>

**Actionable insight (for <role>):** <...>

**Frontier question:** <...>

## <Article body -- outline sections with inline [@citekey] citations>
### ...

## Contradiction Map
- **Conflicts:** <perspective A claims ... [@key] vs perspective B claims ... [@key]>
- **Strongest / weakest evidence:** <...>
- **Resolving question:** <...>
- **Universal agreement:** <...>
- **Blind spot:** <...>

## Peer Review & Reliability Scorecard
<For `standard`/`deep`: the reconciled result of the 4-role panel in §7,
not any single reviewer's raw output.>

| Finding | Confidence (1-10) | Why |
|---|---|---|
| ... | ... | ... |

- **Panel verdicts:** domain-accuracy: <verdict> - methodology-rigor:
  <verdict> - clarity-completeness: <verdict> - devils-advocate: <verdict>
- **Concerns addressed** (met the concession threshold): <what changed and why>
- **Concerns logged, not addressed** (below threshold): <what, and why left as-is>
- **Weakest link:** <claim + what would verify it>
- **Bias check:** <which perspective's sources dominated>
- **Missing perspective:** <6th angle>
- **Overall grade:** <grade> -- <what to fix>

<For `quick`: the single self-critique from SKILL.md Phase 7a instead --
confidence scores, weakest link, bias check, missing perspective, overall
grade, no panel verdicts row.>

## References
<Every citekey actually cited above, as "citekey -- Title (Year)", pulled
from content/ledger.sqlite -- not a URL list.>
```

Save as `content/drafts/deep-research-<kebab-topic>.md` (canonical format),
then render `.tex`/`.pdf` from it — see SKILL.md Phase 7(d) for the exact
commands and the warn-and-continue behavior on a rendering failure.

---

## 6. Honesty rules

- Grounded by default; a claim with no surviving evidence doesn't get made.
- Surface real disagreement among corpus sources rather than smoothing it over.
- The peer-review section must be genuinely critical -- it's the fix for
  STORM's documented lack of self-critique, and it's the same reason this
  project's `citation_gate.py` exists: a soft "please cite your sources"
  instruction is not the same guarantee as an explicit, adversarial check.

---

## 7. Peer-review panel protocol (Phase 7a, `standard`/`deep` depth)

**Idea credited to
[Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills)'s
Stage-3 peer review** (an Editor-in-Chief plus several independent
reviewers and a Devil's Advocate). **Nothing below is that project's text**
-- it's CC-BY-NC 4.0, and what follows is this project's own design,
written from scratch, borrowing only the idea of an independent multi-
reviewer panel with a dedicated adversarial role. See the README's
Acknowledgements section.

**Why a panel instead of one self-critique pass:** one voice reviewing its
own draft shares its own blind spots -- the exact failure mode STORM's
single self-critique step doesn't fix, just narrows. Four independent
reviewers, each with a different, narrow mandate, and none seeing the
others' notes before writing their own, surface more than a single pass
asked to "review this critically."

**The four roles** (dispatched as parallel `peer-reviewer` subagents; full
spec in `.claude/agents/peer-reviewer.md`):

| Role | Mandate |
|---|---|
| `domain-accuracy` | Re-check every cited claim against the actual source text. The one check `citation_gate.py` and academic-research-skills' external-database triangulation both skip: neither verifies the *claim* matches the *source*, only that the citekey/reference exists somewhere. |
| `methodology-rigor` | Does the argument hold together; do conclusions overreach the evidence; is the contradiction-map/synthesis logic internally consistent? |
| `clarity-completeness` | Is it well organized; are thin-coverage gaps or missing citations left unflagged? |
| `devils-advocate` | Argue the strongest case against the draft's central conclusion -- substance, not style. |

**The concession threshold** (this project's own rule -- academic-research-skills'
actual "concession-threshold protocol" internals weren't reused, since this
project doesn't have visibility into them and wrote this independently):

- Any **high**-severity concern from *any* single reviewer must be
  addressed -- revise the claim, or state the concern openly in the
  scorecard as unresolved. Never silently dropped.
- Any concern (medium or high) raised **independently by 2+ reviewers**
  must also be addressed -- agreement across differently-focused reviewers
  who didn't see each other's notes is a stronger signal than any one
  reviewer's opinion.
- **low**-severity or single-reviewer **medium** concerns are logged in the
  scorecard but don't block presenting the draft.

**Reconciliation:** the orchestrating skill (this file's SKILL.md) acts as
the reconciling editor itself -- no separate "EIC" subagent. It reads all
four verdicts (`ready`/`needs revision`/`reject`), applies the threshold
above, revises what the threshold requires, and writes the final
Peer-Review & Reliability Scorecard section from the reconciled result, not
from any single reviewer's raw output.

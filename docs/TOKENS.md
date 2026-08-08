# Tokens

Status: **reasoning document.** Written 2026-08-08.

Where a drafting run's tokens actually go, which of the two pools each
cost lands in, what the dossier does and does not recover -- and how to
put a number on any of it without paying for a full seven-phase run.

This document consolidates the token accounting that was previously
spread across [DRAFT-ITERATION.md](DRAFT-ITERATION.md)'s "Where the
tokens go" section and [REJECTION.md](REJECTION.md)'s cost argument.
Those documents keep their subjects -- the dossier, and why a retrieval
change was withdrawn -- and defer the arithmetic here.

Related reading:

- [DRAFT-ITERATION.md](DRAFT-ITERATION.md) -- the dossier: what it holds,
  why it is Markdown, and how a draft is revised weeks later.
- [REJECTION.md](REJECTION.md) -- why turning a source down is the
  load-bearing judgment, and the record of a two-stage retrieval read
  that was built and then withdrawn on the reasoning below.
- [RETRIEVAL.md](RETRIEVAL.md) -- what a `SearchResult` contains and how
  big a snippet is, which is the input to every estimate here.
- [PERFORMANCE.md](PERFORMANCE.md) -- **measured** costs, all of them
  wall-clock and disk. Nothing in this document belongs there. Every
  token figure below is an estimate or a derivation, and is labelled.

## Table of contents

- [The two pools](#the-two-pools)
- [The resident multiplier](#the-resident-multiplier)
- [Where the tokens go](#where-the-tokens-go)
- [Two worked examples](#two-worked-examples)
- [What the dossier actually recovers](#what-the-dossier-actually-recovers)
- [Why deep-research has one lever left](#why-deep-research-has-one-lever-left)
- [Who writes a packet down, and when](#who-writes-a-packet-down-and-when)
- [Measuring this without writing a survey](#measuring-this-without-writing-a-survey)
- [Measured, derived, and asserted](#measured-derived-and-asserted)

## The two pools

The useful split is not "input versus output". It is **where a token
sits**, because that decides how many times it is billed:

| Pool | Billed | Examples |
|---|---|---|
| **Orchestrator-resident** | once per turn, for every remaining turn of the run | retrieval snippets read inline, returned interview packets, the assembled draft, every tool result |
| **Subagent one-shot** | once | anything read or written inside a dispatched subagent, discarded when it returns |

A subagent's context is destroyed when it hands back its packet. An
orchestrator's is not: it is **append-only between compactions**, and the
whole of it is re-sent on every subsequent turn. So the same 500-character
snippet costs one unit inside a subagent and one unit per remaining turn
in the main run. That difference, not the byte count, is what makes a
drafting run expensive.

### What caching changes, and what it does not

Prompt caching blunts the resident pool without removing it. The
structural ratios -- stable across Claude models, and ratios rather than
prices so they do not go stale -- are:

| | Multiple of base input |
|---|---|
| Uncached input | 1x |
| Cache write (5-minute TTL) | 1.25x |
| Cache read | 0.1x |
| Output | 5x |

Two consequences run through everything below.

**A resident token is roughly a tenth of a fresh one, not free.** Twenty
turns of cached residency come to `20 x 0.1` = 2x the base rate, against
1.25x to put the material in context in the first place -- so a long run
pays more to keep a snippet than it paid to fetch it. But any figure
computed as `bytes x turns` overstates the bill by about six times if it
forgets the 0.1x, which is an easy mistake to make in this direction.

**Output is the expensive direction, at 50x a cached input token.**
Anything the orchestrator *writes* -- a draft, a dossier entry, a
dispatch prompt pasted full of packet material -- is the costliest thing
it does per token. A fix that trades resident input for extra output can
easily lose.

## The resident multiplier

The quantity that matters is not how many tokens a phase produces but

```
resident cost = tokens entering context x turns remaining in the run
```

and the second factor is a property of the **skill**, not of the topic.
`survey-writer` has 15 numbered steps, of which retrieval is step 1;
`deep-research` has seven phases, and Phase 7 alone mandates a peer-review
dispatch, a reconciliation, an assembly, a provenance write, a gate run,
a references build, three renders, two dossier writes and a presentation.
Neither count changes if you ask about digital twins instead of runtime
verification.

This is why the cheapest place to spend attention is the *earliest* one.
A token that enters context in step 1 is multiplied by everything after
it; a token that enters at the presentation step is billed once.

## Where the tokens go

**Every figure in this section is an estimate**, derived from file sizes
in `content/drafts/` and the defaults documented in the genre skills.
Nothing here counts tokens directly: the closest this repository gets is
`retrieval.md`, which records the *character* payload of each retrieval
call for one draft. Read the ratios, not the absolute numbers.

### 1. Retrieved candidates that never leave context

`survey-writer` step 1 calls `search(sub_theme, k=15)` for two to four
sub-themes, over-fetching on purpose. Each `SearchResult` carries a
citekey, a title, a score and a 500-character snippet -- **an estimated
~150 tokens each**, so 30-60 results is an estimated **4.5k-9k tokens per
retrieval pass**. Step 3 then tells you to reformulate and search again
when a sub-theme comes up thin.

The sharp part is what happens next. `reference.md` §1 sets "results kept
per query ~ top 3" out of fifteen. **The roughly 80% that get rejected
cost exactly what the kept ones cost, and then stay resident for the rest
of the run anyway.** Rejecting a candidate saves no tokens at all; it only
saves you from citing it. [REJECTION.md](REJECTION.md) is the full
argument, including why a cheaper first read aimed at this cost was built
and then withdrawn.

### 2. Fan-out results held across phases

`deep-research` Phase 2 dispatches six interviewers and holds their
packets through Phases 3, 4, 5, 6 and 7 -- the contradiction map, the
outline, the section writers, the polish pass and the peer-review
reconciliation all read them. An estimated ~1k tokens per packet is ~6k
tokens resident across the longest stretch of the run. This is the
subject of
[#74](https://github.com/prasadtalasila/chitragupta/issues/74), and
["What the dossier actually recovers"](#what-the-dossier-actually-recovers)
below is careful about which part of it a dossier can and cannot remove.

### 3. Whole-file rewrites

`content/drafts/digital-twins-for-software-engineers/survey.md` is 18.3
KB, an estimated **~4.6k output tokens to write once** -- and output is
the 5x direction. A draft rewritten whole for each revision pays that
every time, including for a gate failure that touches one citekey.

### 4. No revision path at all

This was the big one, and it is what `src/dossier.py` plus the
`draft-reviser` skill exist to remove. Before them, no genre skill had a
branch for "an existing draft plus a change request", so the only way to
alter a paragraph was to run every step again. That is a **structural**
cost -- a whole run you should not have had to make -- rather than a
constant factor on a run you make anyway, which is why it was fixed
first.

## Two worked examples

Both are derived, not measured, and both are written in **input-token
equivalents**: a cached resident token counts 0.1, a cache write 1.25, an
output token 5. Multiplying raw token counts by turn counts, without
those weights, is the specific error these examples exist to avoid.

### Example 1: one rejected paper, followed to the end of the run

A `survey-writer` run on a topic broken into three sub-themes.

| Step | What happens | Tokens |
|---|---|---|
| 1 | `search --k 15` x 3 sub-themes | 45 results x ~150 = ~6.7k |
| 2 | ~3 kept per query, 12 rejected | ~1.4k kept, **~5.4k rejected** |
| 2-14 | thirteen further numbered steps, an estimated 20+ orchestrator turns | nothing evicted |

The 5.4k tokens of rejected candidates are the interesting half. Costed
properly:

- entering context once: `5.4k x 1.25` = **6.8k equivalents**
- resident across ~20 further turns: `5.4k x 0.1 x 20` = **10.8k**
- total: **~17.6k input-token equivalents, for material cited nowhere.**

Three things follow. The rejected candidates cost **4x what the kept ones
do**, because there are four times as many of them and residency does not
care which is which. **Rejecting harder saves nothing** -- the tokens were
spent at retrieval; a rejection only prevents a citation. And the naive
figure, `5.4k x 20 = 108k`, overstates the bill by about six times: the
honest unit is the multiplier, not the raw product.

What *does* help is the subagent boundary. Dispatch one subagent per
sub-theme and the 45 results are read inside three contexts that are then
discarded; only the kept evidence comes back. The same 5.4k of rejects
lands in the one-shot pool at 1.25x once -- about **6.8k equivalents,
against 17.6k** -- and the saving grows with every turn the run still has
to make.

### Example 2: six interview packets, from Phase 3 to Phase 7f

A `standard`-depth `deep-research` run: five personas plus the Basic fact
writer, packets estimated at ~1k tokens each.

| | Tokens |
|---|---|
| Six packets returning into the orchestrator | ~6k |
| Cache write when they arrive | `6k x 1.25` = 7.5k |
| Resident across an estimated 22 turns of Phases 3-7 | `6k x 0.1 x 22` = 13.2k |
| **Total residency** | **~20.7k equivalents** |

Set beside that the two costs the same packets incur *outside* the
resident pool:

- **Transcription into the dossier.** `SKILL.md` already requires the
  kept claims into `evidence.md` and the discarded citekeys into
  `rejected.md`. Say ~4k output tokens: `4k x 5` = **20k equivalents** --
  as much as the entire residency, paid once, and paid for durability
  rather than for speed. It is not a saving and was never billed as one.
- **Phase 5 dispatch prompts.** Each section writer is handed "the
  relevant citekeys plus supporting facts", which the orchestrator emits
  as *output*. Four writers x ~800 tokens of packet-derived material is
  3.2k output = **16k equivalents**.

That last row is the one #74 can actually collect, and it is why the
answer is a file rather than better summarising: replace the pasted
material with `read content/dossiers/<draft>/evidence.md, the rows for
section 3` -- an estimated 40 output tokens per writer, ~0.8k
equivalents. **An estimated 15k equivalents saved, in the 5x direction**,
which is the same order as the entire resident cost the issue set out to
attack, arrived at from the opposite side.

## What the dossier actually recovers

The issue's diagnosis is right about where the cost is and needs one
correction about the mechanism, which is worth stating plainly because it
changes what a fix should optimise.

**Residency cannot be undone from inside a run.** The orchestrator's
context is append-only between compactions. Once six packets have been
returned into it, writing them to disk does not remove them -- reading an
extract back *adds* tokens. There is no eviction primitive, so "hold the
extract instead of the packet" is not something a skill can do to a turn
that has already happened.

So of the resident 20.7k in Example 2, a dossier recovers **none** within
that run. What it does recover:

| Effect | Pool | Why it is real |
|---|---|---|
| Phase 5 dispatch prompts shrink to a file reference | output, 5x | The orchestrator stops re-emitting packet material once per writer |
| Subagents read only the rows they need | subagent one-shot | Four writers each receive a path instead of a paste |
| Compaction stops being lossy | resident | A compacted run can recover exact packet detail from disk instead of re-dispatching six interviewers -- the single largest cost in the skill |
| The next run skips Phase 2 entirely | structural | `draft-reviser` reads `evidence.md` and `rejected.md`; no interviews at all |

The third row is the underrated one. Today a long run that hits
compaction either loses packet detail silently or pays six interviewer
dispatches to get it back. With the packets on disk, compaction becomes a
cheap operation instead of a lossy one -- which is a *resident*-pool
effect, just an indirect one.

### The one way to cut residency, and what it would cost

Residency can only be avoided by **not putting the material in the
orchestrator at all**. That collides with a rule the skill states
deliberately: the main run owns the dossier, and a subagent never writes
it (`.claude/skills/deep-research/SKILL.md`, "The dossier"). The three
subagent definitions enforce it structurally -- `tools: Bash, Read, Grep,
Glob`, with no `Write` or `Edit` -- and each is told in prose that it
writes no files.

**A proposal, not a plan:** relax that rule for exactly one shape --
**one file per subagent, written once, never read by a sibling**. Each
interviewer writes `content/dossiers/<draft>/interviews/<persona>.md` and
returns a short packet: claims, citekeys, one-line reasons. The long-form
material never enters the orchestrator, so the residency is never
incurred; the orchestrator reads back only what Phase 3 needs to build
the contradiction map.

What it buys is the only remaining reduction of the resident pool. What
it costs is the invariant that makes the dossier trustworthy -- one
writer, one record, verifiable by reading one skill file -- and it is
exactly the invariant that keeps
[the synchronisation questions below](#who-writes-a-packet-down-and-when)
answerable. It is written down here so the trade is visible, not because
it is recommended.

## Why deep-research has one lever left

The claim in [#74](https://github.com/prasadtalasila/chitragupta/issues/74)
-- that this is now the only remaining way to cut `deep-research`'s token
cost -- is reached by elimination, and the eliminations are each recorded
elsewhere:

| Lever | Status for this skill |
|---|---|
| Remove the structural cost (no revision path) | Already done -- `src/dossier.py` plus `draft-reviser` |
| Trim what retrieval returns (two-stage triage) | Withdrawn. See [REJECTION.md](REJECTION.md): `deep-research`'s reads already happen inside subagents, so triage optimises the *cheap* pool, adds an estimated 270 further process starts at standard depth, and discards exactly the qualifying passages contradiction mapping exists to find |
| Move reads behind the subagent boundary | Already done -- Phases 2, 5 and 7 all dispatch |
| Cut the fan-out payload the orchestrator carries and re-emits | **Open** -- #74 |

The elimination is a real conclusion rather than an accident of what is
left: this is the one substantial payload the skill puts in the expensive
pool and then re-emits by hand.

Note the dependency listed on the issue is stale. It records itself as
blocked by
[#81](https://github.com/prasadtalasila/chitragupta/issues/81), which is
closed -- the dossier wiring landed in `c4fbd9a`, and
`.claude/skills/deep-research/SKILL.md` has required the Phase 2
transcription since. The write half exists; what remains is the
dispatch-prompt half.

## Who writes a packet down, and when

Two questions come up whenever this design is explained, and both have
answers that are properties of the current code rather than intentions.

**Do the later phases write the packets to disk?** No. Every write to
`content/dossiers/` is done by the orchestrating run, in the phase that
dispatched the subagent, before that phase closes. The subagents cannot
write: `deep-research-interviewer`, `deep-research-writer` and
`peer-reviewer` each declare `tools: Bash, Read, Grep, Glob` in their
frontmatter, and each is told in prose that it writes no files and that
anything not in its returned packet is lost when it exits. `Bash` is a
theoretical escape hatch; nothing instructs them through it.

The failure mode that remains is therefore **loss, not corruption**: an
orchestrator that moves to Phase 3 without transcribing has lost six
packets' worth of rejected citekeys, and nothing reports it. That is
silent by construction, which is why the skill states the transcription
as a rule of the skill rather than as a suggestion.

**Is there a synchronisation risk?** Not on the current paths, and the
reason is worth knowing because it is narrower than "the module is safe".

- **One writer.** The orchestrator is single-threaded with respect to its
  own tool calls, and it is the only dossier writer. Concurrent
  modification of `evidence.md` or `rejected.md` cannot arise.
- **`init` cannot clobber.** `src.dossier.init` only creates files that
  are missing, so re-running it against a part-filled dossier adds what
  is absent and touches nothing else.
- **No locks, deliberately.** `src/dossier.py` takes no lock and is not a
  gate. It must not block behind a `sync` that is mid-run, and a
  bookkeeping write is never allowed to fail the work it was recording.

There is one path that *could* produce concurrent writers, and it is
worth naming before someone builds it. `python3 -m src.retrieval ...
--log <draft>` appends to the dossier's `retrieval.md`, and subagents can
run Bash. Today only `survey-writer` and `draft-reviser` pass `--log`,
and both are single orchestrators. Give `--log` to six parallel
interviewers and two things become live:

- `log_retrieval` writes the file's template when the file is absent and
  then appends. Two processes that both find it missing can both write
  the template, and the second `write_text` truncates -- so a row the
  first had already appended is lost. The window is small and real.
- The append itself is a single small write to a file opened `"a"`, which
  is atomic on a local POSIX filesystem at these row sizes and is not
  guaranteed to be on NFS.

Neither is a reason to change `src/dossier.py` now. Both are reasons that
"the orchestrator owns every write" is load-bearing rather than
stylistic, and they are what the per-persona-file proposal above would
have to answer -- it sidesteps them by construction, since one file with
one writer never races, but it does so by giving up the single-writer
rule everywhere else.

## Measuring this without writing a survey

Every figure above is derived. Turning them into numbers is
[#76](https://github.com/prasadtalasila/chitragupta/issues/76), and the
obvious way to do it -- run a full `standard`-depth `deep-research` on a
real topic, before and after -- is also the most expensive experiment
available and the least controlled, since two runs on the same topic do
not take the same number of turns. Four cheaper routes, in increasing
order of what they cost you.

### Free: the session transcript already has the answer

Claude Code writes a JSONL transcript per session under
`~/.claude/projects/<slugified-cwd>/<session-id>.jsonl`, and every
assistant entry carries a `usage` object with `input_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens` and
`output_tokens`. Subagent turns appear in the same file flagged
`isSidechain: true` -- which means **the two pools can be separated
empirically, from a run you have already paid for**:

```python
import json, sys

seen = set()
pools = {"orchestrator": [0, 0, 0], "subagent": [0, 0, 0]}   # turns, input, output
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        entry = json.loads(line)
    except ValueError:
        continue
    usage = (entry.get("message") or {}).get("usage")
    rid = entry.get("requestId")
    if not usage or rid in seen:      # streaming writes an entry more than once
        continue
    seen.add(rid)
    pool = pools["subagent" if entry.get("isSidechain") else "orchestrator"]
    pool[0] += 1
    pool[1] += (usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0))
    pool[2] += usage.get("output_tokens", 0)

for name, (turns, inp, outp) in pools.items():
    print(f"{name:13} turns {turns:4}  input {inp:12,}  output {outp:9,}"
          f"  mean input/turn {inp // max(turns, 1):,}")
```

This is a recipe, not shipped tooling -- it reads a harness file this
project does not own, and the schema is the harness's to change.

Run against the session that wrote this document -- a documentation
session, no drafting, no subagents -- it reports 35 orchestrator turns,
**1,991,974 input tokens against 14,318 output tokens**. That ratio, 139
input tokens per output token, is the resident multiplier measured rather
than argued, on a session doing nothing more expensive than reading files
and writing prose.

De-duplicating on `requestId` matters: streaming writes the same usage
record more than once, and summing naively inflated the same session to
56 turns and 3.2M tokens.

### Cheap: a stub corpus

The turn structure of a genre skill is independent of how big the corpus
is. A synthetic bibliography of five to ten short PDFs syncs in seconds
and exercises every phase: six interviewers still get dispatched, Phase 3
still builds a map, Phase 5 still writes sections, the gate still runs.
What changes is the *content* of each packet, not the count of them or
the number of turns they are resident for.

That makes a stub corpus the right vehicle for the A/B that matters --
the same topic, the same depth, once with packets pasted into dispatch
prompts and once with a file reference -- because it is the one
comparison where the difference is the change rather than the topic.

Its limit is the honest one: a stub corpus tells you what the *structure*
costs, not what a real run costs. Packet sizes on five toy papers are not
packet sizes on 501.

### Free, and needs no run at all: count the turns

The second factor in `bytes x turns` can be read off the skill file. Take
`.claude/skills/deep-research/SKILL.md`, count the mandated steps after
Phase 2 -- each named command, each dispatch, each dossier write, each
render -- and you have a floor on the multiplier that no topic can change.
Do the same for `survey-writer` after step 1. This is how the "~20 turns"
and "~22 turns" in the examples above were obtained, and it is the part
of the estimate least likely to be wrong, because it is a property of a
file in this repository rather than of a model's behaviour.

The first factor, packet size, can be bounded the same way: take one real
packet from any previous run's transcript, count its characters, divide
by four. No new run required.

### Already instrumented: `retrieval.md`

`python3 -m src.retrieval ... --log <draft>` appends one row per call --
mode, query, `k`, results, characters -- to the dossier's `retrieval.md`,
and `python3 -m src.dossier status` totals it. That is characters rather
than tokens and covers retrieval only, but it is the one number this
repository already collects on a real corpus, it is comparable between
runs, and it costs nothing beyond passing a flag.

The gap it leaves is exactly the one this document is about: it measures
what entered context, and not how many turns it stayed there for.

## Measured, derived, and asserted

Kept separate on purpose, in a project where
[PERFORMANCE.md](PERFORMANCE.md) means measured.

**Measured** -- one figure. The 35 turns / 1,991,974 input / 14,318
output above, from this session's own transcript, on the machine this was
written on. It demonstrates the ratio; it is not a benchmark of a
drafting run.

**Derived** -- the turn counts (read off the skill files), the pricing
multipliers (structural ratios of the Claude API, not prices), and every
worked example built from them.

**Estimated** -- every token count of a payload: ~150 per
`SearchResult`, ~1k per interview packet, ~4.6k to write an 18.3 KB
draft. All from file sizes and documented defaults, at four characters
per token.

**Asserted** -- that the orchestrator's context is append-only between
compactions, and that a subagent's is discarded on return. These are
properties of the harness rather than of this repository, and everything
in ["What the dossier actually recovers"](#what-the-dossier-actually-recovers)
depends on them. If a future harness evicts old tool results, the
residency argument weakens and the dispatch-prompt argument does not.

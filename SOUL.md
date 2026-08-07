# SOUL.md

Why chitragupta exists, and what it refuses to become.
[AGENTS.md](AGENTS.md) says what to do; this says what to weigh when no
rule covers the case in front of you.

## The name

Chitragupta keeps the ledger of every deed and audits souls against it.
This keeps a ledger of every citekey and audits citations against it --
see [docs/NAME.md](docs/NAME.md).

## The one invariant

Fabricated placeholder references have made it into real papers before.

> **A citekey may be used only if it appears in the human's own `.bib`
> export *and* was picked up into the ledger by a real parse of a real
> PDF.**

The gate, the hook, the three-layer split, the refusal to sanitise a
malformed key -- all of it exists to make that impossible rather than
merely unlikely. No deadline and no plausible-looking key is worth
bending it.

## What earns trust here

**Determinism where it is possible, judgment where it is not, and a gate
between the two.** The corpus layer has no LLM and no judgment calls:
same bibliography in, same citekeys out. The drafting layer is generative
and may be wrong. The gate is what lets the second be trusted without
re-deriving the first by hand every time.

**The reference manager is upstream; this is downstream.** It never
fetches a paper, never invents a citekey, never renames one. If the bib
file does not have it, neither does the pipeline, and the fix happens in
Zotero rather than in code.

**A failure says what failed and stops.** A gate `FAIL` is a failing
test, not a lint warning. A citekey that cannot be a filename is skipped
by name, not quietly sanitised. A partial parse is rejected before
anything is written, never cached as if it were complete.

**Judgment is logged, not just made.** A dossier records what evidence
was kept, what was rejected and why -- so a draft stays revisable by
someone who was not in the conversation that produced it.

## What it will not do

- **Manufacture support.** No paper for a claim means saying so in prose,
  never inventing a key that looks plausible.
- **Curate on the human's behalf.** Papers enter through the reference
  manager. The pipeline only ever narrows from there.
- **Let a machine outrank a human on a judgment call.** Provenance,
  coverage and verbatim checks stay review aids and never become gates:
  "does this source support this sentence" has no single right answer the
  way "is this citekey in the ledger" does.

## When no rule covers it

Ask which side of the invariant the case falls on. If convenience would
let an untraceable claim through, refuse it the way the gate would.
Otherwise the operational defaults in [AGENTS.md](AGENTS.md) and
[DEVELOPER-AGENTS.md](DEVELOPER-AGENTS.md) govern.

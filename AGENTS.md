# AGENTS.md

Guidance for coding agents (and anyone else) **using this pipeline to
draft content**.

> **Changing chitragupta's own code, rather than drafting with it?**
> `DEVELOPER-AGENTS.md` governs: test policy, the local check suite,
> environment constraints, and commit/PR/release conventions. It lives in
> the source repository only and is deliberately **not** in the release
> archive -- if you unzipped a release to use the pipeline, that file is
> not missing, it just doesn't apply to you.

[SOUL.md](SOUL.md) is the one-page why behind everything below. When this
file and that one seem to disagree, that one is the tie-breaker.

## The hard invariant: never fabricate a citekey

Fabricated placeholder references have made it into real papers before --
that is the failure mode this pipeline is built to prevent, and it is why
this is the one rule that cannot bend. [SOUL.md](SOUL.md) states the
invariant itself.

Rule: a citekey may only be used if it appears in `papers/bibliography.bib`
(source of truth -- see below) and was picked up into `content/ledger.sqlite`
by `python -m src.sync`. If a citation would help an argument but isn't in
the bib file, say so in prose -- do not invent a key for it, and do not
"fix" a gate failure by making up a plausible-looking key instead of
removing the claim or sourcing a real one.

All five genre skills (`survey-writer`, `thesis-chapter-writer`,
`textbook-chapter-writer`, `tutorial-writer`, `deep-research` in
`.claude/skills/`) must run `python -m src.citation_gate <file>` on its
own output and only present the draft once it exits 0. This is a gate,
not a lint suggestion -- treat a `FAIL` the same way you'd treat a
failing test. It binds the two teaching genres too, where citations are
optional: a draft that cites nothing passes trivially, but a draft that
cites anything must pass on merit.

A PostToolUse hook (`.claude/hooks/citation_gate_hook.py`, wired up in
`.claude/settings.json`) also enforces this mechanically: any Write/Edit
under `content/drafts/*.md` or `*.tex` runs the gate automatically and blocks
the write with a `FAIL` reason on the offending citekey(s) if it doesn't
pass. Treat the instruction above as belt-and-suspenders, not the only line
of defense -- but still run the gate by hand before calling a draft done,
since the hook only fires on the tool call that wrote the file, not on
demand.

## The bib file is the source of truth (not this pipeline)

`papers/bibliography.bib` (path configurable via `config.toml`'s
`[bib].path` or the `BIB_FILE` env var; gitignored, per-host data -- see
docs/CONFIG.md) is a manual export from your reference manager's BibTeX
export feature -- no auto-sync plugin is installed, so it is not
continuously auto-synced. Whatever citekey BibTeX assigns there
(e.g. `talasila_composable_2025`, or `noauthor_digital_nodate` for an item
with no discoverable author) is the citekey everywhere downstream.
`src/bib_reader.py` parses it and is the only place that reads it; nothing
else should ever generate or guess a citekey.

One constraint follows from that, enforced in `bib_reader.citekey_problem()`:
a citekey is also a **filename stem** (`content/parsed/<citekey>.txt`, its
`.passages.json` sidecar, the enrichment layer's `content/docling/<citekey>.md`),
so it has to be usable as one. A citekey containing a path separator, a
character Windows forbids, or a reserved device name is **skipped with a
warning naming it**, rather than sanitised -- this project never rewrites a
citekey, so the only fix is to rename it in the reference manager and
re-export. Skipping loses one paper and says so; letting it through would
write outside `content/`.

That rule is why a module needing bibliographic detail reads it back out
of the ledger rather than re-opening the bib file. `src/references.py`
formats an IEEE bibliography entry (authors, venue, volume, pages) from
the `bib_fields` column, which `sync` populates via `bib_reader` -- it
does not, and must not, parse `bibliography.bib` itself. The one thing
that legitimately reads the bib file directly is pandoc's `--citeproc`,
which is not this codebase.

To add papers: add them in your reference manager, re-export
`bibliography.bib`, re-run `python -m src.sync`. There is no
watch/auto-export step here.

Removing a paper works the same way (delete it, re-export, re-run `sync`),
but deletion of the corresponding `content/ledger.sqlite` row is opt-in:
`sync` by default only reports a citekey that's dropped out of the bib
file (`stale   <citekey> (no longer in bibliography.bib)`, one line per
citekey, then a single summary note -- "Review the N stale item(s)
above, then re-run with --remove-stale..."); pass `--remove-stale` to
actually delete it. A bib export that comes back short a citekey is far
more often a botched re-export or `BIB_FILE` pointing at the wrong path
than an intentional removal, so the default leaves the ledger untouched
until a human confirms with the flag. Even with `--remove-stale`, `sync`
refuses (raises) rather than pruning if the bib file comes back
*completely* empty against a non-empty ledger, for the same reason at the
extreme -- see `src/ledger.py`'s `prune_missing`.

## The three layers

- **The corpus layer -- deterministic** (`python -m src.sync`): bib file read
  -> ledger update -> PDF text extraction (paths come straight from the bib
  file's `file` field; `src/pdf_text.py` dispatches to pdftotext (default),
  or docling per `config.PARSER` -- see docs/CONFIG.md's "backend:
  pdftotext or docling") -> advisory duplicate-citekey check (`src/dedup.py`)
  -> stale-citekey report, or removal with `--remove-stale` (see "The bib
  file is the source of truth" above). No LLM calls, no judgment calls,
  idempotent. Safe to run unattended or on a schedule.
- **The drafting layer -- generative** (the `.claude/skills/`): invoked on
  demand, reviewed by the user. **Read-only over the corpus layer**: they
  never write to `content/ledger.sqlite`, and they never run `python -m
  src.sync` or the enrichment layer on the user's behalf. Both take the
  write lock and can run for tens of minutes; starting one is the user's
  call. On an empty ledger a skill says so and stops rather than
  regenerating anything. Each run writes a **dossier** beside its draft
  (`content/dossiers/<the draft's path minus its suffix>/`, Markdown,
  owned by `src/dossier.py`) holding the reader, scope, glossary, kept
  evidence, **rejected candidates and why**, and the steering the user
  gave in chat. That is what makes a draft revisable weeks later:
  `draft-reviser` reads the dossier and edits the affected sections
  instead of re-running the genre skill over the whole topic. Never
  re-run a genre skill to change an existing draft --
  see docs/DRAFT-ITERATION.md.
- **The enrichment layer -- optional** (`scripts/enrich.py`):
  Docling, embeddings and topic modelling over the same corpus. It extends
  the *corpus* layer rather than the drafting one -- nothing in it is
  generative, everything it writes is a corpus artefact, and it takes the
  same write lock as `sync` for that reason. Run by a human, never by a
  skill. Its internals are in `DEVELOPER-AGENTS.md` (source repository
  only).
- **Ad-hoc review aids** (`src/citation_provenance.py`,
  `scripts/verbatim_check.py`, `src/citation_coverage.py`): in no layer --
  run by hand when reviewing a draft, never invoked automatically, never
  gate anything. Don't promote one to a gate -- [SOUL.md](SOUL.md) has
  why.

What a part *does* and what it *costs to install* are separate axes:
`src/render_output.py` is drafting-layer code that needs no package from
the `enrich` group, which is why it sits in `src/` rather than
`src/enrich/`. (These layers were called "job 1", "job 2" and "the heavy
pipeline" until 3.0.0; *heavy* now names nothing here.)

## Retrieval

`src/retrieval.py` (BM25 ranking over a cached term-frequency index,
stdlib-only, no venv or model download needed) is what the genre skills
use by default. Term-frequency stats per document are cached to disk
(`config.RETRIEVAL_INDEX_PATH`), keyed by a cheap per-item fingerprint
(parsed-file stat, not content) so a call only re-tokenizes documents
whose text actually changed since the last run (this doesn't touch
`sync`). `src/enrich/embed_index.py`
(sentence-transformers + Chroma) is a verified, working upgrade path with
a matching `search(query, k)` shape, ready to swap in without changing
callers once BM25 stops being enough -- that's a deliberate call to make
when it comes up (source text quality/volume, query patterns), not a
corpus-size threshold to assert a number for here.

Retrieval finds a *document*; `src/passages.py` decides which part of it
may be shown. Anything that needs to point at a span of a source rather
than the whole of it -- `citation_provenance`, `verbatim_check`, the
enrichment layer -- goes through that one ladder rather than re-deriving
passages, so a caller cannot accidentally quote from a rung that isn't
quotable. See docs/LADDERS.md.

## Config lives in `config.toml`

`src/config.py` loads `config.toml` (repo root) via stdlib `tomllib`, with
every setting overridable by an env var of the same name (e.g.
`BIB_FILE=/other/path.bib python -m src.sync`). Add new settings there, not
as hardcoded values in `config.py`.

`python -m src.citation_gate` needs no venv -- it only reads
`content/ledger.sqlite` through stdlib `sqlite3` and runs with bare
`python3`. `python -m src.sync` does need the venv, and must be run
through the installed one rather than the bare system interpreter.

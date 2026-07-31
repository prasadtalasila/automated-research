---
name: survey-writer
description: Drafts a topic-clustered literature survey / background section / "state of the art" from the synced corpus, with a comparison table and a gap analysis. Every claim is grounded in a citekey pulled from content/ledger.sqlite via src.retrieval.search() -- never a fabricated one. Triggers when the user asks to write, draft, or update a survey paper, literature review, background section, or related-work section for a given topic. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses (and tells the user to run `python -m src.sync` first) if the ledger is empty.
tags: [survey, literature-review, citation]
---

# survey-writer

Genre-specific drafting agent for survey-style output. This is the "generative
drafting" job (job 2) in the two-job pipeline split -- it runs on demand and its
output is reviewed by the user, unlike `python -m src.sync` (job 1, deterministic,
safe to run unattended).

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `papers/bibliography.bib` (gitignored, per-host) -- the source of truth for citekeys/metadata;
  `sync` reads it, it is never regenerated
- `content/parsed/<citekey>.txt` -- extracted PDF text
- `src/retrieval.py` -- `search(query, k)` returns `SearchResult(citekey, title, score, snippet)`

If `content/ledger.sqlite` doesn't exist or `python -m src.citation_gate` reports
an empty ledger, run `python -m src.sync` first and tell the user what it found
before drafting anything.

## When to invoke

| Situation | Action |
|---|---|
| User asks for a survey / lit review / background / related-work section on topic X | Invoke this skill |
| User asks for a thesis chapter | Use `thesis-chapter-writer` instead |
| User asks for teaching material / tutorial | Use `tutorial-writer` instead |
| Ledger is empty or stale | Run `python -m src.sync`, report results, then proceed |

## Process

1. **Retrieve broadly, over-fetching on purpose.** Break the requested topic
   into 2-4 sub-themes if it's broad. Call `src.retrieval.search(sub_theme, k=15)`
   for each -- pull more candidates than you expect to use. This is a
   keyword-overlap ranker, not embeddings (unless `src/heavy/embed_index.py`
   has been built for this corpus) -- a high score or short distance is a
   proxy for relevance, not a judgment of it. Don't let a top rank substitute
   for reading the snippet.
2. **Score every candidate yourself before it counts as evidence.** For each
   result, read the full snippet (both `search()` functions default to 500
   characters specifically so you have enough to judge, not just a title) and
   decide: does this chunk actually support a claim about the sub-theme, or
   did it just share vocabulary with the query? Keep only the ones that pass.
   This is the same discipline PaperQA2 calls "gather evidence" (retrieve,
   then LLM-judge relevance, *then* write) -- the difference here is you're
   doing the judging inline as part of drafting, not via a second API call.
   Write what survives as scored evidence -- `{"citekey": ..., "relevance":
   "why this supports the claim", "quote_or_paraphrase": "..."}` -- into
   `content/provenance/<slug>-evidence.json` before you start drafting
   prose. Treat a citekey that didn't pass this filter as unused, even if it
   was a high-scoring `search()` hit.
3. **Reformulate and re-search if a sub-theme comes up thin.** A single
   query wording is not the ceiling -- if scoring leaves you with little or
   nothing for a sub-theme, try synonyms, broader/narrower terms, or an
   adjacent concept, and search again. Do this a few times before concluding
   "thin coverage" is real rather than a wording problem. Only after genuine
   reformulation attempts should you report a sub-theme as thin -- and then
   say so explicitly rather than padding it with uncited claims.
4. **Cluster by judgment.** With a small corpus there's no BERTopic step;
   group the surviving (scored, kept) citekeys into themes yourself based on
   what the evidence actually says.
5. **Check for disagreement across sources before writing.** If two kept
   pieces of evidence conflict on a claim, don't silently pick one side --
   note the disagreement explicitly in the draft (which source says what).
   Silently resolving a real contradiction is a worse failure than leaving
   it visible.
6. **Draft**, in Markdown, using Pandoc-style citations (`[@citekey]`,
   `[@key1; @key2]`), citing only from your scored-evidence file:
   - Framing paragraph for the overall topic
   - One subsection per theme, citing the papers that actually support each claim
   - A comparison table: columns for approach/paper, citekey, core idea,
     stated limitations
   - A gap-analysis paragraph: what the retrieved corpus does *not* cover
     (including sub-themes that stayed thin after reformulation, and any
     cross-source disagreement from step 5)
7. **Never write a citekey you didn't get from a `search()` result.** If you
   want to cite something you know about from general knowledge but that isn't
   in the ledger, say so in prose to the user instead ("X is commonly discussed
   in this area but isn't in your synced library yet") -- do not invent a key
   for it.
8. **Log provenance.** Write `content/provenance/<slug>.json`: a list of
   `{"section": "...", "citekeys": [...]}` so citation choices are auditable
   later without re-reading the whole draft (in addition to the evidence file
   from step 2).
9. **Gate before presenting.** Save the draft as `content/drafts/<slug>.md`
   (this is the canonical, source-of-truth format), then run:
   ```
   python -m src.citation_gate content/drafts/<slug>.md
   ```
   If it reports `FAIL`, fix the offending line(s) — either correct the citekey
   or remove the claim — and re-run until it reports `OK`. Never show the user
   a draft that hasn't passed.
10. **Build the References section.** Once the gate passes, generate it from
    exactly the gated citekeys rather than writing it by hand:
    ```
    python -m src.references content/drafts/<slug>.md
    ```
    Stdlib-only, like the citation gate — bare `python3`, no venv. Lists
    each citekey next to its title/year pulled straight from
    `content/ledger.sqlite`, so a reader can trace every `[@citekey]`
    marker in the body back to a labeled entry by that same key.
11. **Render tex and pdf.** Once the gate passes and the references section
    is built, also render the other two formats:
    ```
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format tex
    python3 -m src.heavy.render_output content/drafts/<slug>.md --format pdf
    ```
    This needs only bare `python3` plus `pandoc`/`pdflatex` on PATH — no
    heavy venv required. If either command reports `[missing-binary]` or
    `[error]`, print a one-line warning in chat with that message and
    continue anyway — a rendering failure never blocks presenting the
    `.md` draft.
12. Present the draft plus a one-paragraph summary of thin-coverage areas and
    any unresolved cross-source disagreement, and report the render outcome
    (paths to the `.tex`/`.pdf` if they succeeded, or the warning if not).

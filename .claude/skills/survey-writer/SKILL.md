---
name: survey-writer
description: Drafts a topic-clustered literature survey / background section / "state of the art" from the Zotero-synced corpus, with a comparison table and a gap analysis. Every claim is grounded in a citekey pulled from content/ledger.sqlite via src.retrieval.search() -- never a fabricated one. Triggers when the user asks to write, draft, or update a survey paper, literature review, background section, or related-work section for a given topic. Must run `python -m src.citation_gate` on its own output and only present the draft once it passes. Refuses (and tells the user to run `python -m src.sync` first) if the ledger is empty.
tags: [survey, literature-review, citation, zotero]
---

# survey-writer

Genre-specific drafting agent for survey-style output. This is the "generative
drafting" job (job 2) in the two-job pipeline split -- it runs on demand and its
output is reviewed by the user, unlike `python -m src.sync` (job 1, deterministic,
safe to run unattended).

## Shared content layer (read, don't regenerate)

- `content/ledger.sqlite` -- per-citekey status, populated by `sync`
- `content/library.bib` -- generated BibTeX, keyed the same way as the ledger
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

1. **Retrieve.** Break the requested topic into 2-4 sub-themes if it's broad.
   Call `src.retrieval.search(sub_theme, k=8)` for each. This is a keyword-overlap
   ranker, not embeddings (no vector store is installed here) -- read the actual
   snippets, don't trust the score alone.
2. **Cluster by judgment.** With a small corpus there's no BERTopic step; group
   the retrieved citekeys into themes yourself based on what the snippets/titles
   actually say. Note explicitly if a sub-theme returned nothing or near-nothing
   ("thin coverage") -- do not pad it with uncited claims to compensate.
3. **Draft**, in Markdown, using Pandoc-style citations (`[@citekey]`,
   `[@key1; @key2]`):
   - Framing paragraph for the overall topic
   - One subsection per theme, citing the papers that actually support each claim
   - A comparison table: columns for approach/paper, citekey, core idea,
     stated limitations
   - A gap-analysis paragraph: what the retrieved corpus does *not* cover
4. **Never write a citekey you didn't get from a `search()` result.** If you
   want to cite something you know about from general knowledge but that isn't
   in the ledger, say so in prose to the user instead ("X is commonly discussed
   in this area but isn't in your synced library yet") -- do not invent a key
   for it.
5. **Log provenance.** Write `content/provenance/<slug>.json`: a list of
   `{"section": "...", "citekeys": [...]}` so citation choices are auditable
   later without re-reading the whole draft.
6. **Gate before presenting.** Run:
   ```
   python -m src.citation_gate <draft-file>
   ```
   If it reports `FAIL`, fix the offending line(s) — either correct the citekey
   or remove the claim — and re-run until it reports `OK`. Never show the user
   a draft that hasn't passed.
7. Present the draft plus a one-paragraph summary of thin-coverage areas.

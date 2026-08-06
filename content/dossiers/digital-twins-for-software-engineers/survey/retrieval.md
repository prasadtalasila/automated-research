# Retrieval calls

<!-- Appended by `python3 -m src.retrieval ... --log <draft>`, never by
     hand. `chars` is the size of the payload that call handed back --
     the thing that then sits in the caller's context for the rest of the
     run. Together with evidence.md's and rejected.md's counts, this is
     what turns "retrieval is where the tokens go" from an estimate into
     a measurement for a particular draft. -->

*These rows are real `--log` output, not written by hand -- but read the
`chars` column with one caveat. The checkout that produced them has a
ledger with citekeys and titles and **no parsed PDF text**
(`content/parsed/` is per-host and gitignored), so BM25 ranked titles
alone and every snippet is a title-length fragment rather than a window
of a paper. On a real synced corpus the same eight calls return roughly
an order of magnitude more: a triage row lands near `15 x 160 = 2400`
and an evidence row near `2 x 600 = 1200`. What the rows do show
faithfully is the shape -- four triage passes, then `evidence` on
selected survivors -- and that the file fills itself in as a side effect
of retrieving, which is the point of it.*

| date | mode | query | k | results | chars |
|---|---|---|---|---|---|
| 2026-08-06 | triage | digital twin definition levels of integration | 15 | 15 | 1032 |
| 2026-08-06 | evidence | digital twin definition levels of integration | 1 | 1 | 81 |
| 2026-08-06 | triage | digital twin architecture patterns deployment microservices | 15 | 15 | 1063 |
| 2026-08-06 | triage | digital twin platform reuse composition as a service | 15 | 15 | 1008 |
| 2026-08-06 | triage | digital twin failure synchronisation trust simulation gap | 15 | 15 | 997 |
| 2026-08-06 | evidence | digital twin architecture reuse trust | 1 | 1 | 26 |
| 2026-08-06 | evidence | digital twin architecture reuse trust | 1 | 1 | 23 |
| 2026-08-06 | evidence | digital twin architecture reuse trust | 1 | 1 | 29 |

# Scope

- genre: survey
- draft: content/drafts/digital-twins-for-software-engineers/survey.md
- created: 2026-08-06
- corpus: 55 citekeys, digest `5e9e732e92b7`

## Reader

A working software engineer who is at home with message brokers,
containers and CI, has never built a digital twin, and has just been
handed one to build or to assess someone else's.

## Covers

- What distinguishes a twin from systems the reader already ships (the
  coupling criterion, and where the literature disagrees about it)
- Architectural and deployment choices, including the security boundary
  a write path to hardware creates
- Platforms and frameworks that claim to spare you the work
- The failure modes that recur across case studies
- Where twins sit inside a development pipeline
- Exemplars worth cloning, and the gaps in this corpus

## Does not cover

- The physics inside the models
- Manufacturing process detail
- Control theory
- Adoption economics and organisational change
- Consumer-scale deployment guidance: the corpus is dominated by
  manufacturing and infrastructure, and this stayed thin after
  reformulating across agriculture, greenhouse, sensor-actuator and
  small-exemplar phrasings. Reported as a gap in §7 rather than padded.

## Glossary

- **Digital Model** -- a digital counterpart with no automated data flow
  in either direction; a person carries changes across by hand.
- **Digital Shadow** -- automated one-way flow from the physical object
  to the digital one.
- **Digital Twin** -- automated flow in both directions. This is
  Kritzinger's grading, and the draft uses it as its spine; where a
  source disagrees (the Digital Twin Consortium's synchronisation-only
  definition) the draft says so rather than picking a winner.
- **Twin fidelity** -- how completely the twin represents its
  counterpart's state and structure.
- **Twin granularity** -- which parts of the counterpart get modelled at
  all. Distinct from fidelity, and the draft keeps them distinct.
- **DTaaS (Digital-Twin-as-a-Service)** -- a tenant-facing platform
  hosting many twins assembled from shared reusable components.
- **The pot** -- the running example: a potted plant, a soil-moisture
  probe and a small pump. Every section anchors to it. Called "the pot",
  never "the running example" or "the case study".

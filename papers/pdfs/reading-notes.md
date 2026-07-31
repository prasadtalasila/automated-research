# Composable Digital Twins — Reading Notes (ungated)

**Status: not citation-gated.** These notes summarize `papers/pdfs/manifest.json`
(22 candidate works surfaced via OpenAlex/Semantic Scholar/arXiv metadata search,
2026-07-28). None of these works are in `content/ledger.sqlite` yet, so none of
them have a citekey from `src.retrieval.search()`. Per CLAUDE.md's citekey
invariant, this document cites by **(Author/venue, Year, DOI)** only — it must
not be treated as a source of citekeys by `survey-writer`, `thesis-chapter-writer`,
or `tutorial-writer`. To make these usable in a gated survey, add them to Zotero
(Add by Identifier, using the DOIs in the manifest) and run `python -m src.sync`.

Only three of the 22 works could actually be downloaded as full text in this
environment — the rest are closed-access, or are nominally open-access but sit
behind a publisher anti-bot wall (MDPI, IEEE Xplore) or a landing page rather
than a direct PDF link (ScienceDirect, PNAS, an institutional repository, and
a repository "handle" page). Of those three, only two are peer-reviewed
papers (the DTaaS platform paper and the modular collaborative-robotics
safety paper, both discussed below). The third,
`ai-composable-dt-teleop-2024.pdf`, is a UPC Barcelona student exchange
final-year report, not a peer-reviewed source — its actual content (ML-based
anomaly detection on a vacuum-gripper robot) barely engages with
composability despite the title, so it is not used as evidence below; it's
kept in the manifest only because it was legitimately downloadable and might
still be worth a skim. The synthesis leans on the two peer-reviewed full
texts where possible and otherwise draws only on publisher abstracts
(reconstructed from OpenAlex), so claims about the abstract-only works are
necessarily shallower.

## What "composable" means across this set

No single definition dominates; the works cluster into a few distinct notions
of composability that get conflated under the same word:

1. **Asset/library composition** — a DT is assembled from a catalog of reusable
   *data, model, function, and tool* assets, with the platform handling storage,
   compute provisioning, and lifecycle rather than the user hand-coding each DT.
   This is the DTaaS line: Talasila et al., "Digital Twin as a Service
   (DTaaS): A Platform for Digital Twin Developers and Users" (2023, IEEE SWC,
   DOI 10.1109/swc57546.2023.10448890; PDF retrieved is the accepted
   manuscript, arXiv:2305.07244, at `dtaas-platform-2023.pdf`) defines four
   asset categories (D/M/F/T) and six lifecycle phases
   (create/execute/save/analyse/evolve/terminate), explicitly citing
   "composition" as an index term. The already-ledgered
   `talasila2025composable` (2024, DOI 10.1177/00375497241298653) appears to be
   the follow-on journal version of the same DTaaS platform, adding two case
   studies — the two together look like the core of this user's own
   composability work and a natural anchor citation for any survey on this topic.

2. **Sub-DT-to-DT composition (horizontal)** — individual DTs of subsystems or
   entities are linked into a higher-order DT. OpenTwins (Robles et al., 2023,
   DOI 10.1016/j.compind.2023.104007) frames this explicitly: "digital twins
   that link individual entities or subsystems to create a higher degree
   digital twin." The IoE user-centric architecture (2024, DOI
   10.1109/meditcom61057.2024.10621154) splits this into vertical
   intra-twin communication (digital replica ↔ physical object) and horizontal
   inter-twin communication (DT ↔ DT via a shared AI-driven Service Layer).

3. **Microservice/SOA-style composability** — DTs as software-centric,
   discoverable, independently deployable services. "Distributed Digital
   Twins as Proxies" (2023, DOI 10.1109/access.2023.3340132) and "A Composable
   Architectural Model for Digital Twin Computing Applications" (2026, DOI
   10.3390/app16094541) both propose a control/orchestration plane (a "Service
   Catalog" of reusable microservices in the latter) over a distributed set of
   DT proxies, explicitly targeting reusability, modularity, and scalability
   as first-class architectural properties rather than incidental benefits.

4. **Model/behavioral composition via formal methods** — composition as an
   assignment or scheduling problem. "Digital twin composition in smart
   manufacturing via Markov decision processes" (2023, DOI
   10.1016/j.compind.2023.103916) treats DT composition as analogous to Web
   service composition, using MDPs to assign devices to manufacturing tasks
   under uncertainty, with provably cost/quality-optimal policies. "A Modeling
   Approach for Composed Digital Twins in Cooperative Systems" (2023, DOI
   10.1109/etfa54631.2023.10275601) adds an ontology/semantic layer atop an
   object-oriented information model specifically to support composition and
   reusability, demonstrated on two cooperative robot arms.

5. **Modularity as a structuring principle, not composition per se** — several
   works use "modular" to mean internally decomposed and reconfigurable rather
   than assembled from independently-published external assets: the medical
   digital twin framework (Masison et al., 2021, DOI 10.1073/pnas.2024287118,
   PNAS) is a modular *software platform* for integrating heterogeneous,
   community-contributed disease models; the collaborative-robotics safety
   framework (Douthwaite et al., 2021, DOI 10.3389/frobt.2021.758099 — full
   text retrieved, `modular-dt-safety-assurance-cobots-2021.pdf`) is modular in
   that it standardizes representation/communication across a mixed environment
   of Digital Models, Digital Shadows, and Digital Twins for a single safety
   investigation, not in that it publishes reusable third-party assets. The
   earliest work in this set, "Modular based flexible digital twin for
   factory design" (Guo et al., 2018, DOI 10.1007/s12652-018-0953-6, abstract
   not available — title/authorship only), appears to belong here too, on
   title evidence alone.

## Cross-cutting themes

- **Reuse is the recurring justification.** Nearly every abstract in this set
  motivates composability/modularity by reducing the effort of building a DT
  "from scratch" — Talasila et al. open with exactly this framing, and it
  reappears almost verbatim in OpenTwins, the meta-model for human DTs
  (Montini et al., 2021, DOI 10.1016/j.procir.2021.11.116), and the mining PMS
  paper (2024, DOI 10.3390/designs8030040).
- **Robustness/safety of composition is a distinct, less-populated sub-thread.**
  Most papers treat composition as an enabler and stop at feasibility
  demonstrations. Two works explicitly worry about what can go wrong when
  independently-developed DTs interact: Preuveneers et al. (2018, DOI
  10.1109/edocw.2018.00021) add feature toggles and software circuit breakers
  so a faulty composed DT doesn't cascade errors through a production
  workflow; Douthwaite et al.'s modular framework exists specifically to
  produce safety-assurance evidence for a composed collaborative-robotics
  scenario. This looks like a thinner, more citable gap than "composability"
  itself, which is already crowded.
- **Domain spread is wide, architectural convergence is partial.**
  Manufacturing/Industry 4.0 dominates (Preuveneers et al. 2018; De Giacomo et
  al.'s MDP paper 2023; the mining PMS paper 2024; the sustainability-oriented
  modular DT, Werner et al. 2024, DOI 10.1080/00207543.2024.2366997), but
  composability claims also appear in healthcare/medicine (Masison et al.
  2021, PNAS), power systems (Andryushkevich, Kovalyov & Nefedov 2019, DOI
  10.1109/indin41052.2019.8972267, ontology-based composition), robotics
  (2018/2021/2022/2023 cluster above), and IoE/telecom (Amadeo et al. 2024,
  MEDITCOM). None of the abstract-only works cite each other's architectural
  vocabulary consistently — "composable," "compositional," and "modular" are
  used almost interchangeably across domains without a shared reference
  architecture, which itself may be worth stating as a gap.
- **A distinct Aarhus DIGIT cluster sits inside this set and is the most
  directly relevant anchor for this repo's existing corpus.** Besides the
  already-ledgered `talasila2025composable` (Talasila, Gomes, Vosteen et al.,
  2024) and its arXiv predecessor retrieved here (Talasila, Gomes, Mikkelsen,
  Gil Arboleda, Kamburjan, Larsen, 2023), three more closed-access works in
  this candidate set share authors with that group: "A Modeling Approach for
  Composed Digital Twins in Cooperative Systems" (Gil, Mikkelsen, Tola et al.,
  2023) — Gil and Mikkelsen are also DTaaS-platform co-authors; "Towards
  Modular Digital Twins of Robot Systems" (Tola, Böttjer, Larsen et al.,
  2022); and "Towards the Composition of Digital Twins" (Larsen, Talasila,
  Fitzgerald, 2024 — title and author list only; OpenAlex has no abstract for
  this one, so its content is inferred from the title alone and should be
  verified before relying on it). Read together, these five look like one
  research program's progression from asset-based DT platforms (DTaaS, 2023)
  through composed-DT modeling for cooperative robots (2023) and modular
  robot DTs (2022) toward an explicit composition framing (2024, unverified)
  — worth surfacing as a single narrative thread rather than five independent
  data points, and worth
  getting the three closed-access ones via institutional access rather than
  open APIs, since they're clearly load-bearing for this exact topic. The
  unrelated, closed-access "DTaaS in Industry 4.0: An Architecture Reference
  Model" (Aheleroff, Xu, Zhong et al., 2020) is a different, non-Aarhus DTaaS
  lineage and shouldn't be conflated with this cluster despite the shared
  acronym.

## Retrieval gaps (things a real survey would still be missing)

- Four OA-labeled PDFs could not be retrieved automatically because the
  publisher (MDPI ×2, IEEE Xplore, Taylor & Francis) served an anti-bot page
  instead of the file: the composable-architectural-model paper, the mining
  PMS paper, the IEEE Access proxies paper, and the sustainability modular-DT
  paper. These are real open-access papers, just not fetchable by an
  unattended script — grab them by hand in a browser session if they matter
  for the eventual gated survey. Cross-checking all four against Unpaywall
  turned up the same blocked URLs, not an alternate route.
- Six more resolve to an HTML landing page rather than a direct PDF: four are
  publisher article pages for hybrid/gold-OA content (OpenTwins, the MDP
  composition paper, the human-DT meta-model, the medical DT framework), and
  two are repository/aggregator pages that don't expose a direct file link
  (the IoE composition paper's Aalto research-portal entry, and the KU Leuven
  Lirias handle page for the 2018 robust-compositions paper). Same fix: open
  in a browser and download by hand.
- An eight-paper closed-access set is genuinely paywalled — none of the open
  APIs queried here (OpenAlex, Semantic Scholar, arXiv, Unpaywall) found an
  author-deposited copy: the ETFA cooperative-systems paper, "Achieving Scale
  Through Composable and Lean Digital Twins" (book chapter), the 2025
  AAS/Industry 4.0 composition paper, "Towards Modular Digital Twins of Robot
  Systems" (ACSOS), the power-system ontology paper, "Towards the Composition
  of Digital Twins" (book chapter — part of the Aarhus cluster above), the
  2020 DTaaS architecture-reference-model paper, and the 2018 factory-design
  modular-DT paper. These would need institutional access (e.g. via IEEE
  Xplore/ScienceDirect credentials the user already has) rather than anything
  fetchable from outside.

## Suggested next step

Add the DOIs marked `"downloaded": false` in `manifest.json` to Zotero (Add by
Identifier is fastest and pulls real metadata rather than a bare PDF), attach
PDFs by hand for the ones behind anti-bot walls, then run `python -m src.sync`.
At that point `survey-writer` can produce a properly citekey-gated version of
this survey instead of this prose-only synthesis.

# Kept evidence

<!-- One block per citekey that survived relevance scoring. A citekey the
     draft cites should appear here; one that was retrieved and turned
     down belongs in rejected.md instead. -->

## `kritzinger_digital_2018`

- relevance: the draft's spine. Grades digital counterparts by level of integration rather than fidelity, which is what lets §1 open on coupling.
- support: separates Digital Model / Digital Shadow / Digital Twin, and finds work at the highest stage the scarcest.

## `ellwein_rethinking_2025`

- relevance: restates Kritzinger's criterion as the degree of *automated* integration, which is the precise wording §1 needs.
- support: a Digital Model needs a person to carry changes across by hand.

## `heithoff_model-based_2024`

- relevance: supplies the Digital Shadow definition verbatim, and separately the update-cadence cost in §4.
- support: a shadow has "an automated one-way data flow" from physical to digital; consuming everything the sensors produce would stop the software delivering results in meaningful time.

## `bergs_concept_2021`

- relevance: corroborates that shadow and twin are treated as a paired distinction, not one author's coinage.
- support: the pairing appears as an established distinction in manufacturing.

## `vanderhorn_digital_2021`

- relevance: evidence that definitional variance is itself a reported problem, not just an inconvenience noticed here.
- support: definitions vary widely across the literature.

## `semeraro_digital_2021`

- relevance: second, independent source for the same variance claim -- §1 states it as reported rather than observed.
- support: surveys the competing definitions.

## `minerva_digital_2020`

- relevance: the licence for modelling a living plant as three numbers, which the pot example depends on.
- support: a twin reflects only the properties that matter "within a specific application context".

## `bhandal_conceptualising_2024`

- relevance: the other side of §1's disagreement. Kept specifically because it contradicts the spine.
- support: quotes the Digital Twin Consortium definition -- "synchronized at a specified frequency and fidelity" -- naming no automated flow back to the physical side.

## `ferko_architecting_2022`

- relevance: establishes that twin architecture is a claimed subject with peer-reviewed solutions, which is what §2 opens on.
- support: a growing body of peer-reviewed architectural solutions for twins.

## `tekinerdogan_systems_2020`

- relevance: a catalog of shapes to instantiate rather than principles to derive -- directly actionable for the reader, so it is also a comparison-table row.
- support: systematic catalog of twin architecture patterns.

## `lehner_pattern_2023`

- relevance: the behaviour counterpart to `tekinerdogan_systems_2020`; paired with it in both §2 and the table.
- support: patterns for giving twin models behaviour rather than structure alone, via model-driven engineering.

## `bellavista_entanglement-aware_2024`

- relevance: names the live architectural split (twin as data vs twin as process) that §2 is organised around.
- support: commercial platforms represent twins as passive JSON entities; research increasingly treats them as orchestrated microservices.

## `bellavista_exploiting_2024`

- relevance: the deployment realisation of the same argument, across cloud-to-edge.
- support: microservices and serverless functions across the cloud-to-edge continuum.

## `wermann_ktwin_2024`

- relevance: the concrete "you already know these tools" instance for this reader.
- support: twins on Kubernetes with ordinary cloud-native tooling and open standards for models; prototype-stage evaluation.

## `barbone_digital_2024`

- relevance: supports the claim that managing deployments *across* the range is the under-explored part, not any single deployment style.
- support: deployments span centralised cloud, edge-deployable brokers and containerised twins; managing them across that range remains under-explored.

## `alcaraz_digital_2022`

- relevance: the "unfamiliar edge" of §2 -- a service that writes to a pump is a security boundary.
- support: an adversary can attack from the digital side to reach physical assets; the attack surface is large because the paradigm joins two worlds.

## `kulik_security_2024`

- relevance: second security source, kept because it addresses traffic criticality rather than repeating the surface argument.
- support: near-real-time twin traffic can carry critical signals; adding a twin expands an already growing attack surface.

## `talasila_realising_2024`

- relevance: states the construction-cost problem §3 answers, and proposes the platform response.
- support: building a twin is hard because of the variety of assets, models, data and services to marshal; proposes a generic platform assembling twins from reusable components offered as a service.

## `talasila_composable_2025`

- relevance: the follow-on demonstration, and one of the few consumer-scale-adjacent sources for §7's scale gap.
- support: demonstrates composition of twins from parts.

## `gil_survey_2024`

- relevance: the framework landscape for a reader deciding whether to buy or build.
- support: surveys open-source twin frameworks through a case study; single case limits generalisation.

## `zech_digital-twins-as--service_2024`

- relevance: evidence that the DTaaS framing recurs independently rather than being one group's term.
- support: twins offered as a service.

## `duran_toward_2026`

- relevance: supplies the fidelity/granularity vocabulary the glossary fixes, plus a second independent DTaaS framing.
- support: *twin fidelity* is how completely the twin represents its counterpart's state and structure; *twin granularity* is which parts get modelled at all.

## `frasheri_addressing_2023`

- relevance: §4's clock failure mode, stated as a hard constraint rather than a tuning concern.
- support: coupling a simulation to hardware forces simulation time to follow wall-clock time, making the execution time of a single iteration a critical parameter.

## `zhang_knowledge_2024`

- relevance: puts both sides of the update-cadence cost in one place, which is what makes it a design decision rather than a default.
- support: too-frequent updates burn communication and computation; delayed updates let the model drift from reality.

## `gomes_sensing_2024`

- relevance: supports the specific claim that the sampling rate and the link are engineered choices, not device properties.
- support: treats sensing and the link carrying samples as engineered parts of the twin.

## `oakes_case_2024`

- relevance: names the simulation-reality gap as the main challenge across worked cases -- evidence for §4's trust failure mode.
- support: the simulation-reality gap recurs as the main challenge across case studies.

## `committee_on_foundational_research_gaps_and_future_directions_for_digital_twins_foundational_2024`

- relevance: raises trust from a project problem to an institutional one, which is the note §4 ends on.
- support: organisations often cannot tell how well a twin matches reality or whether to rely on it for critical decisions.

## `hugues_twinops_2022`

- relevance: the direct bridge from twins to a practice this reader already has.
- support: TwinOps connects twins to DevOps practice.

## `barbie_toward_2024`

- relevance: the most directly reusable §5 source -- CI for twins, with replication artefacts.
- support: tests twin prototypes through automated integration tests in a CI pipeline, protocols included, with a smart-farming case study and open artefacts.

## `dalibor_cross-domain_2022`

- relevance: establishes that the software engineering literature on twins has been mapped, so §5 is summarising a field rather than asserting one.
- support: maps the software engineering literature on twins across domains.

## `mertens_continuous_2024`

- relevance: twins change under new requirements -- the evolution problem, which §5 needs to avoid reading as a build-once story.
- support: notation for the continuous evolution of twins.

## `beaumont_towards_2025`

- relevance: the lifecycle-automation counterpart to `mertens_continuous_2024`.
- support: automation of twin lifecycle management.

## `kamburjan_declarative_2024`

- relevance: frames twin and counterpart as one self-adaptive system, which the pot example makes concrete.
- support: a greenhouse plant that becomes infected needs a different watering regime, so the physical system's stage changes which twin components apply.

## `kamburjan_greenhousedt_2024`

- relevance: the closest published match to this reader's use case. The recommendation in §6 rests on it.
- support: an extensible architecture whose physical system is plants, sensors and water pumps, with the asset model in a knowledge base; deliberately simple and low-cost.

## `gomes_digital_2025`

- relevance: a tutorial-grade exemplar built from parts the reader recognises.
- support: incubator twin using ordinary differential equations, Kalman filtering and a service-oriented architecture; single case study.

## `goffi_engineering_2025`

- relevance: the closed-loop exemplar -- the only kept source demonstrating direct control of a physical process.
- support: direct control of a physical process modelled as a finite state machine; domain-specific process constraints.

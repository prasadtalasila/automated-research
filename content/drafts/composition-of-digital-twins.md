# Composition of Digital Twins: A Literature Survey

Digital twins (DTs) are rarely built as monolithic artifacts. Because a DT
must represent a physical twin (PT) across several disciplinary layers --
information models, geometry, physics, and behavior -- constructing one from
scratch is a significant, cross-disciplinary undertaking. A recurring
response in the literature is to treat a DT as a *composition* of smaller,
reusable parts (data, models, functions/algorithms, tools, and even other
DTs) rather than as a single bespoke program, and to give the composed whole
an explicit configuration that can be validated, reused, and reconfigured
over the DT's life cycle [@talasila2025composable]. The corpus currently
synced from this project's Zotero library speaks to this question from two
different altitudes: one paper treats composition as its primary subject
and proposes a platform-level formalism for it; the other builds a single
application-level DT and, in passing, exercises a composition of its own
(behaviors, monitors, and state assembled around one physical robot). Both
are drawn on below; coverage of the second is thin and is flagged as such.

## Composable assets as the unit of construction

[@talasila2025composable] proposes the Digital Twin as a Service (DTaaS)
platform, whose central design move is to decompose a DT into four
reusable asset categories -- **Data** (sources/sinks such as sensor streams
or visualization endpoints), **Model** (descriptions of the PT and its
environment at a given abstraction level, following Functional Mock-up
Interface nomenclature for inputs/outputs/states/parameters), **Function**
(pre- and post-processing code that adapts data to a model's expected
format), and **Tool** (software implementing domain-specific or generic
algorithms, e.g., numerical solvers or co-simulation engines). A DT design
is formally defined as a tuple drawn from the power sets of these four
categories, `Dt ∈ P(D) × P(M) × P(F) × P*(T)`, with the constraint that a
valid design contains at least one tool; a concrete DT instance is produced
by pairing a design with a *configuration* `Cdt ∈ Ca × Ci × Ce × Cpt`
(asset selection/parameters, infrastructure, external-service bindings, and
PT-connection parameters, respectively). This gives composition a checkable
structure: only functions and tools may consume models and data, and a
"validity check" is required whenever a user-driven change is proposed to
any part of `Cdt`.

The paper demonstrates this with two case studies -- an incubator DT (four
interchangeable plant models, a controller model, an environment model, and
tools drawn from SciPy and PyTorch) and a firefighter DT (building-navigation
tools, air-consumption/pressure-to-time tools, and a TeSSLa-based runtime
monitor) -- plus seven further exemplars (Table 3 of the source) spanning
robotics, food safety, physics, and water systems. Several of these
exemplars compose a DT out of *other* DTs: the incubator-with-runtime-monitor
exemplar nests one DT inside another, and the Flex-Cell Robots exemplar
contains two independently defined robot DTs as constituents of a larger
one -- evidence that, in this platform, composition is not limited to a
single flat level of assets but can recurse over DTs themselves
[@talasila2025composable].

## Composition mechanisms compared across platforms

Beyond its own asset model, [@talasila2025composable] situates DTaaS
against five other frameworks using a shared comparison criterion set
adapted from prior surveys: description/structuring mechanism, composition
mechanism, DT-to-DT relationships, default bidirectional synchronization,
ability to run simulators, binding to infrastructure services, binding to
DT/optimization services, and re-usability. The composition mechanisms
reported for the compared systems differ mainly in *what* is being
aggregated:

- Eclipse BaSyX (Asset Administration Shell) composes by aggregation of
  Sub-models.
- Eclipse Ditto composes by aggregation of Features.
- Microsoft's Digital Twin Description Language (DTDL) composes Interfaces
  via Components, with cross-entity composition expressed through a
  `Relationships` field.
- Eclipse Vorto composes by aggregation of Function Blocks.
- The INTO-CPS co-simulation framework composes by aggregation of
  hierarchical FMU simulators.
- DTaaS itself composes by aggregation of the four asset types above.

Of the compared systems, only DTDL specifies DT-to-DT relationships
*explicitly* (via a `Relationships` field); BaSyX and Vorto support them
only *implicitly* (via semantic identifiers or model references,
respectively); Ditto and the INTO-CPS framework are marked not applicable.
DTaaS's own cell in this comparison states that DT-DT relationships are
"only applicable for DT composition," which the source does not classify
as either explicit or implicit -- and the paper elsewhere acknowledges this
remains a weak point of its own approach, noting DTaaS "struggles with
incorporating DT-to-DT relationships, especially for a more semantically
accurate way to compose DTs" [@talasila2025composable]. The paper also
distinguishes two
complementary routes to composition found in the wider literature:
*asset reuse* (as in DTaaS, DIGITbrain, and the Digital Twin Consortium's
Platform Stack Architectural Framework, which itself proposes data, model
representations, algorithms, and services as reusable assets) versus
*code generation* from domain-specific languages that emit the
application-independent parts of a DT platform, which it treats as
complementary to, rather than competing with, asset-based composition.
Knowledge graphs are separately identified as a candidate technology for
representing the relationships between composed assets (used, for example,
in the SINDIT architecture) and for expressing consistency-checking queries
over a DT's configuration during reconfiguration.

The paper is explicit that its own composition mechanism only guarantees
*technical* combinability, not semantic correctness: "it is in the
responsibility of the DT creator to assure a semantically meaningful
combination" of models, since simulation granularity, notion of time, and
physical units still have to be reconciled by hand when assets are
composed [@talasila2025composable].

## Composition inside a single applied architecture (thin coverage)

[@anon2026digital] does not address composition as a research question --
its subject is runtime verification of an autonomous mobile robot under
uncertainty -- but its proposed DT architecture is itself an instance of
composing a DT from distinguishable parts: **Data** (an MQTT broker plus
data storage), **Operations** (the interface that ingests PT data into DT
state), **State**, **Behaviors** (model+simulator pairs, each simulating
one aspect of the PT), and **Monitors** (TeSSLa-synthesized runtime
checks), wired together so that state changes trigger both monitors and
behaviors, and behaviors in turn update state and issue directives back to
the PT. The paper frames its own system as "a Digital Twin as-a-Service
(DTaaS) given that it provides service layers ... including data storage,
runtime monitoring, simulation and runtime validation," explicitly aligning
its architecture with the DTaaS asset vocabulary rather than proposing a
distinct composition formalism of its own [@anon2026digital]. Coverage of
composition specifically is thin here: the paper does not discuss asset
reuse across DTs, configuration validity, or DT-to-DT relationships --
it demonstrates one composed instance for one robot rather than a general
mechanism.

## Comparison table

| Approach / paper | Citekey | Core composition idea | Stated limitations |
|---|---|---|---|
| DTaaS asset-based composition | `talasila2025composable` | DT = design (tuple of Data/Model/Function/Tool power sets) + configuration (`Ca`, `Ci`, `Ce`, `Cpt`); DTs can nest other DTs as constituents | Weak on explicit DT-to-DT relationships relative to DTDL; only guarantees technical, not semantic, compatibility of composed assets; `Ca`/`Cpt` are design-specific and resist generalization into a configuration standard [@talasila2025composable] |
| Eclipse BaSyX (AAS) | [@talasila2025composable] (secondary; not a separate item in this library) | Aggregation of Sub-models | DT-DT relationships only implicit, via semantic identifiers/References |
| Eclipse Ditto | [@talasila2025composable] (secondary; not a separate item in this library) | Aggregation of Features | DT-DT relationships: N/A |
| DTDL (Microsoft Azure) | [@talasila2025composable] (secondary; not a separate item in this library) | Composition of Interfaces via Components; explicit DT-DT `Relationships` field | Requires a back-end to realize bidirectional sync (classified as Digital Model by default) |
| Eclipse Vorto | [@talasila2025composable] (secondary; not a separate item in this library) | Aggregation of Function Blocks | DT-DT relationships only implicit, via Model References |
| INTO-CPS co-simulation framework | [@talasila2025composable] (secondary; not a separate item in this library) | Aggregation of hierarchical FMU simulators | DT-DT relationships: N/A |
| Applied DTaaS-aligned architecture for a mobile robot | `anon2026digital` | Composes Data/Operations/State/Behaviors/Monitors around one robot via MQTT; self-identifies as a DTaaS instance | Does not address reuse, configuration validity, or DT-to-DT composition; single-instance demonstration, not a general mechanism [@anon2026digital] |

The BaSyX, Ditto, DTDL, Vorto, and INTO-CPS rows report what
[@talasila2025composable]'s own Table 2 says about those platforms --
their primary sources are not separately present in this library, so
nothing in those rows should be attributed beyond what that one paper
reports.

## Gap analysis

The synced library currently contains two items, and only one of them
(`talasila2025composable`) treats composition as a first-class research
question; the other exercises a composition informally, as a byproduct of
building one runtime-verification system. Several sub-themes one would
expect in a fuller survey of DT composition are therefore not covered by
this corpus at all and should not be treated as covered:

- **Formal/semantic verification of composed DTs.** `talasila2025composable`
  explicitly leaves semantic correctness of a composition to the DT
  creator's judgment; no paper in this library proposes automated checking
  of semantic compatibility (units, time granularity) across composed
  assets.
- **Standardized composition/configuration languages.** DTDL, AAS, and
  Vorto are only visible here second-hand, through one paper's comparison
  table -- their own primary sources are not in the synced library, so no
  claim about them beyond what `talasila2025composable` reports should be
  attributed to them.
- **Composition at scale / multi-DT constellations beyond the two-level
  nesting shown in the Flex-Cell Robots and incubator-with-monitor
  exemplars.** Neither paper studies large graphs of composed DTs or their
  performance/consistency behavior at scale.
- **Runtime-verification-driven composition patterns generalized beyond a
  single robot.** `anon2026digital`'s Monitors/Behaviors/Data/State
  decomposition is a promising second example of asset-style composition
  in an applied setting, but the paper does not generalize it or compare
  it against other composition mechanisms, so it cannot be used to support
  claims about composition approaches in general.

If a broader account of DT composition is needed (e.g., dedicated
treatments of DTDL, AAS/BaSyX, or Vorto as primary sources, or papers
specifically on semantic verification of composed models), those sources
are not yet in this project's synced library and would need to be added to
Zotero and picked up by `python -m src.sync` before they could be cited
here.

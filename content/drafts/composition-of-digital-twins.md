# Composition of Digital Twins: A Literature Survey

Digital twins (DTs) are rarely built as monolithic artifacts. Because a DT
must represent a physical twin (PT) across several disciplinary layers --
information models, geometry, physics, and behavior -- constructing one from
scratch is a significant, cross-disciplinary undertaking. A recurring
response in the literature is to treat a DT as a *composition* of smaller,
reusable parts (data, models, functions/algorithms, tools, and even other
DTs) rather than as a single bespoke program, and to give the composed whole
an explicit configuration that can be validated, reused, and reconfigured
over the DT's life cycle [@talasila_composable_2025]. This is an update of
an earlier version of this survey, written when the project's Zotero-synced
library held only two items. The library has since grown to 643 items after
a fresh export; this update re-ran retrieval against the larger corpus and
found genuinely new, composition-specific evidence -- but composition is
still a minority theme even in the larger library (most of the newly synced
items are general DT surveys, domain case studies, or unrelated material).
What follows keeps the original two-item core (`talasila_composable_2025`
still gives by far the most complete formalism, and it is joined below by
roughly a dozen new supporting or complicating sources) and integrates the
new material theme by theme, updating the earlier draft's gap analysis
where the larger corpus now covers -- or still doesn't cover -- something it
previously flagged as missing.

## Composable assets as the unit of construction

[@talasila_composable_2025] proposes the Digital Twin as a Service (DTaaS)
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
[@talasila_composable_2025]. The newly retrieved sources below (see
"DT-to-DT relationships and multi-DT composition") show this recursive
nesting is not idiosyncratic to DTaaS -- other, unrelated platforms arrive
at comparable DT-in-DT mechanisms independently.

## Composition mechanisms compared across platforms

Beyond its own asset model, [@talasila_composable_2025] situates DTaaS
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

The earlier version of this survey could only attribute the first five of
these to [@talasila_composable_2025]'s own report, since none of those
platforms' primary sources were in the synced library at the time. That is
now only partly true. The Asset Administration Shell in particular is no
longer second-hand: the AAS specification itself is now in the corpus
[@noauthor_asset_nodate], and independent surveys and case studies confirm
the same aggregation-of-Submodels mechanism from primary and near-primary
sources: "[The AAS] is made up of multiple Submodels which itself are
comprised of several SubmodelElements... Submodels can exist independently
of an AAS as indicated by the aggregation relationship between them"
[@jacoby_open-source_2023]; an independent systematic mapping study of AAS
communication types states the same mechanism directly, "the data
representing assets are structured into submodels, each of which addresses
specific aspects of the asset's life cycle" [@ellwein_rethinking_2025]; and
an applied product-passport case study composes an AAS from several
lifecycle-phase submodels, with, for example, "the carbon footprint of all
used materials, manufacturing processes and transports... collected and
aggregated to provide information to end users" [@gleich_asset_2024]. A
separate (non-AAS, non-DTaaS) source *reports* -- in its own related-work
section, so this is a second-hand account within that paper, not a
first-hand description -- that a similar aggregation style appears
elsewhere too: RAMI 4.0 "defines a three dimensional model decomposed into
hierarchy levels," and an SOA-based architecture composes device services
via "vectorial composition" [@schnicke_enabling_2020]. DTDL similarly now
has a second, more concrete anchor beyond [@talasila_composable_2025]'s
report of it: a pattern catalog
for "augmenting Digital Twin models with behavior," demonstrated by
"realization of the patterns in the Microsoft DT platform," shows DTDL
supporting compositional extension of a structural model with behavior
models via reusable patterns [@lehner_pattern_2023]. Eclipse Ditto and
Eclipse Vorto's own composition mechanisms (aggregation of Features and of
Function Blocks, respectively) remain second-hand -- no primary source
describing either mechanism directly was found in this update's retrieval,
only their names appearing in passing in comparison and platform-choice
discussions (e.g. [@zech_digital-twins-as--service_2024]'s system, discussed
below, which *uses* Ditto but does not itself describe Ditto's own
composition semantics).

Of the compared systems, only DTDL specifies DT-to-DT relationships
*explicitly* (via a `Relationships` field) according to
[@talasila_composable_2025]'s comparison table; BaSyX and Vorto support them
only *implicitly* (via semantic identifiers or model references,
respectively); Ditto and the INTO-CPS framework are marked not applicable.
This update's retrieval found evidence that qualifies "only DTDL" as a claim
about *this specific comparison table* rather than about the field in
general: an unrelated industrial-manufacturing DT architecture builds
explicit, first-class vertical (hierarchy) and horizontal (equipment-order)
relationships directly into its DT core model, with hierarchies formed by
"connecting the DI [Digital Interface] of a DT to a PI [Physical Interface]
of a second DT" [@martinelli_hierarchical_2024] -- see the dedicated section
below. DTaaS's own cell in the comparison states that DT-DT relationships
are "only applicable for DT composition," which the source does not
classify as either explicit or implicit -- and the paper elsewhere
acknowledges this remains a weak point of its own approach, noting DTaaS
"struggles with incorporating DT-to-DT relationships, especially for a more
semantically accurate way to compose DTs" [@talasila_composable_2025]. The
paper also distinguishes two complementary routes to composition found in
the wider literature: *asset reuse* (as in DTaaS, DIGITbrain, and the
Digital Twin Consortium's Platform Stack Architectural Framework, which
itself proposes data, model representations, algorithms, and services as
reusable assets) versus *code generation* from domain-specific languages
that emit the application-independent parts of a DT platform, which it
treats as complementary to, rather than competing with, asset-based
composition. Knowledge graphs are separately identified as a candidate
technology for representing the relationships between composed assets
(used, for example, in the SINDIT architecture) and for expressing
consistency-checking queries over a DT's configuration during
reconfiguration -- previously attributed only second-hand via that one
mention. A dedicated primary source is now available: "semantic reflection"
combines runtime reflection with knowledge-graph-based semantic technologies
specifically for digital twins, on the grounds that "ontologies, knowledge
graphs and related semantic technologies have been identified as a key
technology for digital twins" [@hinchey_semantic_2025].

A companion platform-landscape paper from the same DTaaS project, published
before [@talasila_composable_2025], surveys a wider set of DT frameworks by
domain and features rather than by composition mechanism -- Azure DT/DTDL,
AWS IoT Greengrass, Eclipse Ditto, several AAS implementations (BaSyx,
PYI40AAS, SAP I4.0 AAS, NOVAAS), iTwin, Unity, TerriaJS, the Digital Twin
Cities Centre platform, CPS-Twinning, Twined, and the INTO-CPS co-simulation
framework [@talasila_realising_2024]. It is useful context on how many
platforms exist and roughly what each targets, but -- unlike
[@talasila_composable_2025]'s Table 2 -- it does not itself compare
composition mechanisms, so it is cited here only for that landscape context,
not as a second source for the composition-mechanism claims above.

The paper is explicit that its own composition mechanism only guarantees
*technical* combinability, not semantic correctness: "it is in the
responsibility of the DT creator to assure a semantically meaningful
combination" of models, since simulation granularity, notion of time, and
physical units still have to be reconciled by hand when assets are
composed [@talasila_composable_2025]. This update found one concrete,
narrower counterexample to "no automated checking exists at all": a
DT composed from three FMUs (a message-broker FMU, a controller FMU, and an
"out-of-sync" monitor FMU) over the INTO-CPS Maestro co-orchestration engine
includes an automated mechanism -- the out-of-sync FMU -- that detects the
time discrepancy between the composed simulation units and the physical
twin and suspends/re-enables their communication accordingly
[@frasheri_addressing_2023]. This is real automated checking of one specific
compatibility dimension (temporal granularity/synchronization) between
composed assets, not the general semantic-compatibility checking (units,
quantities) that [@talasila_composable_2025] says is left to the DT creator
-- so it narrows, rather than closes, that part of the earlier gap analysis.

## Composition-driven reconfiguration across the DT lifecycle

A cluster of newly retrieved papers, all from the same research group,
treats DT composition as something that must *change* over a physical
asset's lifecycle rather than being fixed at design time -- a concern the
earlier version of this survey did not cover at all. An asset model is
defined as "an organized description of the composition and properties of
some physical asset," paired with a `reconfigure()` operation on the DT
itself [@kamburjan_digital_2022]. A follow-up formalizes this as declarative
lifecycle stages: transitions between stages in a physical asset's lifecycle
("commissioning, via operations, to decommissioning") trigger reconfiguring
which DT components are active, and the same mechanism supports "multiple
lifecycles, each with different stages" running concurrently
[@kamburjan_declarative_2024]. A worked exemplar (a greenhouse DT) makes the
composition angle explicit: "foreseen changes in the composition of the
physical system... require models to be partly replaced or recomposed"
[@kamburjan_greenhousedt_2024]. Together these three treat reconfiguration
as the composition problem's time dimension: which assets are composed into
a DT is not static, but is driven by declarative stage descriptions tied to
the physical asset's own lifecycle. This is a different mechanism from
DTaaS's own configuration-validity check (Section "Composable assets..."
above) -- DTaaS validates a proposed configuration change; this line of work
instead *triggers* the change from an external lifecycle model. Neither
source engages with the other, so no direct comparison between the two
approaches can be attributed to either paper; this remains open.

## DT-to-DT relationships and multi-DT composition

The earlier version of this survey flagged "composition at scale / multi-DT
constellations beyond the two-level nesting shown in the Flex-Cell Robots
and incubator-with-monitor exemplars" as a gap, and separately noted that,
per [@talasila_composable_2025]'s own comparison table, only DTDL supports
DT-to-DT relationships explicitly. Two newly retrieved papers substantially
narrow both gaps, independently of DTaaS and of each other.

An industrial-manufacturing DT architecture builds hierarchy directly into
its DT model: each DT exposes a Digital Interface (DI, for interaction with
other DTs and applications) and a Physical Interface (PI, for its own
physical counterpart), and "hierarchies are... reached [by] chaining
different DTs, i.e., connecting the DI of a DT to a PI of a second DT."
Because "a hierarchy is likely to be composed of several low-level DTs
grouped into a higher-level DT, the higher-level DT can accept connections
from multiple lower-level DTs through its PI." Relationships are first-class
properties of the DT's core model, split into *vertical* (hierarchy) and
*horizontal* (e.g., machine-to-machine ordering) kinds, explicitly to
"enable the ability to compose two or more digitalised entities into one
higher level entity" [@martinelli_hierarchical_2024]. This is an explicit,
general DT-to-DT composition mechanism from a source with no connection to
DTDL or to DTaaS -- direct evidence against reading "only DTDL" as a fact
about the field rather than about one paper's six-platform comparison.

Separately, a paper whose title is literally "Composing Digital Twins for
Internet of Everything Applications" argues that "supporting composability
of DTs to enable complex services" should be a first-class architectural
requirement, distinguishes *intra-twin* communication (a DT with its own
physical counterpart) from *inter-twin* communication (DTs interacting with
each other in the virtual space), and proposes composing a DT from a
Virtualization Layer (digital replicas of physical objects) plus a shared
AI-driven Service Layer, with the eventual goal of Digital Twin Networks
(DTNs) "wherein DTs share their knowledge and experience"
[@amadeo_composing_2024]. This paper is architectural and illustrated with a
toy example, not a deployed system at scale, so it should be read as a
composability *vision* for multi-DT constellations rather than as evidence
that the scale problem itself has been solved.

Taken together, [@martinelli_hierarchical_2024] and [@amadeo_composing_2024]
show that DT-to-DT composition, both hierarchical and networked, is an
active concern independent of DTaaS and independent of DTDL specifically --
but neither paper studies the performance or consistency behavior of large
graphs of composed DTs at runtime, so "composition at scale" (in the sense
of *how many* composed DTs a mechanism can sustain, not just whether DT-DT
relationships can be expressed at all) remains genuinely open in this
corpus.

## Swappable composition backends in a deployed system

A construction-engineering platform, also self-described as "Digital-
Twins-as-a-Service" (an unrelated project from the DTaaS discussed above),
gives a concrete example of treating two of the compared platforms --
BaSyX and Ditto -- as interchangeable composition backends behind one
abstraction. Its Digital Twin Thread Manager "leverage[s] Eclipse Basyx...
as the communication middleware," but is "designed such that the
communication middleware can be replaced, e.g., with Eclipse Ditto, thanks
to the IfcCockpitAPI which abstracts away low-level implementation
details." Within this system, IoT sensors and actuators are "combined in an
AAS and the submodels contained in the AAS represent the individual
devices" [@zech_digital-twins-as--service_2024]. This is useful corroborating
evidence that BaSyX's and Ditto's composition mechanisms can be made
swappable in a real deployment, but the paper does not claim or demonstrate
semantic equivalence between the two backends -- only that both can serve
as the same architectural role (communication middleware) behind a shared
API, which is a narrower claim than "these two composition mechanisms are
interchangeable."

## Composition inside a single applied architecture -- citation withdrawn

The earlier version of this survey included a section here describing an
applied DT architecture for autonomous-mobile-robot runtime verification,
citing it under the key `noauthor_digital_nodate`. That citation can no
longer be used for that content. After the latest Zotero re-export, the
bib entry for `noauthor_digital_nodate` is a different item -- a Carnegie
Mellon University webpage snapshot with no relation to robots, runtime
verification, or composition. A stale parsed-text file left over from
before the re-export still contains the old robot paper's text, which is
why retrieval briefly appeared to still "find" it; the ledger's own
`status` for this citekey is `no_pdf`, confirming the current bib entry has
nothing to extract. Searching the new `bibliography.bib` directly (by
title, author names, and DOI) turned up no entry for the original robot
paper under any citekey. Per this project's citation rule, a claim may only
be attached to a citekey that currently resolves to the source that
actually supports it -- since `noauthor_digital_nodate` no longer resolves
to that paper, its claims have been removed from this draft rather than
re-cited or left dangling. If that paper is still wanted in this survey, it
needs to be re-added in Zotero (or otherwise recovered) and picked up by a
fresh `python -m src.sync` before it can be cited again.

## Comparison table

| Approach / paper | Citekey | Core composition idea | Stated limitations |
|---|---|---|---|
| DTaaS asset-based composition | `talasila_composable_2025` | DT = design (tuple of Data/Model/Function/Tool power sets) + configuration (`Ca`, `Ci`, `Ce`, `Cpt`); DTs can nest other DTs as constituents | Weak on explicit DT-to-DT relationships relative to DTDL; only guarantees technical, not semantic, compatibility of composed assets; `Ca`/`Cpt` are design-specific and resist generalization into a configuration standard [@talasila_composable_2025] |
| Asset Administration Shell (AAS) / Eclipse BaSyX | `noauthor_asset_nodate`; `jacoby_open-source_2023`; `schnicke_enabling_2020`; `ellwein_rethinking_2025`; `gleich_asset_2024` | Aggregation of Submodels (each built of SubmodelElements); "Composite I4.0 Components" | DT-DT relationships only implicit, via semantic identifiers/model references (per [@talasila_composable_2025]'s comparison); no primary source in this corpus discusses cross-submodel semantic-compatibility checking |
| Eclipse Ditto | [@talasila_composable_2025] (composition mechanism itself still second-hand; used, but not described, in [@zech_digital-twins-as--service_2024]) | Aggregation of Features | DT-DT relationships: N/A (per [@talasila_composable_2025]) |
| DTDL (Microsoft Azure) | [@talasila_composable_2025] (Interfaces/Components/Relationships description); `lehner_pattern_2023` (behavior-pattern composition, demonstrated on DTDL) | Composition of Interfaces via Components; explicit DT-DT `Relationships` field; reusable behavior-augmentation patterns | Requires a back-end to realize bidirectional sync (classified as Digital Model by default) |
| Eclipse Vorto | [@talasila_composable_2025] (secondary; no separate primary source found in this update) | Aggregation of Function Blocks | DT-DT relationships only implicit, via Model References |
| INTO-CPS co-simulation framework | [@talasila_composable_2025]; `frasheri_addressing_2023` | Aggregation of hierarchical FMU simulators; concrete 3-FMU composition with an automated out-of-sync detector between composed units and the PT | DT-DT relationships: N/A (per [@talasila_composable_2025]); the automated check covers temporal sync only, not units/semantics generally |
| Kamburjan et al.'s asset-model reconfiguration | `kamburjan_digital_2022`; `kamburjan_declarative_2024`; `kamburjan_greenhousedt_2024` | Composition changes over the DT's lifecycle, driven by declarative lifecycle-stage descriptions of the physical asset | Reconfiguration decisions still require modeler-authored stage/asset-model declarations; not compared against DTaaS's own configuration-validity mechanism |
| Hierarchical DT ecosystem | `martinelli_hierarchical_2024` | Explicit DT-DT composition: hierarchy formed by chaining one DT's Digital Interface to another's Physical Interface; vertical/horizontal relationships as first-class model properties | Demonstrated in a single industrial-manufacturing ecosystem; scale/performance of large composed graphs not studied |
| Composing DTs for IoE | `amadeo_composing_2024` | Composability as an explicit requirement; intra-twin vs. inter-twin communication; DTs composed of a Virtualization Layer + shared AI Service Layer; Digital Twin Networks (DTN) vision | Architectural vision plus a toy example only; not a deployed or scale-tested system |
| Digital-Twins-as-a-Service (construction) | `zech_digital-twins-as--service_2024` | BaSyX and Ditto used as interchangeable composition/communication backends behind one abstraction (IfcCockpitAPI); devices composed as AAS submodels | Single AECO-domain case study; backend swap shown at the middleware-role level only, not proven semantically equivalent |
| Applied DTaaS-aligned architecture for a mobile robot | *(citation withdrawn -- see note above)* | *(was: composes Data/Operations/State/Behaviors/Monitors around one robot via MQTT)* | Citekey `noauthor_digital_nodate` no longer resolves to this paper after the latest bib re-export; the source is not present under any citekey in the current library |

The Ditto and Vorto composition-mechanism cells still report what
[@talasila_composable_2025]'s own Table 2 says about those platforms --
their primary sources describing those mechanisms specifically are still
not present in this library, so nothing in those cells should be
attributed beyond what that one paper reports.

## Gap analysis

This update re-checked every gap the earlier draft flagged against the
larger, re-synced corpus. Two are now partially filled; one is essentially
unchanged; and one gap got worse, not better.

- **Formal/semantic verification of composed DTs -- partially filled.**
  The earlier draft said no paper in this library proposes automated
  checking of semantic compatibility across composed assets.
  [@frasheri_addressing_2023] is a genuine, narrow counterexample: an
  automated out-of-sync detector between composed FMUs and the PT, but it
  checks temporal synchronization specifically, not units or general
  semantic compatibility. [@hinchey_semantic_2025] gives the
  knowledge-graph angle (previously only a passing mention inside
  [@talasila_composable_2025]) a dedicated primary source, but it discusses
  semantic reflection as a technique, not a working cross-asset
  compatibility checker. So: still no general automated semantic-
  compatibility check across composed assets, but the corpus no longer
  supports the claim that *nothing at all* has been done on this front.
- **Standardized composition/configuration languages -- partially filled,
  unevenly.** AAS is no longer only visible second-hand: its own
  specification and several independent studies of it are now in the
  library. DTDL now has a second anchor beyond
  [@talasila_composable_2025]'s report, via [@lehner_pattern_2023]'s
  behavior-pattern catalog. Eclipse Ditto and Eclipse Vorto remain
  second-hand for their own composition mechanisms specifically -- no
  primary source describing either mechanism directly was found, even
  though Ditto now appears as a component of a deployed system
  ([@zech_digital-twins-as--service_2024]).
- **Composition at scale / multi-DT constellations -- partially filled.**
  [@martinelli_hierarchical_2024] and [@amadeo_composing_2024] both treat
  DT-to-DT composition as a first-class concern, independently of DTaaS and
  of each other, and the former gives an explicit (not merely DTDL-only)
  hierarchy mechanism. Neither, however, studies the runtime performance or
  consistency behavior of *large* graphs of composed DTs -- so the "at
  scale" half of this gap (as opposed to the "is DT-to-DT composition
  studied at all outside DTaaS" half) is still open.
- **Runtime-verification-driven composition patterns generalized beyond a
  single robot -- gap widened, not filled.** The earlier draft's only
  supporting citation for this theme, `noauthor_digital_nodate`, no longer
  resolves to the paper it used to cite (see the withdrawn-citation section
  above), and that paper does not appear under any other citekey in the
  re-exported library. This sub-theme currently has **no** supporting
  citation in the corpus at all, pending the source being re-added to
  Zotero and re-synced.

Two new sub-themes surfaced by this update's retrieval, not present in the
earlier draft at all, are worth flagging as their own (currently thin)
areas rather than folding into the above:

- **Composition-driven reconfiguration across the DT lifecycle.** Covered
  above by [@kamburjan_digital_2022], [@kamburjan_declarative_2024], and
  [@kamburjan_greenhousedt_2024] -- but all three are from one research
  group's own toolchain, and none of them engage with DTaaS's
  configuration-validity mechanism or any of the platforms in the
  comparison table. Whether declarative-lifecycle-driven reconfiguration is
  complementary to, or in tension with, asset-based configuration validity
  is an open question this corpus does not answer.
- **Duplicate library entries.** This update's retrieval surfaced several
  same-title items under two different citekeys in the re-exported
  `bibliography.bib` (e.g. the "Survey on open-source digital twin
  frameworks" paper as both `gil_survey_2024` and `noauthor_survey_nodate`;
  several `kamburjan_*`, `zech_*`, and `talasila_realising_2024` entries each
  appear twice). This draft consistently cites one citekey per work, but the
  duplication itself is a Zotero-library housekeeping matter worth the
  user's attention independent of this survey.

If a broader account of DT composition is needed -- e.g., dedicated primary
sources for Eclipse Ditto's and Eclipse Vorto's own composition mechanisms,
a general (not just temporal) automated semantic-compatibility checker for
composed models, or a runtime-verification composition study generalized
beyond one robot -- those sources are still not in this project's synced
library and would need to be added to Zotero and picked up by
`python -m src.sync` before they could be cited here.

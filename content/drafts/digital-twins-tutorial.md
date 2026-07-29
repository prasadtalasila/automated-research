# Digital Twins: A Beginner's Tutorial

## 1. Motivation

Picture a wind turbine floating far out at sea. You can't just walk up and
peer inside it every time something feels off -- a maintenance visit means
a boat, a crew, and a very expensive afternoon. So the National Renewable
Energy Laboratory and Stiesdal Offshore built something cleverer instead:
a live computer model wired to the real turbine's sensors, checked
against a full-scale prototype, that estimates loads and stresses the
physical sensors alone can't see [@branlard_digital_2024]. That's a
*digital twin* -- and once you notice the pattern, you start seeing it
everywhere. At the University of Oslo, the BedreFlyt project runs a
digital twin of a hospital ward to simulate patient flow, so planners can
spot a bed shortage days before it happens instead of scrambling on the
day [@sieve_bedreflyt_2025]. In Extremadura, Spain, engineers built one
for a high-speed railway bridge, updating it live during load tests so
raw sensor readings turn into a structural model that corrects itself as
the data comes in [@chacon_digital_2024]. Different industries, same
trick: take a physical thing, give it a virtual counterpart wired to live
data, and let the two talk to each other.

## 2. What Is a Digital Twin, Really?

Here's a tempting but wrong definition: "a digital twin is a 3D model of
something real." A 3D model is just a picture -- it doesn't know or care
what the real object is doing right now. Here's a definition closer to
the truth, from the paper that coined the term: a digital twin is a
virtual object connected to its physical counterpart by a continuous flow
of data in *both* directions -- sensor readings flow in from the physical
side, and decisions computed on the virtual side flow back out to
actually change something in the real world [@grieves_digital_2017].

That two-way flow is the whole trick, and there's a quick way to test for
it: if data only flows one way -- physical object to model, full stop --
what you have is merely a *digital shadow*. Only when the loop closes,
and the model's output changes the physical object's behavior
automatically, do you get a real digital twin [@kritzinger_digital_2018].

Two examples make the loop concrete. One incubator case study, built
specifically to teach this idea, walks through a controller model that
reads a real incubator's live temperature sensor, computes a new heater
setting, and pushes that setting back to the real heater, no human in the
loop [@gomes_digital_2025]. The offshore wind turbine from Section 1
closes the same loop at industrial scale: sensor data streams in
continuously, and the twin's estimates feed back into the turbine's own
control system [@branlard_digital_2024]. Different scales, same
handshake.

## 3. The Building Blocks: Data, Model, and Algorithms

Zoom in on that loop from Section 2 and you'll find it isn't one thing --
it's three things, stitched together. Every digital twin worth the name
needs to move *data*, run it through a *model*, and let some *algorithm*
decide what to do with the result. That same three-part shape holds
together whether you're twinning a wind turbine or a hospital ward.

### Data

Data is the raw material: the temperature readings, vibration spectra,
and bed-occupancy counts flowing in from the physical side. Most of it
needs a little cleanup before anything useful can be done with it --
converting Fahrenheit to Celsius, filtering out sensor noise, batching a
firehose of readings into something a model can digest a chunk at a time.
None of that cleanup logic is specific to any one digital twin: a
unit-conversion routine works the same whether it's cleaning turbine data
or hospital data. So in practice these small data-handling routines tend
to get written once and reused across many twins, sometimes via
off-the-shelf tool chains built for exactly this kind of heavy lifting
[@talasila_realising_2024].

### Model and Algorithms

The model is where the twin actually knows something about the physical
world -- a set of equations describing how a turbine blade flexes under
load, say, or how patients typically move between hospital wards. But a
model by itself doesn't *do* anything; it just sits there describing
relationships. Something has to actually run the numbers, and that
something is what the literature -- depending on which paper you're
reading -- calls an *algorithm*, a *tool*, or a *method*. All three names
point at the same idea: a software implementation of a domain-specific
procedure that takes a model and some data and evaluates it, the way a
finite-element solver evaluates a structural model, or an optimizer
evaluates a scheduling model [@talasila_realising_2024].

Put the three pieces together and you get the whole loop from Section 2
back again: data flows in, an algorithm evaluates it against a model, and
the result flows back out to the physical twin. Change any one piece --
swap in a better model, plug in a faster algorithm, or feed it richer
data -- and the twin improves without touching the other two pieces at
all. That's the entire point of treating data, models, and algorithms as
separate, swappable building blocks instead of one big tangled program.

## 4. Why Standards Matter: Industry 4.0, AAS, BaSyX, and Friends

Here's a problem you'll hit the moment two digital twins, built by two
different companies, need to talk to each other: whose data format wins?
Manufacturing ran into this early, and the answer -- unsurprisingly --
was to agree on a shared one. Germany's Plattform Industrie 4.0 defines
the *Asset Administration Shell* (AAS), a standardized digital "wrapper"
that describes any physical asset -- a machine, a sensor, a whole product
line -- in a way any AAS-aware software can read [@noauthor_asset_nodate].

The AAS itself is just a paper specification, though; somebody still has
to write the software. That's where Eclipse BaSyX comes in: an
open-source implementation with a registry, a client library, and
visualization tools already built, so engineers don't have to write that
plumbing themselves [@jacoby_open-source_2023].

AAS isn't the only standard you'll run into, and each of the others
solves a slightly different piece of the puzzle. Microsoft's cloud
platform describes twins in a format called DTDL (Digital Twin
Definition Language) -- and because factories were already full of
machines speaking a much older industrial protocol called OPC UA,
somebody had to work out how to translate between the two. A proposed
mapping does exactly that, letting a DTDL-described twin talk to
OPC-UA-speaking hardware without a human translating by hand
[@cavalieri_proposal_2023]. AutomationML tackles an earlier problem in
the same pipeline: before you can even describe an asset for a digital
twin, you first have to get the CAD drawings, wiring diagrams, and PLC
logic that different engineering tools produced out of their proprietary
formats and into something the next tool in the chain can read -- which
is exactly what this open standard for engineering data exchange is built
to do [@noauthor_automationml_2021]. And one level up from all of this,
SysML v2 -- the latest version of the Object Management Group's Systems
Modeling Language -- gives engineers a standard, tool-independent
notation for describing a whole system's structure and behavior in the
first place, before any of the twin-specific plumbing even gets built
[@noauthor_about_nodate].

| Standard | What it standardizes | Who's behind it |
|---|---|---|
| AAS (Asset Administration Shell) | A uniform digital "wrapper" describing any physical asset, so different tools can query the same asset the same way [@noauthor_asset_nodate] | Plattform Industrie 4.0 |
| Eclipse BaSyX | An open-source AAS implementation -- registry, client library, visualization -- so nobody rewrites the AAS plumbing from scratch [@jacoby_open-source_2023] | Eclipse Foundation |
| DTDL <-> OPC UA mapping | A translation between a cloud twin-description format (DTDL) and the older industrial protocol (OPC UA) many factory machines already speak [@cavalieri_proposal_2023] | Proposed academic mapping, bridging Microsoft's and the OPC Foundation's ecosystems |
| AutomationML | Exchange of engineering data -- CAD, wiring, PLC logic -- between the different tools used to design a physical asset, before it's ever twinned [@noauthor_automationml_2021] | AutomationML e.V. consortium |
| SysML v2 | A general, tool-independent notation for describing a system's structure and behavior, independent of any twin-specific format [@noauthor_about_nodate] | Object Management Group (OMG) |

Notice the pattern: none of these standards do the same job. AAS and
BaSyX describe *an asset* once it exists; AutomationML gets the *design*
of that asset out of one engineer's tool and into another's; SysML v2
describes the *system* the asset belongs to, at an even more general
level; and the DTDL/OPC UA mapping solves a narrower, very practical
problem -- getting two specific pieces of software, from two different
vendors, to actually understand each other. A real digital twin project
usually ends up leaning on more than one of these at once, the same way a
web application leans on both HTTP and a database format without anyone
treating that as strange.

# The workflow, drawn eleven ways

Every diagram here describes the same pipeline. They come in three groups:

- **[The ladder](#the-ladder)** -- six views of the whole workflow, ordered
  by how much you already know, from one that assumes nothing to one that
  assumes you are about to change the worker pool.
- **[By genre](#by-genre)** -- three views of how the *writing* skills
  differ in what they ask of the pipeline. The five genre skills are not
  variations on one template; they disagree about how much of it is worth
  running.
- **[Appendix](#appendix)** -- the same workflow in time order, and the
  ledger's state machine for one citekey.

Read the one that matches your question and ignore the rest.

One property holds in all eleven: **a genre skill loops on the citation
gate until it exits 0, and shows you nothing before that.** Every SKILL.md
in `.claude/skills/` carries the same sentence -- *"Fix and re-run until
`OK`. Never present a draft that hasn't passed."* A gate failure is
normally something you never see.

The sources are the fenced `mermaid` blocks below. GitHub renders them
inline, and they are the versions to edit -- kept here rather than as
checked-in images so a change to the pipeline and a change to its diagram
land in the same diff.

---

## The ladder

| # | Diagram | Written for | Answers |
|---|---|---|---|
| 1 | [One glance](#1-one-glance) | Someone who has never heard of this | what are the steps, and who does each one? |
| 2 | [Your first run](#2-your-first-run) | Someone installing it today | what do I type, in what order, and what does each step tell me? |
| 3 | [The full workflow](#3-the-full-workflow) | Someone reading the source | what actually runs, what does it write, and where does the module I'm looking at fit? |
| 4 | [Everything on disk](#4-everything-on-disk) | Someone deciding what to back up, or debugging a wrong file | what are all these files, who wrote them, and what is safe to delete? |
| 5 | [Gates and exit codes](#5-gates-and-exit-codes) | Someone whose run just failed, or who is scripting it | why did this exit 1, and what is safe to retry automatically? |
| 6 | [Inside one parse](#6-inside-one-parse) | Someone tuning `[parser].workers`, or changing the pool | what does a worker pool actually do here, and what happens when part of it dies? |

### 1. One glance

**Written for:** Someone who has never heard of this.
**Answers:** what are the steps, and who does each one?

Deliberately the least detailed diagram here. Two properties do all the
work: phase 0 is the only entrance -- citekeys come from your BibTeX
export and nowhere else -- and phase 3 is the only exit, with no arrow
around it. This is the version in [the README](../README.md#how-it-works).

The `DISCARD DRAFT` box loops back to **drafting**, not to you. A failing
gate is normally invisible: the skill rewrites the claim and runs the gate
again. You only get involved in the rarer case where the paper genuinely
isn't in the corpus yet.

```mermaid
flowchart LR

  P0["<b>0 · CURATE</b><br/><i>you, in Zotero</i><br/><br/>Add papers, export<br/>BibTeX + Export Files<br/><br/><b>papers/bibliography.bib</b><br/><small>nothing else may invent a citekey</small>"]

  P1["<b>1 · SYNC</b><br/><i>job 1 — deterministic, no LLM</i><br/><br/><code>python -m src.sync</code><br/>read bib → update ledger<br/>→ extract PDF text<br/><br/><b>content/ledger.sqlite</b><br/><b>content/parsed/*.txt</b><br/><small>idempotent · re-runs cost almost nothing</small>"]

  P2["<b>2 · DRAFT</b><br/><i>job 2 — generative, you review</i><br/><br/>Ask a genre skill:<br/><i>“write a survey section on …”</i><br/>it retrieves only from<br/>the parsed corpus<br/><br/><b>content/drafts/&lt;slug&gt;.md</b>"]

  P3{{"<b>3 · VERIFY</b><br/><i>machine-enforced</i><br/><br/><code>src.citation_gate</code><br/>Is every citekey<br/>in the ledger?"}}

  P4["<b>4 · PUBLISH</b><br/><i>stdlib + Pandoc / TeX Live</i><br/><br/><code>src.references</code><br/>IEEE list from exactly<br/>the citekeys cited<br/><br/><code>render_output --format pdf</code><br/><b>content/rendered/&lt;slug&gt;.pdf</b>"]

  FIX["<b>DISCARD DRAFT</b><br/><br/>The skill throws the bad claim away<br/>and drafts again — you never see this.<br/><br/><small>“Fix and re-run until <code>OK</code>.”<br/>A FAIL is treated like a failing test,<br/>not a lint warning.</small>"]

  P0 ==> P1 ==> P2 ==> P3
  P3 == "PASS · exit 0" ==> P4
  P3 -- "FAIL · exit 1" --> FIX
  FIX == "re-draft · <b>loop until it passes</b>" ==> P2
  FIX -. "or: the paper really is missing —<br/>add it in Zotero and re-sync" .-> P0

  classDef you fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef det fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:3px,color:#450a0a
  classDef out fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a

  class P0 you
  class P1 det
  class P2 gen
  class P3 gate
  class P4 out
  class FIX bad
```


### 2. Your first run

**Written for:** Someone installing it today.
**Answers:** what do I type, in what order, and what does each step tell me?

The same path as [the Quickstart](../README.md#quickstart), drawn with the
two checkpoints that actually catch people: the **Export Files** tick box,
which silently produces a bibliography with no PDFs attached to it, and
the first `python3 -m src.ledger` after a sync, which is where you find
out whether anything became citable. Exit codes are on the diagram
because at this stage they are the only feedback you have.

```mermaid
flowchart TB

  A["<b>1 · Export from Zotero</b><br/><code>papers/bibliography.bib</code><br/><small>Format: BibTeX &nbsp;·&nbsp; tick <b>Export Files</b></small>"]
  A1{{"Did you tick<br/><b>Export Files</b>?"}}
  ATRAP["<b>The trap.</b> Without it you get metadata and no PDFs.<br/>Every entry then reports <i>no PDF attachment</i> — not an error,<br/>just an empty corpus. Same if you rename the companion folder:<br/><code>bib_reader</code> resolves <code>file</code> fields relative to the .bib.<br/><small>docs/ZOTERO.md</small>"]

  B["<b>2 · Make a config</b><br/><code>cp config.toml.example config.toml</code><br/><small>every key is optional — but the file must exist,<br/>or <code>src.config</code> refuses to import</small>"]

  C["<b>3 · Install</b><br/><code>pipx install poetry</code><br/><code>bash scripts/install_full_pipeline.sh all</code><br/><small><b>all</b> = OS packages (pdftotext, TeX Live, Pandoc) + Python.<br/>No stage argument = Python only.</small>"]

  D["<b>4 · Sync the corpus</b><br/><code>source .venv-full/bin/activate</code><br/><code>python -m src.sync</code><br/><small>needs the venv — this is the one command that does</small>"]
  D1{{"exit code?"}}
  DFAIL["<b>1</b> — something didn't parse.<br/><small>The summary names each one.<br/>Fix or remove it; re-running is cheap.</small>"]
  DBUSY["<b>2</b> — another run holds the lock.<br/><small>Nothing was lost. Try again later.</small>"]

  E["<b>5 · Look at what it found</b><br/><code>python3 -m src.ledger</code><br/><small>read-only, takes no lock, needs no venv —<br/>safe to run <i>while</i> a sync is going</small>"]
  E1{{"Rows with status<br/><code>parsed</code>?"}}
  EEMPTY["Nothing is citable yet.<br/><small>Almost always the Export Files trap above.<br/><code>--status no_pdf</code> tells you which entries and why.</small>"]

  F["<b>6 · Ask for a draft</b><br/><i>“write a survey section on digital twin composability”</i><br/><small>in Claude Code. The matching skill in <code>.claude/skills/</code><br/>picks it up and runs its own gate → references → render chain,<br/><b>looping on the gate until it exits 0</b> before showing you anything.</small>"]

  G["<b>7 · Or drive that chain by hand</b><br/><code>python3 -m src.citation_gate &lt;draft&gt;</code> &nbsp;<i>← fix and repeat until OK</i><br/><code>python3 -m src.references &lt;draft&gt;</code><br/><code>python3 -m src.heavy.render_output &lt;draft&gt; --format pdf</code><br/><small>bare <code>python3</code> — none of these need the venv</small>"]

  DONE(["<b>content/rendered/&lt;slug&gt;.pdf</b>"])

  A --> A1
  A1 -- no --> ATRAP
  ATRAP -. "re-export" .-> A
  A1 -- yes --> B --> C --> D --> D1
  D1 -- "1" --> DFAIL
  D1 -- "2" --> DBUSY
  DBUSY -. "re-run" .-> D
  DFAIL -. "re-run" .-> D
  D1 -- "<b>0</b> — corpus in sync" --> E --> E1
  E1 -- no --> EEMPTY
  EEMPTY -. "fix the export" .-> A
  E1 -- yes --> F --> DONE
  F -. "same chain, manually" .-> G
  G --> DONE

  classDef step fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef check fill:#fefce8,stroke:#a16207,stroke-width:1.5px,color:#422006
  classDef trap fill:#fef2f2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
  classDef done fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#052e16

  class A,B,C,D,E,F,G step
  class A1,D1,E1 check
  class ATRAP,EEMPTY,DFAIL,DBUSY trap
  class DONE done
```


### 3. The full workflow

**Written for:** Someone reading the source.
**Answers:** what actually runs, what does it write, and where does the module I'm looking at fit?

The reference diagram, and the one in
[the README's Architecture section](../README.md#architecture). Everything
in `src/` appears here exactly once.

Three things it is drawn to make unmissable. The thick edge from
`content/ledger.sqlite` to the gate is the entire safety argument -- the
gate consults the ledger and nothing else. The gate sits on the only path
from a draft to `content/rendered/`. And the `FAIL` edge does not leave
the system: it runs back into the skill, which re-drafts and re-runs the
gate until it exits 0.

Note that the heavy pipeline's `docling` stage reads **the PDF itself**,
not `content/parsed/`. It is a second, independent extraction of the same
source, not a refinement of the first one -- which is why it can produce
figures and layout-aware passages that `pdftotext` cannot.

Dotted edges are optional or conditional: the heavy pipeline is opt-in,
and each consumer checks the stack exists before using it and degrades to
the lightweight default when it doesn't.

```mermaid
flowchart TB

  %% ─────────────── 0 · CURATE ───────────────
  subgraph S0["<b>0 · CURATE</b> — you, in Zotero. The pipeline never fetches a paper."]
    direction TB
    ZOT[("Zotero library")]
    BIB["<b>papers/bibliography.bib</b><br/><i>the source of truth for citekeys</i>"]
    ATT[/"<b>papers/bibliography/files/&lt;id&gt;/*.pdf</b><br/><small>the companion folder Zotero writes beside the .bib —<br/>never rename it, the <code>file</code> fields are relative paths</small>"/]
    RAW[/"papers/pdfs/*.pdf<br/><small>optional · raw PDFs you drop in by hand ·<br/><code>doc:&lt;stem&gt;</code>, never citable</small>"/]
    ZOT -- "Export · BibTeX + Export Files" --> BIB
    ZOT --> ATT
  end

  %% ─────────────── 1 · SYNC ───────────────
  subgraph S1["<b>1 · SYNC</b> — job 1 · deterministic, no LLM, safe to run unattended · <code>python -m src.sync</code> · holds the run lock"]
    direction TB
    BR["<b>src/bib_reader.py</b><br/><small>the only module that reads the .bib</small>"]
    LED[("<b>content/ledger.sqlite</b><br/><small>one row per citekey: status, bib_fields,<br/>PDF fingerprint — re-parse only what moved</small>")]
    PT["<b>src/pdf_text.py</b> — extract text<br/><small>backend <code>pdftotext</code> · or <code>docling</code><br/>serial by default; opt-in worker pool</small>"]
    TXT[/"<b>content/parsed/&lt;citekey&gt;.txt</b>"/]
    ADV["<i>reported, never fatal:</i><br/>near-duplicates · parse-quality warning · stale citekeys<br/><small>deletion needs <code>--remove-stale</code></small>"]
    BR --> LED --> PT --> TXT --> ADV
  end

  %% ─────────────── 2 · RETRIEVE ───────────────
  subgraph S2["<b>2 · RETRIEVE</b> — the only evidence a writer is given · <b>one ranker or the other, never both</b>"]
    direction LR
    XOR{{"which ranker?<br/><small>the genre skill picks</small>"}}
    BM25["<b>src/retrieval.py</b> · BM25<br/><small>stdlib keyword overlap, cached term-frequency index.<br/><b>the default every skill starts from</b></small>"]
    EMB["<b>src/heavy/embed_index.py</b> · semantic<br/><small>same <code>search(q, k)</code> signature, so it is a drop-in.<br/>Named as the alternative by <code>survey-writer</code><br/>and <code>deep-research</code> — and only by those two.</small>"]
    PASS["<b>src/passages.py</b> · evidence ladder<br/><small>docling sidecar → page → pdftotext</small>"]
    XOR -- "default" --> BM25
    XOR -. "only if content/chroma/ was built" .-> EMB
    BM25 --> PASS
    EMB --> PASS
  end

  %% ─────────────── 3 · DRAFT ───────────────
  subgraph S3["<b>3 · DRAFT</b> — job 2 · generative, on demand, reviewed by you"]
    direction TB
    SKILLS["<b>.claude/skills/</b> — five genre skills<br/>survey-writer · thesis-chapter-writer · textbook-chapter-writer<br/>tutorial-writer · deep-research"]
    DRAFT[/"<b>content/drafts/&lt;slug&gt;.md | .tex</b>"/]
    SKILLS --> DRAFT
  end

  %% ─────────────── 4 · GATE ───────────────
  GATE{{"<b>THE CITATION GATE</b> · <code>python3 -m src.citation_gate</code><br/>Is every citekey in this draft present in the ledger?<br/><small>run twice: by the PostToolUse hook on every write under content/drafts/,<br/>and by the skill itself before it shows you anything</small>"}}
  BLOCK["<b>REFUSED</b> · exit 1<br/><small>the write is blocked and the chain stops</small>"]
  ITER["<b>the skill fixes it and re-runs — itself</b><br/><small>“Fix and re-run until <code>OK</code>. Never present a draft that hasn't<br/>passed.” — all five SKILL.md files say this, in those words.<br/>It swaps the bad key for one retrieval actually returned, or drops<br/>the claim. You are shown nothing until the gate is green.</small>"]

  %% ─────────────── 5 · PUBLISH ───────────────
  subgraph S5["<b>5 · PUBLISH</b> — stdlib only, no venv needed"]
    direction TB
    REFS["<b>python3 -m src.references</b><br/><small>IEEE ## References, numbered by first appearance,<br/>built only from citekeys the draft actually cites.<br/>Skipped for thesis .tex fragments, where the<br/>surrounding LaTeX owns the bibliography.</small>"]
    REND["<b>python3 -m src.heavy.render_output</b><br/><small>pandoc --citeproc + assets/csl/ieee.csl</small>"]
    OUT[/"<b>content/rendered/&lt;slug&gt;.pdf | .tex | .docx | .md</b>"/]
    REFS --> REND --> OUT
  end

  %% ─────────────── HEAVY (side branch) ───────────────
  subgraph SH["<b>OPTIONAL · HEAVY PIPELINE</b><br/><code>scripts/full_pipeline.py --stages …</code> · same run lock"]
    direction TB
    H1["<b>docling</b> — <i>reads the PDF itself, not content/parsed/</i><br/><small><b>content/docling/&lt;doc&gt;.md</b> — layout-aware text<br/><b>&lt;doc&gt;.passages.json</b> — the quotable-passage sidecar<br/><b>&lt;doc&gt;_artifacts/</b> — figure bitmaps, written by Docling<br/><b>&lt;doc&gt;.figures.json</b> — page, caption, cite string per figure<br/>the last two only when <code>[heavy].docling_images</code> is on</small>"]
    H2["<b>embed</b><br/><small>content/chroma/ — drop-in search(q,k)</small>"]
    H3["<b>bertopic</b><br/><small>content/topics.json</small>"]
    H1 --> H2 --> H3
  end

  %% ─────────────── AIDS (side branch) ───────────────
  subgraph SA["<b>REVIEW AIDS</b> — you run these; none of them is a gate"]
    direction TB
    A1["<b>src.citation_provenance</b><br/><small>what in the source supports this claim?</small>"]
    A2["<b>scripts/verbatim_check.py</b><br/><small>verbatim overlap · locate a phrase by page</small>"]
    A3["<b>src.citation_coverage</b><br/><small>retrieval surfaced it — did the draft cite it?</small>"]
  end

  %% ─────────────── SPINE ───────────────
  BIB --> BR
  ATT --> PT
  TXT --> XOR
  PASS --> SKILLS
  DRAFT --> GATE
  GATE -- "FAIL" --> BLOCK
  BLOCK --> ITER
  ITER == "re-draft · <b>loop until it passes</b>" ==> SKILLS
  GATE == "PASS · exit 0" ==> REFS
  LED == "the ledger is the<br/>only authority the<br/>gate consults" ==> GATE
  LED --> REFS
  ATT -. "the PDF, direct" .-> H1
  RAW -. "src/heavy/corpus.py" .-> H1
  H1 -.-> PASS
  H2 -.-> EMB
  DRAFT --> SA

  %% ─────────────── STYLE ───────────────
  classDef src fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef store fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:2.5px,color:#450a0a
  classDef bad fill:#dc2626,stroke:#7f1d1d,color:#ffffff
  classDef loop fill:#fefce8,stroke:#a16207,stroke-width:2px,color:#422006
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef aid fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 3,color:#0f172a
  classDef heavy fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764

  class BR,PT,BM25,PASS,REFS,REND src
  class ZOT,BIB,ATT,RAW,LED,TXT,DRAFT,OUT store
  class GATE,XOR gate
  class BLOCK bad
  class ITER loop
  class SKILLS gen
  class A1,A2,A3,ADV aid
  class H1,H2,H3,EMB heavy
```


### 4. Everything on disk

**Written for:** Someone deciding what to back up, or debugging a wrong file.
**Answers:** what are all these files, who wrote them, and what is safe to delete?

Same pipeline, but the files are the nodes and the modules are the edge
labels -- the inverse of the full workflow diagram.

The split that matters: everything under `content/` is disposable. Delete
the directory and one `sync` plus one heavy run rebuilds all of it.
Nothing under `papers/` is disposable, and neither is `config.toml`; both
are gitignored and per-host, so they are also the only things a backup
needs to contain.

With `[heavy].docling_images` on, `docling` also writes each document's
figure bitmaps into `<doc>_artifacts/` and an index of them in
`<doc>.figures.json` -- page, caption, and the string to cite each figure
by. Those are a reading aid for checking a draft against its sources.
Having a paper in your library grants no right to reproduce its figures;
see DEVELOPER.md's "Figures and copyright".

```mermaid
flowchart TB

  subgraph IN["<b>YOURS</b> — hand-curated, gitignored, per host"]
    direction LR
    BIB[/"papers/bibliography.bib"/]
    PDF[/"<b>papers/bibliography/files/&lt;id&gt;/*.pdf</b><br/><small>Zotero's companion folder, written beside the .bib<br/>by <b>Export Files</b> — renaming it breaks every<br/><code>file</code> field silently</small>"/]
    RAW[/"papers/pdfs/*.pdf<br/><small>+ manifest.json · never citable</small>"/]
    CFG[/"config.toml"/]
  end

  subgraph GEN["<b>GENERATED</b> — <code>content/</code>, all of it disposable: delete it and re-run"]
    direction TB

    subgraph L1["written by <code>src.sync</code> — job 1"]
      direction LR
      LED[("content/ledger.sqlite")]
      TXT[/"content/parsed/&lt;citekey&gt;.txt"/]
    end

    subgraph L2["written by the heavy pipeline — opt-in"]
      direction LR
      DOC[/"<b>content/docling/&lt;doc&gt;.md</b><br/>+ &lt;doc&gt;.passages.json<br/><b>+ &lt;doc&gt;_artifacts/*.png</b> — figure bitmaps<br/><b>+ &lt;doc&gt;.figures.json</b> — page, caption, cite string<br/><small>the last two only when <code>[heavy].docling_images</code><br/>is on; Docling reads the <b>PDF</b>, never content/parsed/</small>"/]
      CHR[("content/chroma/")]
      TOP[/"content/topics.json"/]
    end

    subgraph L3["caches — rebuilt on demand, safe to delete, cost only time"]
      direction LR
      RIX[/"content/retrieval_index.json"/]
      DCA[/"content/docling_cache.json"/]
      TCA[/"content/topic_embed_cache.json"/]
    end

    subgraph L4["written by you, through a skill"]
      direction LR
      DRF[/"content/drafts/&lt;slug&gt;.md | .tex"/]
      REN[/"content/rendered/&lt;slug&gt;.pdf | .tex | .docx | .md"/]
      PRV[/"content/provenance/&lt;slug&gt;.provenance.md"/]
    end

    LCK[/"content/pipeline.lock.db<br/><small>held by whichever writer is running</small>"/]
  end

  CFG -. "read once at import,<br/>env vars override" .-> GEN

  BIB -- "src/bib_reader.py" --> LED
  PDF -- "src/pdf_text.py" --> TXT
  LED -- "which PDFs need a parse" --> TXT
  RAW -- "src/heavy/corpus.py<br/><small>doc:&lt;stem&gt;, never a citekey</small>" --> DOC
  TXT -- "src/heavy/embed_index.py" --> CHR
  PDF -- "src/heavy/docling_parse.py" --> DOC
  DOC --> CHR
  TXT -- "src/heavy/topic_model.py<br/><small>whole-doc embeddings — its own cache,<br/>not the Chroma collection</small>" --> TOP

  TXT -- "src/retrieval.py" --> RIX

  RIX -- "<b>src/retrieval.py</b> · BM25 hits<br/><small>the default path</small>" --> DRF
  CHR -. "<b>src/heavy/embed_index.py</b> · semantic hits<br/><small><b>either this or BM25 — never both.</b> Only<br/>survey-writer and deep-research name it.</small>" .-> DRF
  DOC -- "src/passages.py<br/>quotable passages" --> DRF
  LED -- "src/references.py<br/>bib_fields → IEEE entries" --> DRF
  DRF == "<b>src.citation_gate</b> — FAIL rewrites the draft in place,<br/>and the skill re-runs it until it exits 0" ==> DRF
  DRF -- "src.heavy.render_output<br/><small>only after the gate passes</small>" --> REN
  DRF -- "src.citation_provenance" --> PRV

  classDef mine fill:#fff7ed,stroke:#c2410c,color:#431407
  classDef job1 fill:#eef2ff,stroke:#4f46e5,color:#1e1b4b
  classDef heavy fill:#faf5ff,stroke:#9333ea,color:#3b0764
  classDef cache fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 3,color:#0f172a
  classDef draft fill:#f0fdf4,stroke:#16a34a,color:#052e16
  classDef lock fill:#fefce8,stroke:#a16207,color:#422006

  class BIB,PDF,RAW,CFG mine
  class LED,TXT job1
  class DOC,CHR,TOP heavy
  class RIX,DCA,TCA cache
  class DRF,REN,PRV draft
  class LCK lock
```


### 5. Gates and exit codes

**Written for:** Someone whose run just failed, or who is scripting it.
**Answers:** why did this exit 1, and what is safe to retry automatically?

Exit codes are the API for unattended callers: `0` corpus in sync, `1`
corpus not in sync and a human is needed, `2` cycle skipped and no work
was lost.

The distinction worth reading twice is in the failure branch. A document
the backend genuinely cannot read is **never retried** -- re-reading it
every run would spend the same minutes to reach the same answer -- but it
**never goes quiet either**, failing the run until someone deals with it.
A failure caused by the *run* rather than the *document* -- a dead worker,
a timeout, a CUDA OOM -- retries itself next time without being asked.

The gate's `exit 1` is the one failure on this diagram that usually
reaches nobody, because the skill loops on it itself.

```mermaid
flowchart TB

  START(["<code>python -m src.sync</code>"]) --> Q2

  Q2{"<b>config.toml present?</b><br/><small>read once at import,<br/>before anything else happens</small>"}
  Q2 -- no --> EC["<b>refuses to import</b><br/><small>cp config.toml.example config.toml</small>"]
  Q2 -- yes --> Q1

  Q1{"<b>run lock free?</b><br/><small>content/pipeline.lock.db</small>"}
  Q1 -- "no · another writer holds it" --> E2["<b>exit 2</b><br/>cycle skipped, no work lost<br/><small>readers were never blocked</small>"]
  Q1 -- yes --> Q3

  Q3{"<b>bibliography.bib readable?</b>"}
  Q3 -- no --> EF["<b>FileNotFoundError</b><br/><small>export from Zotero first</small>"]
  Q3 -- yes --> LOOP

  LOOP["<b>for each citekey in the bib</b><br/><small>upsert the ledger row</small>"] --> Q4

  Q4{"<b>has a resolvable PDF?</b>"}
  Q4 -- no --> SN["status <code>no_pdf</code><br/><small>reported with a reason;<br/>the run still succeeds</small>"]
  Q4 -- yes --> Q5

  Q5{"<b>bytes changed since<br/>the last parse?</b><br/><small>size+mtime, then sha256</small>"}
  Q5 -- no --> SK["<b>skipped</b><br/><small>this is why a re-run costs<br/>close to nothing</small>"]
  Q5 -- yes --> PARSE["<b>parse the PDF</b><br/><small>pdftotext or docling ·<br/>serial, or a clamped worker pool</small>"]

  PARSE --> Q6
  Q6{"<b>parse result</b>"}
  Q6 -- "complete" --> OK["status <code>parsed</code><br/><small>content/parsed/&lt;citekey&gt;.txt written</small>"]
  Q6 -- "partial success" --> PF
  Q6 -- "backend can't read it" --> PF["status <code>parse_failed</code> · <i>deterministic</i><br/><small>never retried — same minutes, same answer —<br/>but fails every run until fixed</small>"]
  Q6 -- "worker died · timeout · CUDA OOM" --> PT2["status <code>parse_failed</code> · <i>transient</i><br/><small>retried automatically next run</small>"]

  OK --> AGG
  SN --> AGG
  SK --> AGG
  PF --> AGG
  PT2 --> AGG

  AGG{"<b>anything unresolved?</b>"} -- no --> E0["<b>exit 0</b><br/>corpus in sync"]
  AGG -- yes --> E1["<b>exit 1</b><br/>corpus not in sync,<br/>human attention needed"]

  E0 --> GATEIN(["a skill drafts against this corpus"])
  GATEIN --> G{"<b>src.citation_gate</b><br/>every citekey resolvable?"}
  G -- "yes · <b>exit 0</b>" --> GOOD["references → render<br/><small>the only path to a rendered draft</small>"]
  G -- "no · <b>exit 1</b>" --> GBAD["<b>the write is blocked.</b><br/><small>Treated like a failing test,<br/>not a lint warning.</small>"]
  GBAD --> GLOOP["<b>the skill re-drafts and re-runs the gate</b><br/><small>“Fix and re-run until <code>OK</code>. Never present a draft<br/>that hasn't passed.” — all five SKILL.md files.<br/>This exit 1 is the only one nobody has to see:<br/>the loop is inside the skill, not in front of you.</small>"]
  GLOOP == "<b>loop until exit 0</b>" ==> G
  GLOOP -. "unless the paper genuinely isn't in the corpus —<br/>then it stops and tells you to sync" .-> E1

  classDef q fill:#eef2ff,stroke:#4f46e5,color:#1e1b4b
  classDef ok fill:#f0fdf4,stroke:#16a34a,color:#052e16
  classDef warn fill:#fefce8,stroke:#a16207,color:#422006
  classDef bad fill:#fef2f2,stroke:#dc2626,stroke-width:2px,color:#450a0a
  classDef hard fill:#dc2626,stroke:#7f1d1d,color:#ffffff

  class Q1,Q2,Q3,Q4,Q5,Q6,AGG,LOOP,PARSE,G q
  class E0,OK,SK,GOOD ok
  class GLOOP warn
  class E2,SN,PT2 warn
  class E1,PF,EC,EF bad
  class GBAD hard
```


### 6. Inside one parse

**Written for:** Someone tuning `[parser].workers`, or changing the pool.
**Answers:** what does a worker pool actually do here, and what happens when part of it dies?

The deepest view, and the only part of the repository that runs work in
parallel. Everything else -- retrieval, gating, rendering -- is
deliberately serial.

The through-line: **the pool is clamped to the host, not to the request.**
The ceiling counts the CPUs *this process* may run on
(`os.sched_getaffinity`), not the machine's, and an over-large request is
clamped *and said out loud* -- silently obeying thrashes, and silently
ignoring leaves someone believing they configured something they did not.

Five failure modes hang off the pool, each handled where it can be, and
the parent process keeps everything only it can do: sqlite has a single
writer, and the parent is the only place that can order results
deterministically. Full component-by-component write-up in
[docs/PARALLELISM.md](PARALLELISM.md); the reasoning behind the lock is in
[docs/DESIGN.md](DESIGN.md).

```mermaid
flowchart TB

  subgraph PRE["<b>before the pool exists</b>"]
    direction TB
    P1["<b>prestart_pool()</b><br/><small>forkserver begins importing torch + docling<br/>in the background — and declines outright when<br/>no pool is coming (pdftotext, or workers = 1)</small>"]
    P2["<b>read bibliography.bib</b> — ~2.5s<br/><small>deliberately started <i>after</i> the pre-start, so the<br/>import overlaps the read: a fixed ~1.5-2s saved</small>"]
    P3["<b>ask the ledger</b><br/><small>which documents actually need a parse</small>"]
    P1 -.-> P2 --> P3
  end

  W{{"<b>resolve_workers(n_docs)</b><br/><code>max(1, min(requested, ceiling, n_docs))</code>"}}

  subgraph CEIL["<b>the ceiling is the host's, not the request's</b>"]
    direction TB
    C1["<b>allowed_cpus()</b> = <code>len(os.sched_getaffinity(0))</code><br/><small>the CPUs <i>this process</i> may run on — not <code>os.cpu_count()</code>.<br/>On the dev machine those are 48 and 96.</small>"]
    C2["<b>÷ 4 for docling</b><br/><small>one worker was measured holding ~300% CPU.<br/>A later full-corpus sweep found the divisor<br/>too conservative by ~1.41x — not yet changed.</small>"]
    C3["<b>clamped <i>and</i> reported</b><br/><small>silently obeying thrashes; silently ignoring leaves<br/>someone believing they configured something they didn't</small>"]
    C1 --> C2 --> C3
  end

  SER["<b>workers = 1 — the default</b><br/><small>a genuinely different code path: no executor,<br/>no pickling, no subprocess. Incremental skipping<br/>means a routine run has almost nothing to do,<br/>so pool setup would cost more than it saves.</small>"]

  subgraph POOL["<b>the pool</b> — each backend gets the concurrency it can use"]
    direction TB
    EXE{{"backend?"}}
    TP["<b>ThreadPoolExecutor</b> · <code>pdftotext</code><br/><small>external subprocess — releases the GIL.<br/>Processes would add pickling and spawn cost<br/>to buy the same OS-level concurrency.</small>"]
    PP["<b>ProcessPoolExecutor</b> · <code>docling</code><br/><small>runs in-process and holds the GIL.<br/><b>forkserver</b> or <b>spawn</b>, never plain fork —<br/>a forked child inherits a broken CUDA context.</small>"]
    LPT["<b>submit biggest-file-first</b><br/><small>one 675-page document is 5% of the corpus's pages.<br/>Picked up last it would define the wall clock by itself.<br/>File size, not page count — counting pages needs a<br/>PDF library the core pipeline refuses to depend on.</small>"]
    DEV["<b>init_worker</b> — one CUDA card each<br/><small>a shared counter under a lock, round-robin over<br/><code>usable_devices()</code> (≥ 2.5 GiB free). Without this,<br/>docling's <code>AcceleratorDevice.AUTO</code> resolves to<br/><code>cuda:0</code> in <i>every</i> process.</small>"]
    EXE -- pdftotext --> TP
    EXE -- docling --> PP
    TP --> LPT
    PP --> LPT --> DEV
  end

  RET["<b>workers return</b> <code>(citekey, out_path, exception)</code><br/><small>the exception is <i>returned</i>, not raised, so both the value<br/>and its type survive pickling — <code>sync</code> reports<br/><code>ExtractionError</code> and <code>BackendUnavailable</code> differently</small>"]

  subgraph FAIL["<b>five failure modes, each handled where it can be</b>"]
    direction TB
    F1["<b>a dead worker</b> → <code>BrokenProcessPool</code><br/><small>results collected with <code>as_completed</code>, not <code>map</code>,<br/>so documents that already finished are kept</small>"]
    F2["<b>a hung pool</b> → stall watchdog<br/><small>fires when <i>no</i> document completes for <code>stall_timeout</code>;<br/>warns at half. Not a per-document deadline — no threshold<br/>separates a hang from the legitimate 246s document.</small>"]
    F3["<b>a slow document</b> → <code>document_timeout</code><br/><small>a real kill for pdftotext; a cooperative between-stages<br/>check for docling. Not equally strong, and said so.</small>"]
    F4["<b>CUDA OOM</b> → demote that worker to CPU<br/><small>for the rest of the run, and retry immediately.<br/>Surviving the CPU retry marks it <i>transient</i>.</small>"]
    F5["<b>Ctrl+C</b> → explicit SIGINT handler<br/><small><code>except KeyboardInterrupt</code> around <code>as_completed()</code><br/>never fires. Terminate, grace, <code>kill()</code>, <code>os._exit</code> —<br/>safe only because the ledger commits incrementally.</small>"]
  end

  PARENT["<b>the parent keeps what only the parent can do</b><br/>every ledger and cache write · results replayed <b>in bib order</b><br/><small>sqlite has a single writer, and the parent is the only place<br/>that can order results deterministically</small>"]

  PARTIAL["<b>partial success is a failure</b><br/><small><code>convert(raises_on_error=True)</code> raises only on FAILURE.<br/>A PARTIAL_SUCCESS would hand the citation gate a source that<br/>silently ends at page k of n — so both call sites check the<br/>status and raise <i>before</i> anything is written.</small>"]

  LOCK["<b>and around all of it: one writer at a time</b><br/><code>content/pipeline.lock.db</code> · a dedicated sqlite file under <code>BEGIN IMMEDIATE</code><br/><small>a RESERVED lock does not block readers, so the gate, retrieval and the<br/>drafting skills keep working during a run. A second writer gets SQLITE_BUSY<br/>and exits 2. A killed holder releases it immediately — no staleness heuristic.</small>"]

  P3 --> W
  CEIL -.-> W
  W -- "= 1" --> SER
  W -- "&gt; 1" --> EXE
  DEV --> RET
  SER --> RET
  RET --> PARTIAL --> PARENT
  FAIL -.-> RET
  LOCK -.-> PRE

  classDef pre fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef dec fill:#fefce8,stroke:#a16207,stroke-width:1.5px,color:#422006
  classDef pool fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef fail fill:#fef2f2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
  classDef parent fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#052e16
  classDef lock fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407

  class P1,P2,P3,C1,C2,C3,SER pre
  class W,EXE dec
  class TP,PP,LPT,DEV pool
  class F1,F2,F3,F4,F5,PARTIAL fail
  class RET,PARENT parent
  class LOCK lock
```

---

## By genre

The ladder above draws the pipeline as one thing. It isn't, quite -- the
five genre skills in `.claude/skills/` use very different amounts of it.
The heavy pipeline in particular is worth building for two of them,
largely wasted on two others, and reduced to a preview step for the fifth.

| Genre | Skills | Retrieval | Heavy pipeline | `src.references` |
|---|---|---|---|---|
| [Genre A: corpus-led](#genre-a-corpus-led) | `survey-writer`, `deep-research` | BM25 **or** `embed_index` | `docling` + `embed`, both worth it | yes |
| [Genre B: teaching](#genre-b-teaching) | `tutorial-writer`, `textbook-chapter-writer` | BM25 only | `render` only | yes (custom heading) |
| [Genre C: LaTeX-native](#genre-c-latex-native) | `thesis-chapter-writer` | BM25 only | `render`, as a throwaway preview | **no -- skipped** |

All five run the same gate, in the same loop, with the same wording.

### Genre A: corpus-led

**Skills:** `survey-writer`, `deep-research`

**The corpus is the content.** Nearly every sentence is a cited claim, so
these are the two skills that pay off the heavy pipeline: `docling` for
passages good enough to survive review, and `embed` for semantic recall --
finding the paper that makes your point in words you didn't search for.
They are also the only two skills whose SKILL.md names
`src.heavy.embed_index.search()` as an alternative to BM25.

`deep-research` is the only skill that reads `papers/pdfs/` -- the
non-citable source-PDF tray -- and it may only discuss those documents by
title, never cite them.

`bertopic` sits off to one side because **no skill calls it.** It is for
you, deciding what the survey should be about before anything is drafted.

Same reason, other direction: `src.citation_coverage` ("retrieval surfaced
this paper -- did the draft actually cite it?") is only a meaningful
question in this genre.

```mermaid
flowchart LR

  P0["<b>0 · CURATE</b><br/><i>you, in Zotero</i><br/><br/>Breadth is the whole job here.<br/>A thin corpus shows up<br/>immediately as a thin survey.<br/><br/><b>papers/bibliography.bib</b><br/><small>+ <code>papers/pdfs/</code> — <b>deep-research</b> is the<br/>only skill that reads those, and it may<br/>only discuss them by title, never cite them</small>"]

  P1["<b>1 · SYNC</b><br/><i>deterministic</i><br/><br/><code>python -m src.sync</code><br/><br/><b>content/ledger.sqlite</b><br/><b>content/parsed/*.txt</b>"]

  HEAVY["<b>HEAVY — worth it for this genre</b><br/><code>full_pipeline.py --stages docling,embed</code><br/><br/><b>docling</b> → layout-aware .md + .passages.json<br/><small>better quotable passages, so claims survive review</small><br/><br/><b>embed</b> → content/chroma/<br/><small>semantic recall: finds the paper that argues the<br/>point in different words. <b>Both skills name<br/><code>embed_index.search()</code> by hand.</b></small>"]

  P2["<b>2 · DRAFT</b><br/><i>every sentence is a cited claim</i><br/><br/><b>survey-writer</b> — over-fetch <code>k=15</code>,<br/>hand-cluster into 2-4 sub-themes,<br/>comparison table + gap analysis<br/><br/><b>deep-research</b> — perspectives,<br/>parallel interviews, contradiction map,<br/>then cited sections<br/><br/><b>content/drafts/&lt;slug&gt;.md</b>"]

  P3{{"<b>3 · VERIFY</b><br/><code>src.citation_gate</code><br/><br/>Highest citation density<br/>in the repo, so this<br/>is the genre where<br/>the gate earns its keep"}}

  FIX["<b>DISCARD DRAFT</b><br/><small>swap the key for one retrieval<br/>actually returned, or drop the claim.<br/>Never invent one.</small>"]

  P4["<b>4 · PUBLISH</b><br/><br/><code>src.references</code> → IEEE list<br/><code>render_output --format tex</code><br/><code>--format pdf</code> · <code>--format md</code><br/><br/><b>content/rendered/&lt;slug&gt;.pdf</b>"]

  AID["<b>afterwards, by you — not a gate</b><br/><code>--stages provenance</code> · <code>verbatim_check.py</code><br/><code>src.citation_coverage</code><br/><small>“retrieval surfaced it — did the draft cite it?”<br/>only meaningful when the corpus <i>is</i> the argument</small>"]

  BERT["<b>bertopic</b> → content/topics.json<br/><small>no skill calls this. It is for <i>you</i>, deciding what<br/>the survey should even be about.</small>"]

  P0 ==> P1 ==> P2 ==> P3
  P1 ==> HEAVY ==> P2
  P3 == "PASS · exit 0" ==> P4
  P3 -- "FAIL · exit 1" --> FIX
  FIX == "re-draft · <b>loop until it passes</b>" ==> P2
  P4 -.-> AID
  P1 -.-> BERT -.-> P0

  classDef you fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef det fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:3px,color:#450a0a
  classDef out fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
  classDef heavy fill:#faf5ff,stroke:#9333ea,stroke-width:2.5px,color:#3b0764
  classDef aid fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 3,color:#0f172a

  class P0 you
  class P1 det
  class P2 gen
  class P3 gate
  class P4 out
  class FIX bad
  class HEAVY heavy
  class AID,BERT aid
```


### Genre B: teaching

**Skills:** `tutorial-writer`, `textbook-chapter-writer`

**The corpus is a garnish.** Most of the content is original -- worked
examples, exercises, a lesson that has to actually run. Citations are
deliberately confined: `tutorial-writer` bans them mid-lesson and allows
them only in a closing "Where to go next", and `textbook-chapter-writer`
uses them for motivation and background.

So the heavy pipeline is mostly wasted here. Neither SKILL.md mentions
`embed_index`; both use `src.retrieval.search()`, which is stdlib BM25.
Building a semantic index to place four citations is effort in the wrong
place. `render` is the one stage they do want, and only at the very end.

This is also the one genre where **the gate can legitimately pass with
zero citations**, and both SKILL.md files say so. An empty reference list
is a correct outcome for a tutorial.

Which points at the real risk: the failure mode in this genre is not a bad
citekey, it is writing the wrong genre -- a tutorial that explains instead
of instructing. Both skills open by warning about exactly that, and no
gate in this repository can catch it.

```mermaid
flowchart LR

  P0["<b>0 · CURATE</b><br/><i>you, in Zotero</i><br/><br/>A handful of grounding papers is<br/>enough. Breadth buys you<br/>very little in this genre.<br/><br/><b>papers/bibliography.bib</b>"]

  P1["<b>1 · SYNC</b><br/><i>deterministic</i><br/><br/><code>python -m src.sync</code><br/><br/><b>content/ledger.sqlite</b><br/><b>content/parsed/*.txt</b>"]

  HEAVY["<b>HEAVY — skip the retrieval half</b><br/><br/><s>docling</s> · <s>embed</s> · <s>bertopic</s><br/><small>Neither SKILL.md mentions <code>embed_index</code>.<br/>Both use <code>src.retrieval.search()</code> — <b>BM25, stdlib</b> —<br/>and building a semantic index to place four<br/>citations is effort spent in the wrong place.</small><br/><br/><b>render</b> — the one stage they do need<br/><small>and only at the very end</small>"]

  P2["<b>2 · DRAFT</b><br/><i>mostly original content</i><br/><br/><b>tutorial-writer</b> — one path, keyboard-first,<br/>verified to actually run. Citations are<br/><b>banned mid-lesson</b>; they live only in<br/>a closing “Where to go next”.<br/><br/><b>textbook-chapter-writer</b> — objectives,<br/>worked examples, exercises. Cites for<br/><b>motivation and background only</b>.<br/><br/><b>content/drafts/&lt;slug&gt;.md</b>"]

  P3{{"<b>3 · VERIFY</b><br/><code>src.citation_gate</code><br/><br/><small><b>May legitimately pass with<br/>zero citations.</b> Both SKILL.md<br/>files say so explicitly — this is<br/>the one genre where an empty<br/>reference list is correct.</small>"}}

  FIX["<b>DISCARD DRAFT</b><br/><small>drop the claim; the lesson<br/>rarely needed it in the first place</small>"]

  P4["<b>4 · PUBLISH</b><br/><br/><code>src.references</code><br/><small><b>tutorial-writer</b> passes<br/><code>--heading &quot;Further reading&quot;</code>,<br/>which then survives into the render<br/>instead of being stripped</small><br/><br/><code>render_output --format pdf</code>"]

  RISK["<b>the real failure mode here isn't a bad citekey</b><br/><small>It is writing the wrong genre: a tutorial that explains<br/>instead of instructing, or a chapter that instructs instead<br/>of explaining. Both SKILL.md files open by warning about<br/>exactly that — and no gate in this repository can catch it.<br/><b>You are the check.</b></small>"]

  P0 ==> P1 ==> P2 ==> P3
  P1 -. "render only" .-> HEAVY
  HEAVY -.-> P4
  P3 == "PASS · exit 0<br/>(often trivially)" ==> P4
  P3 -- "FAIL · exit 1" --> FIX
  FIX == "re-draft · <b>loop until it passes</b>" ==> P2
  P2 -.-> RISK

  classDef you fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef det fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:3px,color:#450a0a
  classDef out fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
  classDef off fill:#f1f5f9,stroke:#94a3b8,stroke-dasharray:5 4,color:#334155
  classDef risk fill:#fefce8,stroke:#a16207,stroke-width:2px,color:#422006

  class P0 you
  class P1 det
  class P2 gen
  class P3 gate
  class P4 out
  class FIX bad
  class HEAVY off
  class RISK risk
```


### Genre C: LaTeX-native

**Skills:** `thesis-chapter-writer`

**The output isn't the deliverable.** This skill emits a standalone `.tex`
fragment with `\citep`/`\citet` and no preamble, meant to be `\input` by
your own thesis document -- so the heavy pipeline's `render` stage
produces a *preview*, not the artifact that matters. A rendering failure
never blocks presenting the draft.

It is also the only skill that **deliberately skips `src.references`**.
Your thesis owns its own bibliography; a second reference list inside the
fragment would collide with it. That exception is easy to miss when
reading the README's `citation_gate -> references -> render_output` chain,
which is why it gets its own diagram.

The gate is not relaxed for LaTeX: `src.citation_gate` reads
`\citep`/`\citet` as readily as Markdown `[@key]`, and the loop-until-`OK`
rule is worded identically to the other four skills.

```mermaid
flowchart LR

  P0["<b>0 · CURATE</b><br/><i>you, in Zotero</i><br/><br/>Depth over breadth: everything<br/>bearing on one research question.<br/><br/><b>papers/bibliography.bib</b><br/><small>this same .bib is very likely already<br/>your thesis's <code>\\bibliography</code> — which is<br/>why the two stay consistent for free</small>"]

  P1["<b>1 · SYNC</b><br/><i>deterministic</i><br/><br/><code>python -m src.sync</code><br/><br/><b>content/ledger.sqlite</b><br/><b>content/parsed/*.txt</b>"]

  HEAVY["<b>HEAVY — preview only</b><br/><br/><s>docling</s> · <s>embed</s> · <s>bertopic</s><br/><small>SKILL.md names <code>src/retrieval.py</code> alone —<br/><b>BM25, <code>k=15</code>, then filter by hand</b></small><br/><br/><b>render</b> — and even this is disposable<br/><small><code>--format md</code> and <code>--format pdf</code>,<br/>to <i>look</i> at the chapter. The artifact that<br/>matters is the .tex you <code>\\input</code>.<br/>“A rendering failure never blocks<br/>presenting the draft.”</small>"]

  P2["<b>2 · DRAFT</b><br/><i>RQ-driven narrative</i><br/><br/>A standalone <b>.tex fragment</b> —<br/><code>\\citep</code> / <code>\\citet</code>, <b>no preamble</b>,<br/>meant to be <code>\\input</code> by your own<br/>thesis document.<br/><br/><b>content/drafts/&lt;slug&gt;.tex</b>"]

  P3{{"<b>3 · VERIFY</b><br/><code>src.citation_gate</code><br/><br/><small>Reads <code>\\citep</code>/<code>\\citet</code> as<br/>readily as Markdown <code>[@key]</code>,<br/>so the .tex path is gated<br/>exactly as hard</small>"}}

  FIX["<b>DISCARD DRAFT</b><br/><small>“Fix and re-run until <code>OK</code>.<br/>Never present a draft<br/>that hasn't passed.”</small>"]

  P4["<b>4 · PUBLISH</b> — <i>the odd one out</i><br/><br/><b>❌ <s>src.references</s> — deliberately skipped</b><br/><small>the only genre that skips it. Your thesis's own<br/>bibliography owns the reference list; a second<br/>one inside the fragment would collide with it.</small><br/><br/><code>render_output --format md | pdf</code><br/><small>a preview for you, not the deliverable</small>"]

  OUT["<b>the actual output</b><br/><code>\\input{chapter-4}</code><br/><small>into your own thesis, compiled by your own<br/>LaTeX toolchain against your own .bib</small>"]

  P0 ==> P1 ==> P2 ==> P3
  P1 -. "render only" .-> HEAVY
  HEAVY -.-> P4
  P3 == "PASS · exit 0" ==> P4
  P3 -- "FAIL · exit 1" --> FIX
  FIX == "re-draft · <b>loop until it passes</b>" ==> P2
  P4 ==> OUT

  classDef you fill:#fff7ed,stroke:#c2410c,stroke-width:1.5px,color:#431407
  classDef det fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b
  classDef gen fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#052e16
  classDef gate fill:#fef2f2,stroke:#dc2626,stroke-width:3px,color:#450a0a
  classDef out fill:#faf5ff,stroke:#9333ea,stroke-width:1.5px,color:#3b0764
  classDef bad fill:#fee2e2,stroke:#dc2626,stroke-width:1.5px,color:#450a0a
  classDef off fill:#f1f5f9,stroke:#94a3b8,stroke-dasharray:5 4,color:#334155
  classDef final fill:#ecfdf5,stroke:#16a34a,stroke-width:2.5px,color:#052e16

  class P0 you
  class P1 det
  class P2 gen
  class P3 gate
  class P4 out
  class FIX bad
  class HEAVY off
  class OUT final
```

---

## Appendix

These two answer narrower questions than the groups above, and are kept
separate for that reason.

### One draft, in time order

The full workflow with time on the vertical axis instead of dependency.
Useful for two things in particular: seeing that **the gate runs twice**
-- the PostToolUse hook fires on every write under `content/drafts/`, so a
bad citekey cannot reach disk even if a skill forgets, and the skill then
runs the gate itself, because the hook only fires on the tool call that
wrote the file -- and seeing the **loop** that wraps both of them.

```mermaid
sequenceDiagram
    autonumber
    actor You as You
    participant Z as Zotero
    participant Sync as src.sync<br/>(job 1)
    participant Led as content/<br/>ledger.sqlite
    participant Ret as src.retrieval<br/>+ src.passages
    participant Skill as genre skill<br/>(.claude/skills/)
    participant Hook as PostToolUse hook
    participant Gate as src.citation_gate
    participant Ref as src.references
    participant Ren as render_output<br/>(pandoc / TeX Live)

    rect rgba(255,247,237,0.6)
    Note over You,Z: CURATE — the only step that adds a paper
    You->>Z: add papers, then Export BibTeX + files
    Z-->>You: papers/bibliography.bib + attachment tree
    end

    rect rgba(238,242,255,0.6)
    Note over Sync,Led: SYNC — deterministic, incremental, holds the run lock
    You->>Sync: python -m src.sync
    Sync->>Sync: take content/pipeline.lock.db
    Sync->>Led: upsert one row per citekey
    Led-->>Sync: which PDFs actually changed
    Sync->>Sync: extract text → content/parsed/*.txt
    Sync->>Led: mark parsed / parse_failed / no_pdf
    Sync-->>You: summary + stale list + exit 0 | 1 | 2
    end

    rect rgba(240,253,244,0.6)
    Note over You,Skill: DRAFT — the generative half
    You->>Skill: "write a survey section on digital twin composability"
    Skill->>Led: is the ledger populated?
    Led-->>Skill: N citekeys parsed
    Skill->>Ret: search(query, k)
    Ret-->>Skill: ranked snippets, each tied to a real citekey
    Skill->>Skill: write content/drafts/<slug>.md
    end

    rect rgba(254,242,242,0.7)
    Note over Hook,Gate: VERIFY — enforced mechanically, twice
    Hook->>Gate: every Write/Edit under content/drafts/
    Gate->>Led: are these citekeys in the ledger?
    loop until the gate exits 0 — you are shown nothing before that
        alt any citekey unresolved
            Led-->>Gate: no
            Gate-->>Hook: FAIL (exit 1)
            Hook-->>Skill: the write is blocked
            Skill->>Skill: swap the key for one retrieval returned,<br/>or drop the claim — never invent one
        else all resolvable
            Led-->>Gate: yes
            Gate-->>Skill: PASS (exit 0)
        end
    end
    Skill->>Gate: runs the gate again itself, before showing you anything
    Note right of Skill: If the paper genuinely isn't in the corpus,<br/>the loop stops and the skill tells you to<br/>add it in Zotero and re-run sync.
    end

    rect rgba(250,245,255,0.6)
    Note over Ref,Ren: PUBLISH
    Skill->>Ref: python3 -m src.references <draft>
    Ref->>Led: bib_fields for exactly the cited citekeys
    Ref-->>Skill: a ## References section, numbered by first appearance
    Skill->>Ren: render_output --format pdf
    Ren-->>You: content/rendered/<slug>.pdf
    end

    Note over You,Ren: Optional afterwards, and never a gate:<br/>citation_provenance · verbatim_check · citation_coverage
```


### The life of a single citekey

The ledger's own state machine, for when the question is "why can't I
cite this paper?". `parsed` is the only state that makes a citekey
citable, and the self-loop on it is the incremental path that makes a
routine re-run nearly free.

Note that `stale` deletes nothing. A bib export that comes back short a
citekey is far more often a botched re-export or a `BIB_FILE` pointing at
the wrong path than an intentional deletion, so the row survives until you
re-run with `--remove-stale`.

```mermaid
stateDiagram-v2
    direction TB

    [*] --> discovered : appears in bibliography.bib<br/>(src.bib_reader)

    discovered --> no_pdf : no resolvable<br/>file field
    discovered --> parsing : PDF found and<br/>its bytes changed

    no_pdf --> parsing : you attach the PDF in<br/>Zotero and re-export

    parsing --> parsed : text extracted in full
    parsing --> failed_deterministic : backend cannot read this<br/>PDF, or parsed it only partly
    parsing --> failed_transient : worker died, timeout,<br/>CUDA OOM, broken pool

    failed_transient --> parsing : retried automatically<br/>on the next sync
    failed_deterministic --> parsing : only on<br/>sync --reparse

    parsed --> parsing : PDF bytes changed<br/>(size+mtime, then sha256)
    parsed --> parsed : bytes unchanged, skipped —<br/>the incremental path

    parsed --> stale : dropped out of<br/>bibliography.bib
    no_pdf --> stale : dropped out of<br/>bibliography.bib
    stale --> parsed : it was a botched re-export —<br/>fix the export and re-sync
    stale --> [*] : sync --remove-stale<br/>opt-in, after you read the list

    note right of parsed
        Only this state makes the citekey citable.
        citation_gate resolves a citekey against
        the ledger, so a row that is not here
        cannot appear in a draft that renders.
    end note

    note left of failed_deterministic
        Never silent, never pointlessly expensive.
        Not re-parsed — the same minutes would
        reach the same answer — but every run
        exits 1 until a human deals with it.
    end note
```

---

## Editing these

Each diagram is plain Mermaid in a fenced block. GitHub renders them, and
so does any Mermaid-aware editor. To regenerate SVG or PNG copies for a
slide deck or a paper:

```bash
npm install -g @mermaid-js/mermaid-cli
mmdc -i diagram.mmd -o diagram.svg -b white -w 1900
```

Keep them honest the way the rest of this repository stays honest: if a
diagram and the code disagree, the diagram is the bug.

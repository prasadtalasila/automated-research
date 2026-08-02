# Design review for automated-research

## Repository constraints and operating model

The repository is designed around a few hard constraints that strongly shape the architecture:

1. **Never fabricate a citekey**
   - Citekeys must come from `papers/bibliography.bib` and be synchronized into `content/ledger.sqlite`.
   - Drafts must pass `python -m src.citation_gate` before being considered valid.
   - This is the repo's primary safety invariant.

2. **Two-job split**
   - Job 1: deterministic content maintenance (`python -m src.sync`).
   - Job 2: generative drafting and heavier processing via Claude Code skills or the heavy pipeline.
   - Ad-hoc review tools remain outside the automatic chain.

3. **Config and host variability are first-class concerns**
   - `config.toml` is the single source of configuration.
   - Stages must probe for binaries/services rather than assume availability.
   - Missing dependencies should degrade to honest reporting, not crashes or silent success.

4. **Incremental processing is a load-bearing design goal**
   - `ledger.py` uses stat-before-hash skipping.
   - `retrieval.py` caches term-frequency stats.
   - `embed_index.py` and `topic_model.py` cache embeddings.
   - `docling_parse.py` fingerprints each PDF by (size, mtime).
   - The intent is to avoid reprocessing unchanged material.

5. **Parallelism is opt-in, and bounded by the host rather than by the
   request**
   - `[parser].workers` defaults to `1`, so the default run is strictly
     serial -- no pool, no subprocesses. Incremental skipping means a
     routine run has almost nothing to do, and pool setup would cost more
     than it saves.
   - The resolved count is `min(requested, host ceiling, work available)`.
     The host ceiling counts CPUs *this process* may run on, not the
     machine's, and divides by the CPUs one worker actually occupies.
   - Only the parse call is dispatched. Every ledger and cache write
     stays on the parent process, because sqlite has a single writer and
     because the parent is the only place that can order results
     deterministically.
   - Backends get the concurrency they can use: threads for `pdftotext`
     (external subprocess, releases the GIL), processes for `docling`
     (in-process, holds it), with one CUDA device claimed per worker.

## Design pattern review

### 1. Pipeline architecture
The project is fundamentally a staged pipeline:
- BibTeX export -> ledger -> parsed text -> retrieval
- Optional heavy pipeline: Docling -> embeddings -> BERTopic -> render

This is the dominant structural pattern and fits the use case well.

### 2. Separation of concerns
Modules are mostly narrowly scoped:
- `bib_reader.py` parses bibliographic input
- `ledger.py` persists state and change detection
- `pdf_text.py` handles plain-text extraction
- `retrieval.py` ranks documents
- `citation_gate.py` validates citations
- `render_output.py` renders drafts

This is a strong point of the codebase.

### 3. Adapter/facade pattern around external tools
Several modules wrap command-line tools or services behind Python APIs:
- `pdftotext`
- Docling
- Pandoc/TeX Live

This keeps the rest of the system insulated from tool-specific details.

### 4. Strategy-like backend substitution
The repo already supports alternate approaches for retrieval and parsing:
- BM25 retrieval in the core pipeline
- embedding retrieval in the heavy pipeline
- plain extraction vs structured extraction

That is a good fit for a tiered research workflow.

### 5. Guard pattern
`citation_gate.py` is a hard validation gate that protects downstream stages. This is appropriate for a citation-grounded writing system.

### 6. Repository-style persistence layer
`ledger.py` functions as a small persistence/repository layer over SQLite. It keeps the rest of the code from directly dealing with SQL details.

## SOLID review

### S — Single Responsibility Principle
Mostly good.

- `config.py` is focused.
- `ledger.py` is focused.
- `pdf_text.py` is focused.
- `citation_gate.py` is focused.

The main exception is orchestration modules like `sync.py` and `full_pipeline.py`, which coordinate multiple concerns. That is acceptable for entrypoints, but they should stay orchestration-only.

### O — Open/Closed Principle
Moderately good.

The heavy pipeline is extensible by stage, and retrieval has an upgrade path from BM25 to embeddings. However, many concrete integrations are still hard-coded to specific tools. Introducing explicit backend interfaces would improve openness.

### L — Liskov Substitution Principle
Reasonably good.

`CorpusDoc` is used consistently, and the code avoids inheritance-heavy designs. There is little sign of substitution problems, but this also means there are few abstractions to test.

### I — Interface Segregation Principle
Fairly good.

The modules expose small APIs, which is positive. The remaining opportunity is to split larger helpers into smaller, more specialized interfaces, especially in retrieval and parsing stages.

### D — Dependency Inversion Principle
This is the weakest SOLID area.

Most integrations depend directly on concrete tools and binaries. That is understandable for a pipeline repo, but it makes portability and testing harder.

Suggested improvement:
- define small interfaces for text extraction, structured parsing, retrieval, and embedding
- inject implementations where practical instead of importing concrete tools everywhere

## Design improvement recommendations

### 1. Use hierarchical document representations
Instead of only parsing to flat text, preserve:
- document-level text
- section-level text
- chunk-level text
- page provenance

This would improve retrieval, reranking, and downstream citation support.

### 2. Split retrieval responsibilities
`retrieval.py` currently does tokenization, caching, scoring, and snippet building. Consider splitting this into:
- indexing/cache maintenance
- ranking
- snippet generation
- backend selection

### 3. Improve platform portability
The repository is cross-platform-friendly in code, but toolchain-dependent at runtime.

Likely rough spots:
- `pdftotext` availability
- `pandoc`/`pdflatex`
- Docling model/runtime dependencies

Suggested mitigations:
- clearer OS-specific setup docs
- optional fallback backends
- extend the CI matrix beyond the current Linux + Windows legs to macOS

### 4. Add reranking
For search quality, a good pattern would be:
- BM25 or vector retrieval first
- lightweight reranker second

That would improve precision without making every query expensive.

### 5. Preserve more structured metadata
When parsing PDFs, keep:
- page numbers
- section titles
- source tool
- extraction confidence if available
- citation/reference blocks where possible

This would improve evidence quality substantially.

## Parser backends

[PDF-PARSER.md](PDF-PARSER.md) owns the backend comparison -- the
tradeoffs, the two backends evaluated and removed, and the measured
speed figures. It is not restated here.

## Overall assessment

The codebase has a strong conceptual model:
- safe citation handling
- deterministic core pipeline
- clear heavy-stage upgrade path
- incremental caching
- good test coverage around key invariants

Its main opportunities are:
- stronger abstraction over parsing backends
- more structure-aware indexing
- improved cross-platform ergonomics
- making the heavy path more incremental

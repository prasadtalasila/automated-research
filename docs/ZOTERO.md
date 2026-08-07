## Exporting your library from Zotero

How to get a `.bib` file and its PDFs into the shape this pipeline
expects. See [../README.md](../README.md) for the Quickstart that
refers here.

Step 1 above, in detail, for [Zotero](https://www.zotero.org/) (see its own
[export documentation](https://www.zotero.org/support/kb/exporting) for the
general feature):

1. Right-click the collection you want (or use **File -> Export Library**
   for everything) -> **Export Collection...** / **Export Library...**.
2. Format: **BibTeX**. Check **Export Files** -- without it you get
   metadata only and `pdf_text.py` will have nothing to extract.
3. Save it as `bibliography` directly inside this repo's `papers/`
   directory. Zotero writes `papers/bibliography.bib` plus a **companion
   folder** (`papers/bibliography/`, `files/<id>/<name>.pdf` inside) for
   every attachment -- the exported `.bib`'s `file` field encodes that
   folder's name as a literal relative path, tied to whatever name you
   gave the export at save time.
4. **Don't rename that companion folder afterward.** `src/bib_reader.py`'s
   `_resolve_pdf_path` resolves each entry's `file` field relative to
   wherever `bibliography.bib` itself lives (`papers/`) -- if you rename
   or move the attachments folder, that relative path breaks silently
   (entries just show up as "without a PDF attachment" after `sync`, not
   as an error).
5. Re-run `python -m src.sync`.

A Zotero export is the **only** way to get a paper into this pipeline.
There is no directory you can drop a raw PDF into to have it indexed:
the enrichment layer's corpus is the bibliography, so anything it can
retrieve is something a draft may cite -- see
[`src/enrich/corpus.py`](../src/enrich/corpus.py) and AGENTS.md's citekey
invariant. (Earlier versions did have such a directory, `papers/pdfs/`;
it is gone. A PDF there is now simply ignored.)

To add more papers later: add the entry in Zotero, re-export the same way
(re-check **Export Files** so new attachments are included), then re-run
`python -m src.sync`.

Removing a paper: delete the entry in Zotero, re-export, re-run `sync`.
By default `sync` only *reports* citekeys that dropped out of the bib file
(`stale   <citekey> (no longer in bibliography.bib)`, one line per
citekey, plus a single summary note pointing at `--remove-stale`) -- it
doesn't delete their `content/ledger.sqlite` row until you re-run with
`--remove-stale`. This is deliberate: a bib export that comes back short a
citekey is far more often a botched re-export or `BIB_FILE` pointing at the
wrong path than an intentional deletion, so the default keeps the ledger
untouched until a human confirms. `--remove-stale` still refuses (raises)
if the bib file comes back completely empty against a non-empty ledger,
for the same reason -- fix the export/path rather than deleting everything
in one run.

## Citekeys have to work as filenames

A citekey is not only an identifier here -- it is the stem of every file
the pipeline writes for that paper (`content/parsed/<citekey>.txt`, and
the enrichment layer's `content/docling/<citekey>.md`). So a citekey
containing `/` or `\`, one of `: * ? " < > |`, or a name Windows reserves
(`CON`, `NUL`, `COM1`...) is **skipped**, with a warning naming it:

```
  WARNING skipping citekey 'smith/2024': it contains '/', which cannot
  appear in a filename. ...  Rename it in your reference manager,
  re-export, and re-run sync.
```

Zotero's own key generator won't produce one of those, so you are most
likely to hit this with Better BibTeX and a custom key pattern. This
project never rewrites a citekey -- the bib file is the source of truth --
so the fix is always to rename it in the reference manager and re-export.
Ordinary accented characters (`naïve_2024`) are fine; they are legal in a
filename.

All paths are configurable in `config.toml` (repo root), overridable
per-run with an env var of the same name, e.g. `BIB_FILE=/path/to/other.bib
python -m src.sync`. See [CONFIG.md](CONFIG.md) for the
full settings reference.



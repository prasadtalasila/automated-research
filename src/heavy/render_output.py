"""Stage 7: Pandoc/LaTeX rendering of generated Markdown into PDF/DOCX.

Needs the `pandoc` and TeX Live binaries (apt packages, not pip -- not
installable via pyproject.toml/Poetry or any other venv mechanism). Verified working
on this host (2026-07-28): `pandoc`, `pdflatex`, `latexmk` are all on
PATH. Where they aren't, this stage fails cleanly with MissingBinary
rather than hanging or stack-tracing -- see docker/Dockerfile for a
target that installs them when the host doesn't have root.

Every genre-skill draft cites sources with Pandoc-style `[@citekey]`
markers (see src/citation_gate.py), so rendering always resolves them via
pandoc's built-in `--citeproc` against `config.BIB_FILE_PATH` -- without
it, citations would come out as literal, unresolved `[@key]` text with no
bibliography. Pandoc's own citation-key tokenizer has a real limitation
that surfaces on this corpus: a double hyphen (`--`) inside a citekey
(bibtexparser produces these, e.g. `zech_digital-twins-as--service_2024`)
truncates the key mid-token, silently losing the citation. `_safe_render_inputs`
works around this by aliasing just the affected citekey(s) in temporary
copies of the input and the bib file -- never touching the real
`bibliography.bib` -- before handing both to pandoc.

Citations render in IEEE style -- numeric `[1]` markers, `[3]-[6]` for a
consecutive run, over a numbered list of complete entries -- via the CSL
style vendored at `assets/csl/ieee.csl` (`config.CSL_STYLE_PATH`,
`--csl` to override). Two notes on how that is wired, both explained
where they're implemented: the collapsing needs one attribute upstream
IEEE omits, injected into a temp copy by `_collapsed_csl` so the vendored
file stays byte-identical to upstream; and a draft's own citekey-labeled
References section (added by `python -m src.references`) has its entries
swapped out in the temp copy by `_swap_manual_refs_for_citeproc` --
heading kept, entries replaced by the anchor citeproc fills in -- so
citeproc's bibliography is the only one in the output, and it appears
under the draft's own heading. citeproc's is the one that can be numbered
consistently with the inline markers, and the one with authors and venues
in it, because it reads `bibliography.bib` directly. Inputs with no such
section (e.g. thesis-chapter-writer's preamble-less `.tex` fragment,
which defers to the user's own thesis-wide bibliography) are unaffected.

Every render also passes documentclass/fontsize/papersize/geometry
variables so a tex/pdf output always opens with a 12pt, a4paper article
class and 1-inch margins via the geometry package -- overridable per
call, but those are the project's fixed defaults.

`python -m src.heavy.render_output <file> --format tex|pdf|...` runs standalone
with bare `python3` (no heavy venv) -- it depends only on stdlib plus
`src.config`/`src.citation_gate`/`src.references` (all three stdlib-only,
same as this module), deliberately independent of `scripts/full_pipeline.py`,
which drags in the full corpus build and the docling/embed/topic_model
imports for stages this one doesn't need. The genre-writing skills under
`.claude/skills/` call this CLI directly.
"""

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from src import config, references
from src.citation_gate import _PANDOC_CITE_RE


class MissingBinary(RuntimeError):
    pass


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise MissingBinary(
            f"'{binary}' is not on PATH. This stage needs Pandoc + TeX Live, "
            "which need root to install (apt) and aren't available here. "
            "Use the Docker target (docker/Dockerfile installs both)."
        )


def _alias_for(citekey: str) -> str:
    # "--" is the one substring pandoc's own citation tokenizer can't
    # carry through a citekey (see module docstring) -- collapsing it to
    # a single hyphen plus a marker keeps the alias readable and, checked
    # against every citekey currently in the ledger, collision-free.
    #
    # Every hyphen in a run has to be separated, not just the first pair:
    # a plain .replace("--", "-x2d-") turns the 3-hyphen run in this
    # corpus's own `tygesen_state---art_2019` into `state-x2d--art`, which
    # still contains "--" and so still truncates -- the citation resolves
    # to nothing and the source silently drops out of the bibliography.
    # A run of n hyphens becomes "-" + "x2d-" * (n-1), which reduces to
    # the original "-x2d-" for the 2-hyphen case.
    return re.sub(r"-{2,}", lambda m: "-" + "x2d-" * (len(m.group()) - 1), citekey)


def _safe_render_inputs(input_path: Path, bib_path: Path, tmp_dir: Path) -> tuple[Path, Path]:
    """Returns (markdown_path, bib_path) safe to hand to `pandoc --citeproc`.

    Two independent fixups, both applied only to temp copies -- the draft
    and the real bibliography.bib are never modified:
      - a `python -m src.references` References section has its entries
        replaced by citeproc's own placement anchor, keeping the draft's
        heading (see _swap_manual_refs_for_citeproc);
      - a citekey containing "--" is aliased in both files, in the input
        and the bib together, because pandoc's citation tokenizer would
        otherwise truncate it mid-key and silently drop the citation.

    Returns the original paths untouched when neither applies.
    """
    original = input_path.read_text()
    text = _swap_manual_refs_for_citeproc(original)
    bad_keys = {m.group(1) for m in _PANDOC_CITE_RE.finditer(text) if "--" in m.group(1)}
    if not bad_keys:
        if text == original:
            return input_path, bib_path
        safe_md = tmp_dir / input_path.name
        safe_md.write_text(text)
        return safe_md, bib_path

    bib_text = bib_path.read_text()
    for key in bad_keys:
        alias = _alias_for(key)
        text = re.sub(
            r"(?<![A-Za-z0-9._%+-])(-?@)" + re.escape(key) + r"(?![A-Za-z0-9_-])",
            r"\1" + alias,
            text,
        )
        # Anchored on the entry header's trailing "," so e.g. aliasing
        # `zech_digital-twins-as--service_2024` doesn't also touch the
        # separate `zech_digital-twins-as--service_2024-1` entry.
        bib_text = re.sub(
            r"(@\w+\{)" + re.escape(key) + r"(,)",
            r"\1" + alias + r"\2",
            bib_text,
            count=1,
        )

    safe_md = tmp_dir / input_path.name
    safe_bib = tmp_dir / bib_path.name
    safe_md.write_text(text)
    safe_bib.write_text(bib_text)
    return safe_md, safe_bib


# The `<citation>` element opening tag, with or without attributes
# already on it. Upstream CSL styles hand-format this file, so matching
# the tag textually (rather than parsing and re-serializing the whole
# document with ElementTree, which rewrites namespace prefixes and
# reflows every other element) keeps the temp copy a one-attribute diff
# against the vendored original -- which is the point of not editing the
# vendored file in the first place.
_CSL_CITATION_TAG_RE = re.compile(r"<citation(\s[^>]*?)?(/?)>")


def _collapsed_csl(csl_path: Path, tmp_dir: Path) -> Path:
    """Returns a CSL style path whose citations collapse consecutive runs.

    Upstream `ieee.csl` renders `[@a; @b; @c; @d]` as "[1], [2], [3], [4]".
    The IEEE Reference Guide's own examples use the collapsed "[1]-[4]"
    form, and CSL has exactly one knob for it: `collapse="citation-number"`
    on `<citation>`. Rather than carry a modified copy of a CC BY-SA style
    in-tree (where the deviation is invisible in a diff against upstream
    and easy to lose across a style bump), the attribute is added here, to
    a temp copy, and assets/csl/ieee.csl stays byte-identical to what the
    CSL project publishes.

    A style that already sets `collapse` is returned unchanged -- its
    author made a deliberate choice, and overriding it would silently
    change how someone's own style renders.
    """
    text = csl_path.read_text()
    match = _CSL_CITATION_TAG_RE.search(text)
    if match is None or "collapse=" in (match.group(1) or ""):
        return csl_path

    patched = (
        text[:match.start()]
        + f'<citation collapse="citation-number"{match.group(1) or ""}{match.group(2)}>'
        + text[match.end():]
    )
    out = tmp_dir / csl_path.name
    out.write_text(patched)
    return out


# Pandoc's own idiom for "put the bibliography exactly here" -- citeproc
# fills this div in place instead of appending its bibliography to the end
# of the document. `fenced_divs` is on by default in pandoc's markdown.
_REFS_ANCHOR = "::: {#refs}\n:::\n"


def _swap_manual_refs_for_citeproc(text: str) -> str:
    """Replaces a `python -m src.references` section's *entries* with an
    anchor citeproc fills in, keeping the draft's own heading.

    Only ever applied to the temp copy handed to pandoc, never to the
    draft itself. The draft keeps its citekey-labelled entries: a reader
    (or `citation_gate`) can trace one back to a literal key, which is the
    project's whole citation invariant. What that hand-built list cannot
    be is *numbered consistently with the rendered output* -- pandoc
    assigns citation numbers itself -- so the rendered artifact takes
    citeproc's bibliography instead, drawn straight from bibliography.bib
    with authors and venues in it. That is what
    `--metadata suppress-bibliography=true` used to prevent here, back
    when the manual section was the only one with real entries in it.

    The heading stays because it is the draft's own: a genre skill may
    have numbered it to match its other headings (`## 6. References`, via
    `src.references --heading`), and citeproc emits no heading of its own,
    so dropping the whole section left the rendered bibliography
    untitled.
    """
    lines = text.splitlines(keepends=True)
    idx = references.section_start(lines)
    if idx is None:
        return text
    heading = lines[idx] if lines[idx].endswith("\n") else lines[idx] + "\n"
    return "".join(lines[:idx]).rstrip() + f"\n\n{heading}\n{_REFS_ANCHOR}"


# Matches Markdown image syntax: ![alt](path) or ![alt](path "title").
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
# A URI scheme prefix (http:, https:, data:, ...) -- pandoc fetches these
# itself; nothing local to copy.
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _local_image_refs(text: str) -> list[str]:
    """Every local (non-URL) image path a Markdown draft references."""
    return [
        ref for ref in (m.group(1) for m in _MD_IMAGE_RE.finditer(text))
        if not _URI_SCHEME_RE.match(ref)
    ]


def _copy_local_images(input_path: Path, dest_dir: Path) -> None:
    """Copies every local image `input_path` references alongside the
    rendered output in `dest_dir`, so a `tex` output is actually
    self-contained and compilable on its own (`cd content/rendered &&
    pdflatex *.tex`).

    Without this, pandoc's LaTeX writer emits `\\includegraphics{path}`
    verbatim, unresolved and uncopied -- the `pdf` format only looks fine
    because pandoc's own internal pdflatex pass reads the image directly
    via `--resource-path` below, in a temp dir this function never touches.
    A relative `path` an image reference doesn't resolve to a real file
    under `input_path`'s own directory is silently skipped here (letting
    pandoc's own missing-resource handling surface it, same as today), as
    is any reference that would resolve outside `input_path`'s directory
    (absolute, or `..`-escaping) -- a draft's image references are never a
    reason to write outside `dest_dir`.
    """
    for ref in _local_image_refs(input_path.read_text()):
        ref_path = Path(ref)
        if ref_path.is_absolute() or ".." in ref_path.parts:
            continue
        src = input_path.parent / ref_path
        if not src.is_file():
            continue
        dst = dest_dir / ref_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def render(
    input_path: str,
    output_format: str = "pdf",
    documentclass: str = "article",
    fontsize: str = "12pt",
    papersize: str = "a4",
    margin: str = "1in",
    csl: str | Path | None = None,
    collapse_citations: bool | None = None,
) -> Path:
    """Renders `input_path` (Pandoc markdown) to `output_format` (pdf/tex/docx/...).

    Citations and the bibliography are formatted with `csl` (default:
    `config.CSL_STYLE_PATH`, the vendored IEEE style), so a rendered draft
    carries numeric markers -- `[1]`, and `[3]-[6]` for a consecutive run
    when `collapse_citations` (default: `config.RENDER_COLLAPSE_CITATIONS`)
    -- over a numbered reference list of complete entries. Without a
    `--csl`, pandoc falls back to Chicago author-date, which is what this
    rendered before.

    `--standalone` is always passed so a `tex` output is a complete,
    compilable LaTeX document (documentclass + preamble), not a bare
    fragment -- matching what pandoc already builds internally on the way
    to a `pdf` output. `documentclass` defaults to LaTeX's plain `article`
    class, the right shape for the short, section-based genre drafts this
    project produces (no chapters, no front matter); pass a different
    value only if a specific draft genuinely needs one. `fontsize`/
    `papersize`/`margin` default to this project's fixed house style --
    `\\documentclass[12pt,a4paper]{article}` plus
    `\\usepackage[margin=1in]{geometry}`.
    """
    _require("pandoc")
    if output_format == "pdf":
        _require("pdflatex")

    config.RENDERED_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path)
    _copy_local_images(input_path, config.RENDERED_DIR)
    out_path = config.RENDERED_DIR / f"{input_path.stem}.{output_format}"
    csl_path = Path(csl) if csl is not None else config.CSL_STYLE_PATH
    if collapse_citations is None:
        collapse_citations = config.RENDER_COLLAPSE_CITATIONS
    if not csl_path.is_file():
        raise MissingBinary(
            f"CSL style not found at {csl_path}. The IEEE style ships with this "
            "repo at assets/csl/ieee.csl -- pass --csl to point somewhere else, "
            "or see assets/csl/README.md to re-fetch it."
        )

    with tempfile.TemporaryDirectory() as tmp:
        safe_md, safe_bib = _safe_render_inputs(input_path, config.BIB_FILE_PATH, Path(tmp))
        if collapse_citations:
            csl_path = _collapsed_csl(csl_path, Path(tmp))
        cmd = [
            "pandoc", str(safe_md),
            "--standalone",
            # Local image references (`![...](figure.png)`) in the draft are
            # relative to input_path's own directory, not whatever directory
            # this CLI happened to be invoked from. Without this, pandoc's
            # PDF/DOCX writers (which read the image file themselves, unlike
            # the tex writer, which just emits an unverified \includegraphics
            # path) can't find it, and silently replace the image with its
            # alt-text caption instead of erroring -- a wrong-but-successful
            # render that's easy to miss without diffing file sizes.
            "--resource-path", str(input_path.resolve().parent),
            "--variable", f"documentclass={documentclass}",
            "--variable", f"fontsize={fontsize}",
            # Pandoc's own default LaTeX template appends "paper" itself
            # (papersize=a4 -> "...,a4paper,..."); passing "a4paper" here
            # would double up to "a4paperpaper" -- verified empirically
            # against pandoc 3.1.3's default template, not documented
            # anywhere obvious, so don't "fix" this back to "a4paper".
            "--variable", f"papersize={papersize}",
            "--variable", f"geometry:margin={margin}",
            "--citeproc", "--bibliography", str(safe_bib),
            "--csl", str(csl_path),
        ]
        if output_format == "pdf":
            cmd += ["--pdf-engine", "pdflatex"]
        cmd += ["-o", str(out_path)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    return out_path


def main() -> int:
    """CLI entry point -- deliberately independent of scripts/full_pipeline.py.

    That script imports docling/embed/topic_model at module load and
    builds the whole corpus before any stage runs, which drags in the
    multi-GB `.venv-full` for a stage that itself only needs stdlib +
    `src.config` + `src.citation_gate`. Genre skills that just want a
    tex/pdf rendering of a draft should be able to run this with bare
    `python3`, no heavy venv required.
    """
    parser = argparse.ArgumentParser(description="Render a Pandoc-markdown or LaTeX draft to tex/pdf/docx.")
    parser.add_argument("input", help="Path to the draft file (Markdown or LaTeX)")
    parser.add_argument("--format", dest="output_format", default="pdf", help="Output format (default: pdf)")
    parser.add_argument("--documentclass", default="article", help="LaTeX documentclass (default: article)")
    parser.add_argument("--fontsize", default="12pt", help="LaTeX font size (default: 12pt)")
    parser.add_argument(
        "--papersize", default="a4",
        help='LaTeX paper size, without the "paper" suffix pandoc appends itself (default: a4)',
    )
    parser.add_argument("--margin", default="1in", help="Page margin, passed to the geometry package (default: 1in)")
    parser.add_argument(
        "--csl", default=None,
        help=f"CSL style for citations and the bibliography (default: {config.CSL_STYLE_PATH})",
    )
    parser.add_argument(
        "--no-collapse-citations", dest="collapse_citations", action="store_false", default=None,
        help="Render a consecutive run as [3], [4], [5], [6] instead of [3]-[6] "
             "-- i.e. leave the CSL style exactly as it is on disk",
    )
    args = parser.parse_args()

    try:
        out_path = render(
            args.input, args.output_format, args.documentclass,
            args.fontsize, args.papersize, args.margin,
            args.csl, args.collapse_citations,
        )
    except MissingBinary as exc:
        print(f"[missing-binary] {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"[error] pandoc failed: {exc.stderr or exc}")
        return 1

    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Vendored CSL styles

`ieee.csl` is **byte-identical to upstream** and must stay that way. Source:

    https://raw.githubusercontent.com/citation-style-language/styles/master/ieee.csl

    title:   IEEE Reference Guide version 11.29.2023
    updated: 2024-03-27T11:41:27+00:00
    sha256:  b4c7619fc16c45a31e4cc3271eab94ffe83192d3b4c7fc729470a3b459448de3
    license: CC BY-SA 3.0 (http://creativecommons.org/licenses/by-sa/3.0/)

It is vendored rather than fetched at render time because rendering has to
work on a host with no network, and because a style that silently changes
underneath a draft would renumber a document that was already reviewed.

## Why it isn't edited in place

Upstream IEEE does not collapse a run of consecutive citations: `[@a; @b;
@c; @d]` renders `[1], [2], [3], [4]`, not the `[3]–[6]` form the IEEE
Reference Guide itself shows. That needs exactly one attribute --
`collapse="citation-number"` on the `<citation>` element -- which
`src/heavy/render_output.py:_collapsed_csl` injects into a **temp copy** at
render time, the same way `_safe_render_inputs` patches a temp copy of the
bib file for `--`-containing citekeys.

Keeping the file on disk unmodified is what makes it possible to re-fetch
the URL above and `diff` it, or bump to a newer CSL release, without first
having to work out which local edits were deliberate. The one deviation
lives in code, where it is commented and covered by a test
(`tests/test_heavy_render_output.py::TestCollapsedCsl`), instead of being
an invisible diff against upstream.

Render with the unmodified upstream behaviour via
`--no-collapse-citations`.

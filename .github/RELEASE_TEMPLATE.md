# Release notes template

GitHub has no hook that picks this file up automatically -- copy everything
below the horizontal rule into the GitHub Release body by hand when cutting
a release, leaving this preamble behind. The git tag message is not the
release notes. For how the version number itself is chosen, see
[DEVELOPER-AGENTS.md](https://github.com/prasadtalasila/chitragupta/blob/main/DEVELOPER-AGENTS.md),
"Versioning and releases" (absolute, so it still resolves if this preamble
is pasted somewhere by accident).

---

# v<version> -- <YYYY-MM-DD>

## Summary

[Bulleted highlights, written for a reader who wasn't following along
commit-by-commit -- what changed and why it matters, not a raw commit log.]

## What's Changed

- <PR title> by @<author> in <link>

**Full Changelog**: https://github.com/prasadtalasila/chitragupta/compare/v<previous>...v<version>

## In Detail

[Only for a larger release: elaborate the most significant items above under
their own subheadings. Omit this section entirely for a small one.]

## Upgrading

[Only when the release is MAJOR, or otherwise needs an existing user to
change how they invoke or configure the pipeline -- the config key that
moved, the CLI argument that changed shape, whether a `--reparse` or a
re-export of `bibliography.bib` is required. Omit if nothing is needed.]

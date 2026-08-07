---
name: Feature request
about: Suggest an improvement or new capability for this project
title: "[FEATURE]"
labels: enhancement
assignees: ''

---

## Describe the Feature

A clear and concise description of the new feature using the
"As a [...] I want to [...] So That [...]" idiom (see
[explanation](https://www.agilealliance.org/glossary/user-story-template/)).

Ex. As a **thesis author**, I want to **see which corpus papers a draft
failed to cite** so that **a gap in my related work is visible before
review**.

## Problem Statement

A clear and concise description of the problem this addresses. Reference an
existing issue where applicable.

## Proposed Solution

A clear and concise description of the desired outcome. Name which layer it
belongs to -- corpus (deterministic), drafting (generative), enrichment
(optional), or a review aid (see AGENTS.md, "The three layers").

## Alternatives Considered

Any alternative solutions or features that were considered.

## Additional Context

Any other context or screenshots.

## Success Criterion

Checklist:

- [ ] Feature works as described (add more specific checkpoints here)
- [ ] Line and branch coverage stays at 100%
- [ ] No citekey is generated, guessed or rewritten (see SOUL.md's one
      invariant), and no new check is promoted into a gate

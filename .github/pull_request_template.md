# Title

[A succinct, imperative title describing the change's effect, e.g. "Warn and
continue when pandoc fails rendering a provenance report". This becomes the
squashed commit title on `main`; don't add the PR number by hand.]

## Type of Change

- [ ] New feature
- [ ] Bug fix
- [ ] Documentation update
- [ ] Refactoring
- [ ] Security patch
- [ ] Test coverage

## Description

[Why this change, not just what it is -- the motivating problem or gap, and
the reasoning behind the approach taken over the alternatives. Reference an
issue number where one exists.]

## What changed, from the user's point of view

[Bulleted, concrete, from the reader's perspective -- command examples where
relevant, not a restatement of the diff.]

## Impact

[Version bump taken and why (PATCH/MINOR/MAJOR -- see DEVELOPER-AGENTS.md).
New dependencies, changes to an output format or a CLI's argument shape, or
anything an existing user would have to change how they invoke. Write "none"
if there is none.]

## Test plan

What was actually run, not what was intended (see DEVELOPER-AGENTS.md,
"Before claiming a task complete"):

- [ ] Full suite with coverage: `.venv-full/bin/python -m pytest --cov=src
      --cov=scripts --cov-report=term-missing` -- still 100% line and branch
- [ ] `poetry check`
- [ ] At least one real end-to-end smoke test against real dependencies,
      not only mocked unit tests -- [name it here]

## Checklist

- [ ] A failing test was written first, and confirmed failing, for each
      behaviour added or bug fixed.
- [ ] No citekey is generated, guessed or rewritten anywhere in the change
      (see SOUL.md's one invariant).
- [ ] Corresponding documentation updated, with each claim owned by exactly
      one document.

## Additional information

[Anything else reviewers should know.]

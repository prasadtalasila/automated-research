#!/usr/bin/env python3
"""PostToolUse hook: enforce the citekey gate on genre-skill drafts.

AGENTS.md calls python -m src.citation_gate "a gate, not a lint
suggestion" and every genre skill's prose instructs the agent to run it
before presenting a draft -- but until this hook existed, nothing
mechanically enforced that instruction; an agent could just skip the
step. This makes it enforced by the harness: every Write/Edit under
content/drafts/ (.md from survey-writer/tutorial-writer/deep-research,
.tex from thesis-chapter-writer -- see each SKILL.md's "Save the
draft/fragment as content/drafts/<slug>.{md,tex}" step) is gated
automatically, and a failure is surfaced back to Claude as blocking
feedback (not a silent/advisory warning).

Reads the PostToolUse JSON payload on stdin (schema: {"tool_input":
{"file_path": "..."}, ...}). Claude Code's own Write/Edit tools document
file_path as always absolute, and this hook has been proven live against
that -- but a substring match on "/content/drafts/" would silently skip
gating if a payload ever carried a relative "content/drafts/<slug>.md"
instead (no leading slash to match). Repo root is derived from this
script's own fixed location (<repo_root>/.claude/hooks/) rather than from
the target path, so a relative file_path is resolved against it instead
of just being ignored, and containment is checked via resolved path
parts (is_relative_to), not string matching.

Malformed stdin fails open (return 0, no block) rather than crashing:
invalid JSON syntax, valid JSON that isn't an object (e.g. a bare
array), and a "tool_input" that isn't itself an object are all treated
as "no file_path given" -- any of them previously reached a bare
`.get()` call on something that isn't a dict and crashed with an
uncaught AttributeError instead.
"""

import json
import subprocess
import sys
from pathlib import Path

GATED_EXTENSIONS = (".md", ".tex")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # can't identify a target file from this -- fail open, not loud
    if not isinstance(payload, dict):
        return 0  # valid JSON (e.g. a bare array/string/number) but not the expected object shape
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}  # missing, null, or the wrong shape -- same as "no file_path given"
    raw_path = tool_input.get("file_path", "")
    if not raw_path:
        return 0

    repo_root = Path(__file__).resolve().parent.parent.parent
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = repo_root / file_path
    file_path = file_path.resolve()

    drafts_dir = (repo_root / "content" / "drafts").resolve()
    if not file_path.is_relative_to(drafts_dir) or file_path.suffix not in GATED_EXTENSIONS:
        return 0  # not a genre-skill draft -- nothing to gate

    result = subprocess.run(
        ["python3", "-m", "src.citation_gate", str(file_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        reason = (
            "Citation gate FAILED for this draft (AGENTS.md: a hard gate, not "
            "advisory). Fix the offending citekey(s) -- correct the key or "
            "remove the claim -- then this file will be re-checked "
            "automatically on your next write to it.\n\n"
            f"{result.stdout}{result.stderr}"
        )
        print(json.dumps({"decision": "block", "reason": reason}))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
{"file_path": "..."}, ...}). Derives the repo root from the file path
itself (splitting on "/content/drafts/") rather than trusting cwd or an
env var, so this keeps working regardless of what directory the hook
happens to be invoked from.
"""

import json
import subprocess
import sys

MARKER = "/content/drafts/"
GATED_EXTENSIONS = (".md", ".tex")


def main() -> int:
    payload = json.load(sys.stdin)
    file_path = (payload.get("tool_input") or {}).get("file_path", "")

    if MARKER not in file_path or not file_path.endswith(GATED_EXTENSIONS):
        return 0  # not a genre-skill draft -- nothing to gate

    repo_root = file_path.split(MARKER)[0]
    result = subprocess.run(
        ["python3", "-m", "src.citation_gate", file_path],
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

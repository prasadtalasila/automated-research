"""Citation-coverage report: how much of what retrieval surfaced actually
made it into a draft's citations.

`src.retrieval.search()` (and its embedding-based upgrade,
`src.enrich.embed_index.search()`) return candidate sources for a query --
but nothing today reports whether a genre skill actually used them.
A citekey that scored well but never got cited is either a sign the
draft skipped a relevant source, or a sign the query was too broad; a
citekey cited but never surfaced by any of the given queries is not a
problem (it's likely explained by a different query the skill also ran)
but worth showing so the report isn't misread as a gap-finder.

Not a gate -- purely informational, unlike citation_gate.py. This does
not run automatically as part of any genre skill; it is a review aid you
run by hand with whatever queries you want to check coverage against, the
same way scripts/verbatim_check.py is a review aid rather than part of
the automatic citation_gate -> references -> render_output chain.

Stdlib-only (reuses src.retrieval and src.citation_gate.extract_citekeys_from_line,
both already stdlib-only) -- runs with bare `python3`, no venv, same as
citation_gate.py/references.py.

Usage:
    python -m src.citation_coverage <draft.md> --query "topic one" --query "topic two" [--k 5]
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src import retrieval
from src.citation_gate import extract_citekeys_from_line


@dataclass
class CoverageResult:
    candidates: dict[str, str] = field(default_factory=dict)  # citekey -> title
    cited: set[str] = field(default_factory=set)  # every citekey actually cited in the draft

    @property
    def cited_candidates(self) -> set[str]:
        return set(self.candidates) & self.cited

    @property
    def uncited_candidates(self) -> set[str]:
        return set(self.candidates) - self.cited

    @property
    def cited_outside_candidates(self) -> set[str]:
        """Cited, but not surfaced by any of the given queries -- not a
        problem by itself, just outside what this report's queries cover."""
        return self.cited - set(self.candidates)

    @property
    def coverage_pct(self) -> float | None:
        if not self.candidates:
            return None
        return 100.0 * len(self.cited_candidates) / len(self.candidates)


def cited_citekeys(draft_path: Path) -> set[str]:
    keys: set[str] = set()
    for line in draft_path.read_text().splitlines():
        keys.update(extract_citekeys_from_line(line))
    return keys


def compute_coverage(draft_path: Path, queries: list[str], k: int = 5) -> CoverageResult:
    candidates: dict[str, str] = {}
    for query in queries:
        for result in retrieval.search(query, k=k):
            candidates[result.citekey] = result.title
    return CoverageResult(candidates=candidates, cited=cited_citekeys(draft_path))


def format_report(draft_path: Path, queries: list[str], result: CoverageResult) -> str:
    lines = [f"Citation coverage for {draft_path}", f"Queries: {queries}"]

    if result.coverage_pct is None:
        lines.append("No candidates found for any query -- nothing to compare against.")
    else:
        lines.append(
            f"Coverage: {result.coverage_pct:.0f}% "
            f"({len(result.cited_candidates)}/{len(result.candidates)} retrieved candidates cited)"
        )
        if result.uncited_candidates:
            lines.append("Retrieved but not cited:")
            for key in sorted(result.uncited_candidates):
                lines.append(f"  - {key}: {result.candidates[key]}")

    if result.cited_outside_candidates:
        lines.append("Cited but not surfaced by these queries (not necessarily a problem):")
        for key in sorted(result.cited_outside_candidates):
            lines.append(f"  - {key}")

    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft", help="Path to the draft to check")
    parser.add_argument("--query", action="append", required=True, dest="queries",
                         help="A retrieval query to check coverage for (repeatable)")
    parser.add_argument("--k", type=int, default=5, help="Top-k results per query (default: 5)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    result = compute_coverage(Path(args.draft), args.queries, k=args.k)
    print(format_report(Path(args.draft), args.queries, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

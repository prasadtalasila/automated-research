"""Near-duplicate citekey detection: flags likely repeat imports of the
same paper under two different citekeys -- a common Zotero failure mode
(the same item added twice, once per re-export, or once per each of two
overlapping search hits).

Advisory only, exactly like sync.py's existing no-author-metadata
warning -- this never blocks a sync or removes anything. A human still
decides whether two flagged entries are really the same source: a shared
DOI is essentially certain, but a shared title is only a heuristic --
this project's own real bibliography has a `kayla_digital_2023` /
`digital_twin_consortium_digital_2023` pair with an identical title
("Digital Twin Platform Stack Architectural Framework by Digital Twin
Consortium") that turn out to be a blog post and a webinar recording
about the same named report, not the same citable source. This checker
cannot tell that apart from a genuine duplicate and doesn't try to --
it surfaces the pair and leaves the call to the person re-exporting the
bib file.
"""

import re
from collections import defaultdict

from src.bib_reader import Reference


def _normalize_title(title: str) -> str:
    text = re.sub(r"[{}]", "", title).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _normalize_doi(doi: str) -> str:
    text = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def find_duplicates(references: list[Reference]) -> list[list[Reference]]:
    """Groups references that share a normalized DOI or normalized title.

    Only groups spanning 2+ distinct citekeys are returned -- multiple
    field entries under the same citekey (shouldn't happen, bibtexparser
    already requires unique keys) aren't a "duplicate" in the sense this
    check cares about.
    """
    by_doi: dict[str, list[Reference]] = defaultdict(list)
    by_title: dict[str, list[Reference]] = defaultdict(list)
    for ref in references:
        if ref.doi:
            by_doi[_normalize_doi(ref.doi)].append(ref)
        if ref.title:
            by_title[_normalize_title(ref.title)].append(ref)

    seen_groups: set[frozenset[str]] = set()
    groups: list[list[Reference]] = []
    for bucket in (by_doi, by_title):
        for refs in bucket.values():
            distinct = {r.citekey: r for r in refs}
            if len(distinct) < 2:
                continue
            group_key = frozenset(distinct)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            groups.append(list(distinct.values()))
    return groups

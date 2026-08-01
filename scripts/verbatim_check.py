#!/usr/bin/env python3
"""Ad-hoc plagiarism / page-locator helper for reviewing a draft.

Not part of the deterministic pipeline -- a review aid. Two modes:

    verbatim_check.py overlap <draft.md> <citekey> [--n 8]
        report the longest verbatim word-n-gram runs shared between the
        draft's sentences citing <citekey> and that source's parsed text.

    verbatim_check.py locate <citekey> "<phrase>" [more phrases...]
        report which PDF page each phrase (or its distinctive words)
        appears on, for fact-checking page numbers.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config  # noqa: E402 -- needs REPO on sys.path first

BIB = config.BIB_FILE_PATH
PARSED_DIR = config.PARSED_DIR


def bib_entry(citekey):
    if not BIB.exists():
        # papers/bibliography.bib is gitignored, per-host data (see
        # AGENTS.md) -- absent on a fresh clone/CI checkout until someone
        # exports their own. Treat that the same as "citekey not in the
        # bib file" rather than crashing on a raw FileNotFoundError.
        return ""
    text = BIB.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"@\w+\{" + re.escape(citekey) + r",", text)
    if not m:
        return ""
    # Brace-match to the entry's real end rather than stopping at the
    # first "\n}": that sequence occurs *inside* multi-line field values
    # too (an `annote` holding a URL list is the common case here), which
    # truncated the entry mid-way and hid every field after it --
    # including `file`, so 40 papers looked like they had no PDF at all.
    depth = 0
    for i in range(text.index("{", m.start()), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():i + 1]
    return text[m.start():]  # unbalanced braces: hand back what we have


def pdf_path(citekey):
    """The `file` field's attachment format is `Desc:path:mimetype`,
    `;`-separated per attachment -- the same shape src.bib_reader
    parses, and it must be split the same way here.

    Splitting on ':' and taking the first segment that merely *ends in*
    `.pdf` picks the human-readable description, not the path: this
    project's export writes both, as
    `Smith - 2024 - Title.pdf:pdfs/21/Smith - 2024 - Title.pdf:application/pdf`.
    Those two coincide only when the attachment sits directly beside the
    .bib, so the mistake was invisible in a flat fixture directory and
    silently lost 196 of 501 real PDFs -- `locate`/`overlap` then fell
    back to parsed text and reported page 1 for everything.
    """
    entry = bib_entry(citekey)
    m = re.search(r"file = \{(.*?)\},", entry, re.S)
    if not m:
        return None
    # Anchor a relative attachment path to the bib file's own directory,
    # matching src.bib_reader._resolve_pdf_path -- not REPO, which is
    # wrong the moment BIB_FILE points somewhere outside the checked-out
    # repo (a relative path in the file field is only ever relative to
    # wherever the .bib itself lives).
    bib_dir = BIB.resolve().parent
    for attachment in m.group(1).split(";"):
        parts = attachment.split(":")
        if len(parts) < 3:
            continue
        if "pdf" not in parts[-1].lower():
            continue
        p = Path(":".join(parts[1:-1]).strip())
        if not p.is_absolute():
            p = bib_dir / p
        if p.is_file():
            return p
    return None


def pages(citekey):
    """Return list of page texts, 1-indexed by position+1 (PDF page order)."""
    p = pdf_path(citekey)
    if p is None:
        parsed = PARSED_DIR / f"{citekey}.txt"
        if not parsed.exists():
            return []
        # pdftotext leaves stray NUL/control bytes in some files, which
        # makes grep treat them as binary and report nothing. Strip them
        # so a false "no match" can't be mistaken for a real absence.
        raw = parsed.read_text(encoding="utf-8", errors="replace")
        return re.sub(r"[\x00-\x08\x0e-\x1f]", " ", raw).split("\f")
    out = subprocess.run(
        ["pdftotext", "-layout", str(p), "-"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.split("\f")


WORD = re.compile(r"[a-z0-9]+")


def norm(text):
    return WORD.findall(text.lower())


def sentences_citing(draft, citekey):
    """Whole paragraphs mentioning the citekey, not just the citing sentence.

    Paraphrased-but-uncited sentences sitting next to a citation are
    exactly where borrowed wording hides, so compare the whole
    paragraph against the source.
    """
    text = Path(draft).read_text(encoding="utf-8")
    paras = re.split(r"\n\s*\n", text)
    return [re.sub(r"\s+", " ", p) for p in paras if citekey in p]


def cmd_overlap(draft, citekey, n=8):
    src_pages = pages(citekey)
    if not src_pages:
        print(f"no source text for {citekey}")
        return
    grams = {}
    for i, pg in enumerate(src_pages, 1):
        w = norm(pg)
        for j in range(len(w) - n + 1):
            grams.setdefault(tuple(w[j:j + n]), i)
    hits = []
    for s in sentences_citing(draft, citekey):
        w = norm(re.sub(r"\[@[^\]]+\]", "", s))
        run, runs = [], []
        for j in range(len(w) - n + 1):
            g = tuple(w[j:j + n])
            if g in grams:
                run.append((j, grams[g]))
            else:
                if run:
                    runs.append(run)
                run = []
        if run:
            runs.append(run)
        for r in runs:
            start = r[0][0]
            length = r[-1][0] + n - start
            hits.append((length, r[0][1], " ".join(w[start:start + length]), s[:80]))
    hits.sort(reverse=True)
    if not hits:
        print(f"{citekey}: no verbatim run of >= {n} words found")
    for length, pg, frag, ctx in hits[:25]:
        print(f"  [{length} words, pdf p.{pg}] {frag}\n      in: {ctx}...")


def cmd_locate(citekey, *phrases):
    src_pages = pages(citekey)
    print(f"{citekey}: {len(src_pages)} pdf pages")
    for phrase in phrases:
        keys = [w for w in norm(phrase) if len(w) > 3]
        best = []
        for i, pg in enumerate(src_pages, 1):
            w = set(norm(pg))
            score = sum(1 for k in keys if k in w)
            best.append((score / max(len(keys), 1), i))
        best.sort(reverse=True)
        top = ", ".join(f"p.{i} ({s:.0%})" for s, i in best[:4])
        print(f"  {phrase!r}\n      -> {top}")


if __name__ == "__main__":
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "overlap":
        n = 8
        if "--n" in rest:
            k = rest.index("--n")
            n = int(rest[k + 1])
            rest = rest[:k] + rest[k + 2:]
        cmd_overlap(rest[0], rest[1], n)
    elif mode == "locate":
        cmd_locate(*rest)
    else:
        print(__doc__)

#!/usr/bin/env python3
"""Notes upkeep with no model: old log entries leave the pages agents read first.

Runs in the scheduled inbox review job (reviewer/inbox-review-workflow.yaml),
after the review, on its checkout of the notes. Deterministic and idempotent:
a second run on its own output changes nothing. Nothing is dropped: entries
move to monthly archive parts, linked from the page they left.

- log/<project>.md keeps the entries of the last KEEP_DAYS, newest first,
  within LOG_BUDGET bytes; older ones go to log/archive/<project>/YYYY-MM.md,
  newest first, and the log's intro links every part. An entry starts at a
  dated H2 ("## 2026-11-08 ...") outside a code fence, as the session hook and
  the inbox review write them. Once one entry leaves, every older one does
  too, so the page stays one stretch of time.
- A log entry promoted from the inbox once went under a heading of its own,
  left empty above the entry's own dated heading, so the entry read as dated
  by its promotion. Those empty headings are folded into the entries they
  wrap: the entry's heading takes the "(from the inbox ...)" mark.

  VAULT_DIR=<notes checkout> [DRY_RUN=1] [TODAY=YYYY-MM-DD] python3 vault_tidy.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from inbox_review import ENTRY_START, FENCE

KEEP_DAYS = 30
LOG_BUDGET = 40_000  # bytes of entries on a log page
PARTS_LINE = "Older entries, one file per month:"

EMPTY_WRAPPER = re.compile(r"^## \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC (\(from the inbox, restricted session\))\n\s*\n"
                           r"(## \d{4}-\d{2}-\d{2}[^\n]*)", re.M)


def log_entries(text: str) -> tuple[str, list[str]]:
    """(intro, entries): an entry starts at a dated H2 outside a code fence."""
    starts, fence, pos = [], "", 0
    for line in text.splitlines(keepends=True):
        m = FENCE.match(line)
        if m and (not fence or m.group(1)[0] == fence):
            fence = "" if fence else m.group(1)[0]
        elif not fence and ENTRY_START.match(line):
            starts.append(pos)
        pos += len(line)
    if not starts:
        return text, []
    bounds = starts + [len(text)]
    return text[:starts[0]], [text[a:b].rstrip("\n") + "\n" for a, b in zip(bounds, bounds[1:])]


def entry_key(e: str) -> tuple[str, str]:
    m = re.match(r"## (\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", e)
    return (m.group(1), m.group(2) or "") if m else ("", "")


def tidy_log(vault: Path, name: str, today: dt.date) -> tuple[dict[Path, str], list[str]]:
    page = vault / "log" / f"{name}.md"
    text = page.read_text()
    repaired = EMPTY_WRAPPER.sub(r"\2 \1", text)
    intro, entries = log_entries(repaired)
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    keep, moved, size = [], [], 0
    for e in entries:  # newest first, as written
        if entry_key(e)[0] >= cutoff and (not keep or size + len(e.encode()) <= LOG_BUDGET):
            keep.append(e)
            size += len(e.encode())
        else:
            moved.append(e)
    # Once one entry leaves, every older one does too: the page stays one stretch of time.
    if moved:
        first_out = entries.index(moved[0])
        keep, moved = entries[:first_out], entries[first_out:]
    out: dict[Path, str] = {}
    pdir = vault / "log" / "archive" / name
    by_month: dict[str, list[str]] = defaultdict(list)
    for e in moved:
        by_month[entry_key(e)[0][:7] or "undated"].append(e)
    for month, es in by_month.items():
        part = pdir / f"{month}.md"
        _, old = log_entries(part.read_text()) if part.exists() else ("", [])
        every = old + [e for e in es if e not in old]
        out[part] = (f"# {name}: log, {month}\nPart of [[log/{name}]]: entries from {month}, newest first "
                     "(moved here by vault_tidy.py).\n\n" + "\n".join(sorted(every, key=entry_key, reverse=True)))
    months = sorted({p.stem for p in pdir.glob("*.md")} | set(by_month), reverse=True) if (pdir.is_dir() or by_month) else []
    old_intro = intro.rstrip("\n").split("\n")
    intro_lines = [ln for ln in old_intro if not ln.startswith(PARTS_LINE)]
    if months:
        intro_lines.append(PARTS_LINE + " " + " · ".join(f"[[log/archive/{name}/{m}]]" for m in months))
    # Untouched when nothing moved and the links are as they were: no rewrite for spacing alone.
    if moved or intro_lines != old_intro or repaired != text:
        new = "\n".join(intro_lines).rstrip("\n") + "\n\n" + "\n".join(keep)
        out[page] = new.rstrip("\n") + "\n"
    notes = [f"{len(moved)} log entries archived"] if moved else []
    if repaired != text:
        notes.append(f"{len(EMPTY_WRAPPER.findall(text))} empty inbox headings folded into their entries")
    return {p: t for p, t in out.items() if not p.exists() or p.read_text() != t}, notes


def run(vault: Path, today: dt.date, dry_run: bool = False) -> dict:
    changes: dict[Path, str] = {}
    summary: dict[str, list[str]] = {}
    for lg in sorted((vault / "log").glob("*.md")):
        if lg.is_symlink() or lg.name.lower() == "readme.md":
            continue
        out, notes = tidy_log(vault, lg.stem, today)
        changes.update(out)
        if notes:
            summary[f"log/{lg.stem}"] = notes
    if not dry_run:
        for p, t in changes.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(t)
    return {"changed": sorted(p.relative_to(vault).as_posix() for p in changes), "notes": summary}


def main() -> None:
    vault = Path(os.environ.get("VAULT_DIR", "."))
    today = dt.date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else dt.datetime.now(dt.UTC).date()
    json.dump(run(vault, today, dry_run=os.environ.get("DRY_RUN") == "1"), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""md_snapshot_to_manifest — turn a pre-compaction state snapshot (Markdown)
into a memory-anchor manifest that ``cam judge`` can audit.

Many agent frameworks dump a "state snapshot" file right before compaction:
a Markdown document with sections like Plans / Decisions / Progress /
Verification checklist. This converter parses that document into the
verbatim-items manifest format (schemas/manifest.v1.json) so the compaction
can be audited with:

    python3 examples/md_snapshot_to_manifest.py state.md -o manifest.json
    cam judge --before manifest.json --after summary.txt

Section mapping (default, override with --section):

    Plans / 计划 / 下一步计划 / 待办移交          -> todos
    Decisions / 决策 / 🎯 决策                    -> decisions
    Progress / 进度 / 产出与进度                 -> progress
    Rules / 规则                                 -> rules (immutable)
    Verification / 待验证 / Checklist            -> todos (pending)

Design contract preserved: list items are carried over *verbatim* — the
converter never rewrites, summarizes, or normalizes item text (only strips
one level of "- " / "* " bullet markup and whitespace folding is left to
judge). This mirrors the memory-anchor rule: preserver input must be fed
from runtime files, never from a model's paraphrase.

Zero dependencies (stdlib: argparse, json, re, sys).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# section heading -> manifest kind
_SECTION_MAP = {
    "plans": "todos",
    "计划": "todos",
    "下一步计划": "todos",
    "待办": "todos",
    "待办移交": "todos",
    "decisions": "decisions",
    "决策": "decisions",
    "progress": "progress",
    "进度": "progress",
    "产出与进度": "progress",
    "rules": "rules",
    "规则": "rules",
    "verification": "todos",
    "待验证": "todos",
    "checklist": "todos",
}

_ITEM_RE = re.compile(r"^\s*(?:[-*])\s+(.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s*(.*)$")


def parse_snapshot(text: str, section_map: dict[str, str] | None = None) -> dict:
    """Parse a Markdown snapshot into {kind: [item_text, ...]}.

    Items are collected under the nearest preceding heading. A heading whose
    name maps to a kind routes subsequent list items to that kind. Unmapped
    headings route items to ``None`` (skipped).
    """
    mapping = section_map or _SECTION_MAP
    kinds: dict[str, list[str]] = {"rules": [], "todos": [], "decisions": [], "progress": []}
    current: str | None = None
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            title = m.group(1).strip()
            # normalize: strip emoji markers and parenthetical annotations
            norm = re.sub(r"^[^\w\u4e00-\u9fff]{1,3}\s*", "", title)
            norm = re.sub(r"\s*[（(].*?[)）]\s*$", "", norm).strip().lower()
            current = None
            for key, kind in mapping.items():
                if key in norm or norm in key:
                    current = kind
                    break
            continue
        if current is None:
            continue
        m = _ITEM_RE.match(line)
        if m:
            kinds[current].append(m.group(1).strip())
    return kinds


def to_manifest(snapshot_md: str, session_id: str, source_path: str | None = None) -> dict:
    """Convert snapshot Markdown to a manifest.v1 JSON object."""
    kinds = parse_snapshot(snapshot_md)
    manifest: dict = {
        "schema_version": 1,
        "session_id": session_id or "snapshot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "intent": "audit pre-compaction snapshot items after compression",
        "rules": [
            {"rule_id": f"R{i}", "text": t, "immutable": True, "source": source_path}
            for i, t in enumerate(kinds["rules"], 1)
        ],
        "todos": [
            {"todo_id": f"T{i}", "title": t, "status": "pending", "pointer": source_path}
            for i, t in enumerate(kinds["todos"], 1)
        ],
        "decisions": [
            {"decision_id": f"D{i}", "title": t[:48], "decision": t}
            for i, t in enumerate(kinds["decisions"], 1)
        ],
        "progress": [{"step": t} for t in kinds["progress"]],
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("snapshot", help="path to the pre-compaction snapshot Markdown")
    ap.add_argument("-o", "--output", default="-", help="output manifest path ('-' = stdout)")
    ap.add_argument("--session-id", default="", help="session_id to stamp into the manifest")
    ap.add_argument("--section", action="append", default=[], metavar="HEADING=KIND",
                    help="extra section mapping (repeatable), e.g. --section 'Notes=progress'")
    args = ap.parse_args(argv)

    src = Path(args.snapshot)
    if not src.is_file():
        print(f"error: snapshot not found: {src}", file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8")

    extra_map = {}
    for spec in args.section:
        if "=" not in spec:
            print(f"error: --section expects HEADING=KIND, got '{spec}'", file=sys.stderr)
            return 2
        h, k = spec.split("=", 1)
        extra_map[h.strip().lower()] = k.strip().lower()
    mapping = {**_SECTION_MAP, **extra_map}

    manifest = to_manifest(text, args.session_id or src.stem, str(src))
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(payload)
    else:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.output} ({len(json.loads(payload)['rules']) + len(json.loads(payload)['todos']) + len(json.loads(payload)['decisions']) + len(json.loads(payload)['progress'])} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

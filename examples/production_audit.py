#!/usr/bin/env python3
"""production_audit — audit a real pre-compaction snapshot + summary pair.

The workflow that discovered v0.3.3's fixes: an agent framework dumps a
Markdown state snapshot before compacting, then compresses the conversation
into a summary. This example wires that real-world pair into ``cam judge``:

    python3 examples/md_snapshot_to_manifest.py snapshot.md -o manifest.json
    python3 examples/production_audit.py manifest.json summary.txt

It prints the three-state verdict table (verbatim / paraphrased / lost) and
exits 1 when retention drops below --min-retention — a tripwire you can run
after every real compaction.

The bundled ``examples/sample/snapshot.md`` + ``examples/sample/summary.txt``
pair reproduces the audit on sample data (no real session content):
a snapshot whose 12 key items all survive verbatim in the summary.

Zero dependencies (stdlib). Numbers come from the audit itself — no
hard-coded expectations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_anchor.judge import audit_manifest  # noqa: E402
from memory_anchor.models import StateManifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("manifest", help="manifest.json (use md_snapshot_to_manifest to create it)")
    ap.add_argument("summary", help="the compressed context / summary text file")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("--min-retention", type=float, default=None, metavar="PCT",
                    help="exit 1 when retention (verbatim+paraphrased) < PCT")
    args = ap.parse_args(argv)

    manifest = StateManifest.from_json(Path(args.manifest).read_text(encoding="utf-8"))
    after = Path(args.summary).read_text(encoding="utf-8")
    verdicts = audit_manifest(manifest, after)

    total = len(verdicts)
    counts = {"verbatim": 0, "paraphrased": 0, "lost": 0}
    for v in verdicts:
        counts[v.verdict] += 1
    retained = counts["verbatim"] + counts["paraphrased"]
    retention = 100.0 * retained / total if total else 100.0
    verbatim_rate = 100.0 * counts["verbatim"] / total if total else 100.0
    stats = {
        "total": total,
        "verbatim": counts["verbatim"],
        "paraphrased": counts["paraphrased"],
        "lost": counts["lost"],
        "retention": round(retention, 1),
        "verbatim_rate": round(verbatim_rate, 1),
    }

    if args.json:
        print(json.dumps({"stats": stats, "items": [v.__dict__ for v in verdicts]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"audit: {total} items — verbatim {counts['verbatim']}, "
              f"paraphrased {counts['paraphrased']}, lost {counts['lost']}")
        print(f"retention {stats['retention']}% | verbatim {stats['verbatim_rate']}%")
        for v in verdicts:
            if v.verdict != "verbatim":
                print(f"  [{v.verdict}] {v.kind} {v.label[:60]} (sim {v.similarity:.2f})")

    if args.min_retention is not None and retention < args.min_retention:
        print(f"gate FAILED: retention {retention:.1f}% < {args.min_retention}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

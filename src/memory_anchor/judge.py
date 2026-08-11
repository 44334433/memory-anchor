"""judge — audit what a compaction actually preserved.

Given a *before* manifest (the verbatim items you could not afford to lose)
and the *after* text (the compressed context / summary), judge classifies
every tracked item into one of three verdicts:

- ``verbatim``   — item text survives byte-for-byte (after whitespace folding)
- ``paraphrased``— meaning partially retained but the text was rewritten
- ``lost``       — the item is gone

This is the measurement half of memory-anchor: ``compaction_drill`` runs the
experiment, ``cam judge`` audits a compaction that *already happened* (e.g.
the summary your framework just produced).

Rules (``immutable=True``) are graded as the most severe class: a paraphrased
rule is reported as lost-at-L1, because the design contract says rules must
survive verbatim or not at all.

Zero dependencies (stdlib: difflib). Optional ``--min-retention`` turns the
report into a gate: exit 1 when retention drops below the threshold — usable
as a CI / cron tripwire for your compression pipeline.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .models import StateManifest

VERBATIM = "verbatim"
PARAPHRASED = "paraphrased"
LOST = "lost"

# Similarity thresholds on whitespace-folded text.
_VERBATIM_RATIO = 0.98   # effectively byte-for-byte (allows trivial whitespace)
_PARAPHRASE_RATIO = 0.45 # above: partial retention; below: lost


@dataclass
class ItemVerdict:
    kind: str          # rules | todos | decisions | progress
    label: str         # rule_id / title / decision title / step
    text: str          # the verbatim source text
    verdict: str       # verbatim | paraphrased | lost
    similarity: float  # difflib ratio on folded text (1.0 = identical)


def _fold(text: str) -> str:
    """Whitespace folding: compress all runs of whitespace to one space."""
    return " ".join(text.split())


def _similarity(folded_item: str, folded_corpus: str) -> float:
    """Best similarity of the item against any window of the corpus.

    A plain SequenceMatcher.ratio() on the whole corpus is useless for short
    items — a 20-char rule inside a 5000-char summary scores ~0.01 even when
    it is present. Instead we search for the item as a subsequence window.
    """
    if folded_item in folded_corpus:
        return 1.0
    # sliding-window best match (window = item length * 2, step = item length)
    n = len(folded_item)
    if n == 0:
        return 0.0
    step = max(n, 1)
    best = 0.0
    i = 0
    while i < len(folded_corpus):
        win = folded_corpus[i : i + n * 2]
        best = max(best, difflib.SequenceMatcher(None, folded_item, win).ratio())
        i += step
    return best


def _classify(sim: float, immutable: bool) -> str:
    if sim >= _VERBATIM_RATIO:
        return VERBATIM
    if sim >= _PARAPHRASE_RATIO:
        # rules must survive verbatim or not at all (design contract #1):
        # a paraphrased rule is as bad as a lost one at L1.
        return PARAPHRASED if not immutable else LOST
    return LOST


def audit_manifest(manifest: StateManifest, after_text: str) -> List[ItemVerdict]:
    """Classify every tracked item of *manifest* against *after_text*."""
    folded = _fold(after_text)
    verdicts: List[ItemVerdict] = []

    for r in manifest.rules:
        sim = _similarity(_fold(r.text), folded)
        verdicts.append(
            ItemVerdict("rules", r.rule_id, r.text, _classify(sim, r.immutable), round(sim, 4))
        )
    for t in manifest.todos:
        sim = _similarity(_fold(t.title), folded)
        verdicts.append(
            ItemVerdict("todos", t.todo_id, t.title, _classify(sim, False), round(sim, 4))
        )
    for d in manifest.decisions:
        # Provenance is part of the decision: "we decided X" without
        # "because file Y behaved this way on date Z" is a decision that
        # can no longer be challenged. Fold rationale/source/evidence into
        # the matched text so a summary that keeps the conclusion but drops
        # the provenance scores below verbatim (see community feedback:
        # "compaction preserves conclusions but drops provenance").
        provenance = " ".join(x for x in (d.rationale, d.source, d.evidence) if x)
        matched = f"{d.decision} {provenance}".strip()
        sim = _similarity(_fold(matched), folded)
        verdicts.append(
            ItemVerdict("decisions", d.decision_id, d.title, _classify(sim, False), round(sim, 4))
        )
    for p in manifest.progress:
        sim = _similarity(_fold(p.step), folded)
        verdicts.append(
            ItemVerdict("progress", p.step, p.step, _classify(sim, False), round(sim, 4))
        )
    return verdicts


def _summary(verdicts: List[ItemVerdict]) -> dict:
    total = len(verdicts)
    counts = {"verbatim": 0, "paraphrased": 0, "lost": 0}
    for v in verdicts:
        counts[v.verdict] += 1
    retained = counts[VERBATIM] + counts[PARAPHRASED]
    return {
        "total": total,
        "verbatim": counts[VERBATIM],
        "paraphrased": counts[PARAPHRASED],
        "lost": counts[LOST],
        "retention": round(100.0 * retained / total, 1) if total else 100.0,
        "verbatim_rate": round(100.0 * counts[VERBATIM] / total, 1) if total else 100.0,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cam judge",
        description="Audit what a compaction preserved: classify each manifest item "
                    "as verbatim / paraphrased / lost against the after-text.",
    )
    ap.add_argument("--before", required=True, metavar="MANIFEST.json",
                    help="manifest snapshot taken BEFORE compaction")
    ap.add_argument("--after", required=True, metavar="TEXT|'-'",
                    help="compressed context / summary text file; '-' = stdin")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON report on stdout")
    ap.add_argument("--min-retention", type=float, default=None, metavar="PCT",
                    help="gate: exit 1 when retention (verbatim+paraphrased) < PCT")
    ap.add_argument("--min-verbatim", type=float, default=None, metavar="PCT",
                    help="gate: exit 1 when verbatim rate < PCT")
    args = ap.parse_args(argv)

    try:
        manifest = StateManifest.from_json(Path(args.before).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cam judge: cannot load manifest: {e}", file=sys.stderr)
        return 1

    if args.after == "-":
        after_text = sys.stdin.read()
    else:
        try:
            after_text = Path(args.after).read_text(encoding="utf-8")
        except OSError as e:
            print(f"cam judge: cannot read after-text: {e}", file=sys.stderr)
            return 1

    verdicts = audit_manifest(manifest, after_text)
    stats = _summary(verdicts)

    if args.json:
        report = {
            "before": str(args.before),
            "after": str(args.after),
            "stats": stats,
            "items": [
                {"kind": v.kind, "label": v.label, "verdict": v.verdict,
                 "similarity": v.similarity, "text": v.text}
                for v in verdicts
            ],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"== cam judge ==")
        print(f"tracked items     : {stats['total']}")
        print(f"verbatim          : {stats['verbatim']} ({stats['verbatim_rate']}%)")
        print(f"paraphrased       : {stats['paraphrased']}")
        print(f"lost              : {stats['lost']}")
        print(f"retention (v+p)   : {stats['retention']}%")
        print()
        for v in verdicts:
            flag = {"verbatim": "OK ", "paraphrased": "~  ", "lost": "MISS"}[v.verdict]
            print(f"[{flag}] {v.kind}:{v.label} (sim={v.similarity})")
            if v.verdict != VERBATIM:
                print(f"      source: {v.text[:120]}")
        print()
        if stats["lost"]:
            print("NOTE: rules are graded strictly — paraphrased rule == lost (L1 contract).")

    # gates
    if args.min_retention is not None and stats["retention"] < args.min_retention:
        print(f"cam judge: retention {stats['retention']}% < gate {args.min_retention}%",
              file=sys.stderr)
        return 1
    if args.min_verbatim is not None and stats["verbatim_rate"] < args.min_verbatim:
        print(f"cam judge: verbatim {stats['verbatim_rate']}% < gate {args.min_verbatim}%",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

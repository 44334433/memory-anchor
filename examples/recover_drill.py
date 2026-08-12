#!/usr/bin/env python3
"""recover_drill — verify that recovery re-anchors what compaction destroyed.

The mirror image of ``compaction_drill``. That drill measures the *preserve*
side (before compaction). This one measures the *recover* side: compaction has
already happened, the manifest is on disk, and ``recover()`` must re-inject the
verbatim items — including decision provenance — into the compressed context.

    1. You provide the before-manifest (what was tracked) and the after-text
       (the summary your compressor actually produced).
    2. The drill assembles the recovery block and checks every expected item
       — rules, pending todos, decisions *with source/evidence*, progress —
       verbatim inside it.
    3. It prints a per-category retention report and gates the exit code, so
       a recovery regression fails CI instead of silently drifting.

Zero dependencies (stdlib only).

Usage::

    python3 recover_drill.py --manifest m.json --after summary.txt
    python3 recover_drill.py --manifest m.json --input long.txt --compressor "cmd" --min-recovered 90
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_anchor.models import StateManifest  # noqa: E402
from memory_anchor.recovery import RecoveryInjector  # noqa: E402


def expected_items(manifest: StateManifest) -> list[tuple[str, str]]:
    """(category, verbatim text) pairs the recovery block must re-anchor.

    Rules: full text. Todos: title (done ones are intentionally filtered —
    finished work must not resurrect). Decisions: title + decision + rationale
    + *source + evidence* (provenance is part of the decision, v0.3.1).
    Progress: step + pending verifications + artifacts.
    """
    items: list[tuple[str, str]] = []
    for r in manifest.rules:
        items.append(("rules", r.text))
    for t in manifest.todos:
        if t.status != "done":
            items.append(("todos", t.title))
    for d in manifest.decisions:
        if d.status != "superseded":
            core = f"{d.title}: {d.decision}"
            if d.rationale:
                core += f" (why: {d.rationale})"
            if d.source:
                core += f" (source: {d.source})"
            if d.evidence:
                core += f" (evidence: {d.evidence})"
            items.append(("decisions", core))
    for p in manifest.progress:
        items.append(("progress", p.step))
        for pv in p.pending_verification:
            items.append(("progress", pv))
        for art in p.artifacts:
            items.append(("progress", art))
    return items


def run_external_compressor(command: str, text: str) -> str:
    proc = subprocess.run(
        command, shell=True, input=text, capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        sys.exit(f"compressor failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="before-manifest JSON naming the verbatim key items")
    ap.add_argument("--after", default="", help="compressed/summary text file (post-compaction context)")
    ap.add_argument("--input", default="", help="alternative: original long context, compressed here")
    ap.add_argument("--compressor", default="", help="external compressor command (stdin->stdout)")
    ap.add_argument("--budget", type=int, default=2000, help="recovery block token budget")
    ap.add_argument("--min-recovered", type=float, default=0.0, help="exit 1 if recovery rate falls below this %%")
    args = ap.parse_args()

    manifest = StateManifest.from_json(Path(args.manifest).read_text(encoding="utf-8"))
    expected = expected_items(manifest)
    if not expected:
        sys.exit("manifest has no rules/todos/decisions/progress items to recover")

    if args.after:
        after = Path(args.after).read_text(encoding="utf-8")
        compressor_name = f"--after {args.after}"
    elif args.input:
        text = Path(args.input).read_text(encoding="utf-8")
        if args.compressor:
            after = run_external_compressor(args.compressor, text)
            compressor_name = args.compressor
        else:
            sys.exit("--input requires --compressor (the whole point is using your real summarizer)")
    else:
        sys.exit("provide --after <summary.txt> or --input <long.txt> --compressor <cmd>")

    block = RecoveryInjector().build_recovery_block(manifest, token_budget=args.budget)
    if not block:
        print("verdict: recovery block is EMPTY — nothing will be re-anchored")
        return 1

    # Verbatim presence check per category (whitespace-folded substring match).
    norm = block.replace("\n", " ")
    found: dict[str, int] = {}
    total: dict[str, int] = {}
    missing: list[str] = []
    for cat, text in expected:
        total[cat] = total.get(cat, 0) + 1
        if text.replace("\n", " ") in norm:
            found[cat] = found.get(cat, 0) + 1
        else:
            missing.append(f"[{cat}] {text[:120]}")

    f_all = sum(found.values())
    t_all = sum(total.values())
    rate = 100.0 * f_all / t_all

    print("== recover drill ==")
    print(f"after-text         : {compressor_name} ({len(after)} chars)")
    print(f"recovery block     : {len(block)} chars (~{max(len(block) // 4, 1)} tokens, budget {args.budget})")
    print(f"expected items     : {t_all}")
    print()
    for cat in ("rules", "todos", "decisions", "progress"):
        f, t = found.get(cat, 0), total.get(cat, 0)
        mark = "OK " if f == t else "!! "
        print(f"  {mark}{cat:<10}: {f}/{t} verbatim ({100.0 * f / max(t, 1):.0f}%)")
    print()
    print(f"verdict: recovery rate {rate:.0f}% ({f_all}/{t_all})")
    if missing:
        print("missing items:")
        for m in missing[:10]:
            print(f"  - {m}")
    if rate < args.min_recovered:
        print(f"gate FAILED: {rate:.0f}% < {args.min_recovered:.0f}% (--min-recovered)")
        return 1
    if missing:
        return 1
    print("gate PASSED: every expected item re-anchored verbatim")
    return 0


if __name__ == "__main__":
    sys.exit(main())

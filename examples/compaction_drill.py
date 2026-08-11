#!/usr/bin/env python3
"""compaction_drill — measure what compaction destroys, and what memory-anchor saves.

A reproducible before/after experiment:

    1. You provide a long context file (any prose) and a manifest JSON that
       names the *verbatim* items (rules / todos / decisions / progress) you
       cannot afford to lose.
    2. The drill runs the context through a summarizer/compressor — by default
       a built-in extractive one, or any external command you supply via
       ``--compressor`` (stdin in, stdout out).  Use your real compressor for
       a dogfood run, e.g.::

           python3 compaction_drill.py --input long.txt --manifest m.json \\
               --compressor "python3 /path/to/your/compressor.py"

    3. Control group: key items that survive the compressor on their own.
       Treatment group: compressor output + memory-anchor recovery block.
    4. It prints a quantified retention report.

Zero dependencies (stdlib only).
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


def extractive_compress(text: str, ratio: float = 0.35) -> str:
    """Default compressor: keep the first sentence of each paragraph.

    This is deliberately *dumb* — real summarizers lose detail in more
    interesting ways, but this one is deterministic and dependency-free, so
    the drill runs anywhere and the numbers are reproducible.
    """
    kept: list[str] = []
    for para in text.split("\n\n"):
        sentences = [s.strip() for s in para.replace("\n", " ").split("。") if s.strip()]
        if sentences:
            kept.append(sentences[0])
    out = "。".join(kept)
    return out[: max(int(len(text) * ratio), 200)]


def run_external_compressor(command: str, text: str) -> str:
    proc = subprocess.run(
        command, shell=True, input=text, capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        sys.exit(f"compressor failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return proc.stdout


def key_items(manifest: StateManifest) -> list[str]:
    """Verbatim source text of every tracked item (no display prefixes — the
    compressor output and the recovery block both contain the raw text)."""
    items: list[str] = []
    items += [r.text for r in manifest.rules]
    items += [t.title for t in manifest.todos]
    items += [d.decision for d in manifest.decisions]
    items += [p.step for p in manifest.progress]
    return items


def expected_items(manifest: StateManifest) -> list[str]:
    """Items the recovery block *should* re-inject: done todos and superseded
    decisions are intentionally filtered (finished work must not resurrect).
    """
    items: list[str] = []
    items += [r.text for r in manifest.rules]
    items += [t.title for t in manifest.todos if t.status != "done"]
    items += [d.decision for d in manifest.decisions if d.status != "superseded"]
    items += [p.step for p in manifest.progress]
    return items


def retention(needles: list[str], haystack: str) -> tuple[int, int]:
    """How many key items survive verbatim (substring match, normalized)."""
    norm = haystack.replace("\n", " ")
    found = sum(1 for n in needles if n.replace("\n", " ") in norm)
    return found, len(needles)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="long context text file (what gets compressed)")
    ap.add_argument("--manifest", required=True, help="manifest JSON naming the verbatim key items")
    ap.add_argument("--compressor", default="", help="external compressor command (stdin->stdout); "
                                                     "default: built-in extractive summarizer")
    ap.add_argument("--ratio", type=float, default=0.35, help="built-in compressor output ratio")
    ap.add_argument("--budget", type=int, default=2000, help="recovery block token budget")
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    manifest = StateManifest.from_json(Path(args.manifest).read_text(encoding="utf-8"))
    items = key_items(manifest)
    expected = expected_items(manifest)
    if not items:
        sys.exit("manifest has no rules/todos/decisions/progress items to track")
    skipped = len(items) - len(expected)

    if args.compressor:
        compressed = run_external_compressor(args.compressor, text)
        compressor_name = args.compressor
    else:
        compressed = extractive_compress(text, ratio=args.ratio)
        compressor_name = f"built-in extractive (ratio={args.ratio})"

    block = RecoveryInjector().build_recovery_block(manifest, token_budget=args.budget)
    recovered = compressed + "\n\n" + block

    c_found, c_total = retention(items, compressed)
    t_found, _ = retention(items, recovered)
    e_found, _ = retention(expected, recovered)

    print(f"== compaction drill ==")
    print(f"context size      : {len(text)} chars")
    print(f"compressor        : {compressor_name}")
    print(f"compressed size   : {len(compressed)} chars ({100.0 * len(compressed) / max(len(text), 1):.1f}%)")
    print(f"key items tracked : {c_total} (rules/todos/decisions/progress)"
          + (f"; {skipped} filtered by design (done/superseded never resurrect)" if skipped else ""))
    print()
    print(f"control (compressor alone)   : {c_found}/{c_total} items survived "
          f"({100.0 * c_found / c_total:.0f}%)")
    print(f"treatment (+ memory-anchor)  : {t_found}/{c_total} items survived "
          f"({100.0 * t_found / c_total:.0f}%)")
    lost = c_total - c_found
    print(f"items rescued by memory-anchor: {t_found - c_found} of {lost} lost "
          f"({100.0 * (t_found - c_found) / max(lost, 1):.0f}%)")
    print()
    if e_found < len(expected):
        print("NOTE: recovery block missing expected items — token-trimmed (raise --budget)")
        return 1
    print(f"verdict: recovery block re-anchors {e_found}/{len(expected)} expected items verbatim "
          f"(+ {t_found - e_found} intentionally filtered done/superseded items).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

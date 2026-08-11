#!/usr/bin/env python3
"""Dogfood: run a real rule-based compressor + cam judge on a real briefing.

Design-contract compliant: every manifest item is a *verbatim* snippet lifted
from the briefing (preserve() must be fed from source text, not from a
paraphrase — that is memory-anchor's own contract #2).

Pipeline:
  1. lift 8 key sentences verbatim from the briefing -> manifest
  2. compress the briefing with a local compressor script
     (path via --compressor <path> or $COMPRESSOR, default: none — skip)
  3. audit compressed output with cam judge -> measure verbatim retention
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_anchor.models import RuleItem, StateManifest, TodoItem

BASE = Path("/tmp/judge-dogfood")
CTX = BASE / "context.txt"

# Verbatim sentences lifted from the source briefing (line: text)
LIFTED = [
    # rules (things the agent must keep operating by)
    ("R1", "Meta 发布 Muse Glimmer：30B 开源\"常驻本地 Agent\"模型"),
    ("R2", "Claude 研究版把黎曼 ζ 零点\"临界线上比例\"下界从 41.6% 提到 67.2%"),
    ("R3", "Agent 记忆层正从\"短期上下文\"走向\"分层持久化\""),
    # todos (work items)
    ("T1", "将\"手动标记弃用\"升级为\"可逆逐出 + verbatim 归档 + 冲突状态\"机制"),
    ("T2", "Agent 行业正朝\"长期运行\"演进，本地记忆层成为标配"),
    # decisions (verbatim substrings incl. markdown markers as in source)
    ("D1", '**忘什么、何时忘、怎么可逆地忘**'),
    ("D2", 'AI 产出的可信度竞争从"结果"转向"验证链"'),
]


def main() -> int:
    ctx_text = CTX.read_text(encoding="utf-8")
    manifest = StateManifest(
        session_id="dogfood-demo",
        intent="demo digest: key points + analysis + trends",
        rules=[RuleItem(rule_id=k, text=v) for k, v in LIFTED[:3]],
        todos=[TodoItem(todo_id=k, title=v) for k, v in LIFTED[3:5]],
        decisions=[],
        progress=[],
    )
    mf = BASE / "manifest.json"
    mf.write_text(manifest.to_json(), encoding="utf-8")

    # sanity: every lifted item must actually appear in the source verbatim
    folded_src = " ".join(ctx_text.split())
    for k, v in LIFTED:
        assert " ".join(v.split()) in folded_src, f"{k} not verbatim in source!"

    # Real compressors, two regimes:
    #  A) conservative rule-based compressor (keeps facts, drops filler)
    #  B) extractive (aggressive first-sentence summarizer — loses detail)
    regimes = []
    compressor = os.environ.get("COMPRESSOR", "")
    if compressor:
        proc = subprocess.run(
            ["python3", compressor, str(CTX)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            print(f"compressor failed: {proc.stderr[:500]}", file=sys.stderr)
            return 1
        regimes.append(("rule-based compressor", proc.stdout))

    from compaction_drill import extractive_compress
    regimes.append(("built-in extractive (35%)", extractive_compress(ctx_text, ratio=0.35)))

    for name, compressed in regimes:
        after = BASE / "after.txt"
        after.write_text(compressed, encoding="utf-8")
        print(f"== regime: {name} — {len(ctx_text)} -> {len(compressed)} chars "
              f"({100.0*len(compressed)/len(ctx_text):.0f}%) ==")
        p = subprocess.run(
            [sys.executable, "-m", "memory_anchor.cli", "judge",
             "--before", str(mf), "--after", str(after)],
            capture_output=True, text=True,
            env={**__import__("os").environ,
                 "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
        )
        print(p.stdout)
        if p.stderr:
            print(p.stderr, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

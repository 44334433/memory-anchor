#!/usr/bin/env python3
"""Dogfood: run a real rule-based compressor + cam judge on a real news digest.

Design-contract compliant: every manifest item is a *verbatim* snippet lifted
from the source article (preserve() must be fed from source text, not from a
paraphrase — that is memory-anchor's own contract #2).

Pipeline:
  1. lift 7 key sentences verbatim from the article -> manifest
     (5 tracked: 3 rules + 2 todos; the remaining 2 stay as sanity checks)
  2. compress the article with a local compressor script
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

# Sample source text used only when no real context file exists yet.
# Every LIFTED line appears verbatim below (contract #2: preserve must be fed
# from source text, not from a paraphrase). Replace with your own context for
# a real dogfood run.
_SAMPLE_SOURCE = """用户压缩上下文（示例片段，用于复现；请替换为你自己的真实上下文）

Meta 发布 Muse Glimmer：30B 开源"常驻本地 Agent"模型，主打端侧长期驻留与隐私保护，
社区反响热烈。同日，Claude 研究版把黎曼 ζ 零点"临界线上比例"下界从 41.6% 提到 67.2%，
这是数论领域近年的重大进展。分析人士指出，Agent 记忆层正从"短期上下文"走向"分层持久化"，
多家厂商开始把长期记忆作为 Agent 的标配能力。

行业观察：Agent 行业正朝"长期运行"演进，本地记忆层成为标配；对遗忘机制的讨论升温——
"忘什么、何时忘、怎么可逆地忘"成为设计核心问题——**忘什么、何时忘、怎么可逆地忘**的取舍直接决定记忆层的可信度。同时，AI 产出的可信度竞争从"结果"转向"验证链"，
谁能让每一步决策可复核，谁就赢得开发者信任。

行动项：将"手动标记弃用"升级为"可逆逐出 + verbatim 归档 + 冲突状态"机制，并纳入下版本路线图。
"""

def ensure_context() -> Path:
    """Return the context file, generating a sample on first run.

    Zero-friction reproduction: README's `Reproduce:` command must not fail on
    a fresh checkout. The sample is clearly labelled — swap in your own
    compressed context for a real dogfood run.
    """
    if not CTX.exists():
        BASE.mkdir(parents=True, exist_ok=True)
        CTX.write_text(_SAMPLE_SOURCE, encoding="utf-8")
        print(f"[judge_dogfood] no context at {CTX} — wrote sample context "
              f"(swap in your own for a real dogfood run)")
    return CTX

# Verbatim sentences lifted from the source article (line: text)
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
    ctx_text = ensure_context().read_text(encoding="utf-8")
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

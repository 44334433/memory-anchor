# memory-anchor

**面向 LLM agent 的压缩感知记忆层。** 零依赖（纯标准库）Python 库：在上下文压缩前保全四条关键清单——行为规则、待办事项、带理由的决策、待验证路径——并在压缩后**逐字**重新注入。

[English README](./README.md)

## 问题

长会话 agent 最终会触达上下文上限，把早期轮次压缩成摘要。摘要模型（尤其快速便宜的 flash 模型）会抹平细节：

- 行为规则被转述或丢弃（行为漂移）
- 待办事项消失（"我做到哪了？"）
- 决策理由被改写（已定问题被重新争论）
- 待验证路径丢失（未验证完的工作被当成完成上报）

记忆系统（mem0、Letta、other memory systems…）记住的是"事实"；没有一个能保证**本会话的规则与工作状态**在压缩后逐字节存活。

## memory-anchor 做什么

```
preserve(ctx) ──► manifest（四条清单，逐字）──► [摘要器运行] ──► recover(ctx) ──► 恢复块注入消息头部
```

- **StateManifest** — 规则/待办/决策/进度 + 恢复指针，JSON 序列化（见 `schemas/manifest.v1.json`），增量 `merge()`：已完成的待办不复活、被取代的决策不重现、面包屑去重。
- **MemoryStore** — 原子写（tmp+rename）本地 JSON 持久化、按会话索引、加载最新合并版。
- **RecoveryInjector** — 纯函数恢复块组装。L1（不可变规则）**永不裁剪**；裁剪顺序：指针 → 低优先级 → 高优先级。
- **CompactableMemory** — 两行接入的门面：

```python
from pathlib import Path
from memory_anchor import CompactableMemory

mem = CompactableMemory(base_dir=Path(".memory"))
mem.preserve(ctx)                              # 压缩前
messages = mem.recover(ctx, messages, summary) # 压缩后
```

## 安装与测试

```bash
pip install -e .        # 零依赖
pytest                  # models / store / recovery / 演示闭环
```

## CLI（`cam`）

面向 cron 任务、shell 管道与框架钩子的可脚本化压缩工作流——无需写 Python：

```bash
# 压缩前：快照必须存活的内容
cam before my-session \
  --rule "R1|绝不转述行为规则|100" \
  --todo "发布 v0.2|pending|跑 drill" \
  --decision "压缩器|先用提取式|零依赖"

# 压缩后：重新注入恢复块
cam after my-session --messages messages.json --budget 2000

# 诊断
cam status  my-session   # 条目统计 + manifest 文件
cam verify  my-session   # schema 校验（退出码 0/1）
cam judge --before m.json --after summary.txt   # 审计一次压缩（v0.3）
```

- `--rule ID|TEXT|PRIORITY`、`--todo TITLE|STATUS|NEXT`、`--decision TITLE|DECISION|WHY`、`--progress STEP|ARTIFACT`、`--pointer ...`——或 `--manifest file.json` 加载完整 manifest（载入时做 schema 校验）。
- `--messages -` 从 stdin 读消息 JSON、把恢复后的列表写到 stdout——管道友好。
- 退出码 0=成功，1=失败；数据走 stdout，错误走 stderr。

## 实测：压缩到底毁掉了什么（compaction_drill）

`examples/compaction_drill.py` 是可复现的前后对照实验：给定一段长上下文，经压缩器（内置提取式，或 `--compressor` 指定的任意外部命令）压缩，量化关键条目的逐字存活率——有 memory-anchor 与没有的对比。

对 8KB 中文样例文本（10 条追踪项：规则/待办/决策/进度）的真实运行：

| 压缩器 | 上下文 | 对照组（仅压缩器） | 实验组（+ memory-anchor） |
|---|---|---|---|
| 内置提取式（35%） | 8,035 字符 | 8/10 存活（80%） | **10/10（100%）** |
| 规则压缩器（保守） | 8,035 字符 | 9/10 存活（90%） | **10/10（100%）** |

复现：

```bash
python3 examples/compaction_drill.py --input context.txt --manifest m.json
python3 examples/compaction_drill.py --input context.txt --manifest m.json \
    --compressor "python3 /path/to/your/compressor.py"
```

注意：已完成的待办与被取代的决策**有意不重新注入**（\"已办不复活\"）——报告会把它与 token 裁剪区分开。

## 审计已发生的压缩（`cam judge`）

`compaction_drill` 做实验；`cam judge` 审计**已经发生**的压缩（比如你的框架刚产出的摘要）。输入压缩前 manifest 与压缩后文本，它把每条追踪项判定为 `verbatim`（逐字存活）/ `paraphrased`（被改写）/ `lost`（丢失）——基于空白折叠模糊匹配（stdlib `difflib`，滑动窗口最佳匹配，短条目也能在大摘要中找到）：

```bash
cam judge --before m.json --after summary.txt              # 人类可读报告
cam judge --before m.json --after summary.txt --json       # 机器可读报告
cam judge --before m.json --after summary.txt --min-verbatim 90   # CI 门禁（低于则退出 1）
```

规则按最严格标准评分（设计契约 #1）：被改写的规则视同丢失——规则要么逐字存活要么不存在。`--min-retention` / `--min-verbatim` 把报告变成压缩管线的绊线（cron / CI）：保留率低于阈值即退出码 1。

真实 dogfood 运行（8 条关键项逐字摘录，两个真实压缩器）：

| 压缩器 | 体积 | 逐字存活 | 丢失 |
|---|---|---|---|
| 规则压缩器（保守） | 100% | 5/5（100%） | 0 |
| 内置提取式（35%） | 35% | 3/5（60%） | 2（规则 R2/R3） |

保守压缩器保留一切；激进压缩器静默丢掉两条被追踪的规则——这正是 `judge` 存在的意义：让损失可见。复现：`python3 examples/judge_dogfood.py`。

## 设计契约

1. **要么逐字，要么没有。** 规则、决策、验证路径以原文存储并按原文注入，不做任何转述。摘要模型无法精确复现的内容，就不该出现在摘要里。
2. **结构化状态，而非模型转述。** `preserve()` 必须从运行时变量/文件（待办列表、配置、git 状态）取数，绝不依赖摘要模型对对话的转述。
3. **恢复是被动的。** 注入的恢复块只是参考资料，不指示 agent 自动恢复旧工作——除非最新用户消息要求继续（latest-message-wins）。

## Roadmap

- **v0.1** — 核心类、JSON Schema、演示闭环、CI、双语文档
- **v0.2** — CLI（`cam before/after/status/verify`）、compaction drill（实测保留率实验）
- **v0.3（当前）** — cam judge 压缩审计（verbatim/paraphrased/lost 分类、JSON 报告、CI 门禁）、真实压缩器 dogfood 实测
- **v0.3.1** — 决策 provenance：决策携带 `source` + `evidence`（来源与依据），`cam judge` 把 provenance 计入评分——只留结论丢掉"为什么"的摘要不再算逐字存活
- **v0.3.2（当前）** — 恢复侧闭环：恢复块现在把 `source` + `evidence` 随决策一起逐字重注入；新增 `examples/recover_drill.py` 恢复演练（规则/待办/带 provenance 决策/进度逐类验证+退出码门禁）。修复由演练实证驱动：修复前带 provenance 决策恢复率 50%，修复后 100%
- **v0.4** — 框架适配器（LangChain / Claude Code / OpenHands…）、SQLite 后端、可选 LLM 语义保留评估

## 相关项目

- 其他压缩器负责"压"，memory-anchor 负责"压完记得"——互补而非竞争。
- mem0 / Letta / other memory systems 解决长期记忆；memory-anchor 解决**压缩交接**。

## License

MIT

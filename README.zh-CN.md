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

## 设计契约

1. **要么逐字，要么没有。** 规则、决策、验证路径以原文存储并按原文注入，不做任何转述。摘要模型无法精确复现的内容，就不该出现在摘要里。
2. **结构化状态，而非模型转述。** `preserve()` 必须从运行时变量/文件（待办列表、配置、git 状态）取数，绝不依赖摘要模型对对话的转述。
3. **恢复是被动的。** 注入的恢复块只是参考资料，不指示 agent 自动恢复旧工作——除非最新用户消息要求继续（latest-message-wins）。

## Roadmap

- **v0.1（当前）** — 核心类、JSON Schema、演示闭环、CI、双语文档
- **v0.2** — 框架适配器（LangChain / Claude Code / OpenHands…）、CLI（`cam before-compact` / `cam after-compact` / `cam status` / `cam verify`）
- **v0.3** — SQLite 后端、可选 LLM judge 压缩质量评估

## 相关项目

- 其他压缩器负责"压"，memory-anchor 负责"压完记得"——互补而非竞争。
- mem0 / Letta / other memory systems 解决长期记忆；memory-anchor 解决**压缩交接**。

## License

MIT

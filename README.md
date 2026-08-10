# memory-anchor

**Compaction-aware memory for LLM agents.** Zero-dependency (pure stdlib) Python
library that preserves what context compression destroys — governing rules,
pending todos, decisions with rationale, and verification paths — and
re-injects them verbatim after compaction.

[中文版 README](./README.zh-CN.md)

## The Problem

LLM agents that run long sessions eventually hit context limits and compress
old turns into a summary. Summarizers — especially fast/cheap flash models —
flatten details:

- governing rules get paraphrased or dropped (behavioral drift)
- pending todos vanish ("what was I doing?")
- decision rationale gets rewritten (the team re-litigates settled questions)
- verification paths disappear (half-verified work gets reported as done)

Memory systems (mem0, Letta, other memory systems…) remember *facts*; none of them
guarantee that the *rules and work state governing this session* survive a
compaction byte-for-byte.

## What memory-anchor does

```
preserve(ctx) ──► manifest (4 lists, verbatim) ──► [summarizer runs] ──► recover(ctx) ──► recovery block injected at head of messages
```

- **StateManifest** — rules / todos / decisions / progress + recovery pointers,
  serialized to JSON (see `schemas/manifest.v1.json`), with incremental
  `merge()` semantics: done todos never resurrect, superseded decisions never
  reappear, breadcrumbs dedup.
- **MemoryStore** — atomic (tmp+rename) local JSON persistence, per-session
  indexing, load-latest-merged.
- **RecoveryInjector** — pure-function recovery block assembly. L1 (immutable
  rules) is *never* trimmed; trimming order is pointers → low-priority → high.
- **CompactableMemory** — two-line facade:

```python
from pathlib import Path
from memory_anchor import CompactableMemory

mem = CompactableMemory(base_dir=Path(".memory"))
mem.preserve(ctx)                              # before compaction
messages = mem.recover(ctx, messages, summary) # after compaction
```

## Install & test

```bash
pip install -e .        # zero dependencies
pytest                  # models / store / recovery / demo closure
```

## Design contract

1. **Verbatim or nothing.** Rules, decisions and verification paths are stored
   as exact text and re-injected without paraphrase. If a summarizer can't
   reproduce it exactly, it should not be in the summary at all.
2. **Structured state, not model retelling.** `preserve()` must be fed from
   runtime variables/files (todos list, config, git status), never from a
   summary model's paraphrase of the conversation.
3. **Recovery is passive.** The injected block is reference material; it does
   not instruct the agent to resume old work unless the latest user message
   asks for it (latest-message-wins).

## Roadmap

- **v0.1 (this)** — core classes, JSON schema, demo closure, CI, bilingual docs
- **v0.2** — framework adapters (LangChain / Claude Code / OpenHands…), CLI
  (`cam before-compact` / `cam after-compact` / `cam status` / `cam verify`)
- **v0.3** — SQLite backend, optional LLM judge for compression quality

## Related work

- Other context compressors handle the compression; memory-anchor
  remembers what compression forgets. Complementary, not competing.
- mem0 / Letta / other memory systems solve *long-term memory*; memory-anchor solves the
  *compaction handoff*.

## License

MIT

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

## CLI (`cam`)

Scriptable compaction workflow for cron jobs, shell pipelines and framework
hooks — no Python required:

```bash
# before compaction: snapshot what must survive
cam before my-session \
  --rule "R1|never paraphrase governing rules|100" \
  --todo "ship v0.2|pending|run the drill" \
  --decision "compressor|use extractive first|zero deps"

# after compaction: re-inject the recovery block
cam after my-session --messages messages.json --budget 2000

# diagnostics
cam status  my-session   # counts + manifest files
cam verify  my-session   # schema check (exit 0/1)
```

- `--rule ID|TEXT|PRIORITY`, `--todo TITLE|STATUS|NEXT`, `--decision TITLE|DECISION|WHY`,
  `--progress STEP|ARTIFACT`, `--pointer ...` — or `--manifest file.json` for a
  full manifest (schema-validated on load).
- `--messages -` reads messages JSON from stdin and writes the recovered list
  to stdout — pipe-friendly.
- Exit code 0 = ok, 1 = failure; data on stdout, errors on stderr.

## Measured: what compaction destroys (compaction_drill)

`examples/compaction_drill.py` is a reproducible before/after experiment. It
takes a long context, runs it through a compressor (built-in extractive, or
any external command via `--compressor`), and measures how many tracked key
items survive verbatim — with and without memory-anchor.

Real run against 8 KB of Chinese-language sample text
(Chinese, 10 tracked items: rules / todos / decisions / progress):

| compressor | context | control (compressor alone) | treatment (+ memory-anchor) |
|---|---|---|---|
| built-in extractive (35%) | 8,035 ch | 8/10 survived (80%) | **10/10 (100%)** |
| rule-based compressor (conservative) | 8,035 ch | 9/10 survived (90%) | **10/10 (100%)** |

Reproduce:

```bash
python3 examples/compaction_drill.py --input context.txt --manifest m.json
python3 examples/compaction_drill.py --input context.txt --manifest m.json \
    --compressor "python3 /path/to/your/compressor.py"
```

Note: done todos and superseded decisions are *intentionally* not re-injected
(``done work must not resurrect``) — the report separates these from
token-trimming.

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

- **v0.1** — core classes, JSON schema, demo closure, CI, bilingual docs
- **v0.2 (this)** — CLI (`cam before/after/status/verify`), compaction drill
  (measured retention experiment)
- **v0.3** — framework adapters (LangChain / Claude Code / OpenHands…), SQLite
  backend, optional LLM judge for compression quality

## Related work

- Other context compressors handle the compression; memory-anchor
  remembers what compression forgets. Complementary, not competing.
- mem0 / Letta / other memory systems solve *long-term memory*; memory-anchor solves the
  *compaction handoff*.

## License

MIT

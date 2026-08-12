"""memory-anchor: compaction-aware memory layer for LLM agents.

Top-level facade. Typical usage (2 lines):

    mem = CompactableMemory(base_dir=Path(".memory"))
    mem.preserve(ctx)                                  # before compaction
    messages = mem.recover(ctx, messages, summary)     # after compaction
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .models import (
    DecisionItem,
    ProgressItem,
    RuleItem,
    StateManifest,
    TodoItem,
)
from .recovery import RecoveryInjector
from .store import MemoryStore

__version__ = "0.3.2"

__all__ = [
    "CompactableMemory",
    "MemoryStore",
    "RecoveryInjector",
    "StateManifest",
    "RuleItem",
    "TodoItem",
    "DecisionItem",
    "ProgressItem",
]


class CompactableMemory:
    """One-line top-level API:

        mem = CompactableMemory(base_dir=Path(".memory"))
        mem.preserve(ctx)                       # = on_before_compact
        messages = mem.recover(ctx, messages, compact_summary)
    """

    def __init__(
        self,
        base_dir: Path = Path(".memory"),
        store: Optional[MemoryStore] = None,
        injector: Optional[RecoveryInjector] = None,
    ):
        self.store = store or MemoryStore(base_dir)
        self.injector = injector or RecoveryInjector(self.store)

    def preserve(self, ctx) -> StateManifest:
        """Snapshot current state into a manifest and persist it.

        *ctx* must expose ``session_id`` (and optionally ``intent`` and
        ``rules``/``todos``/``decisions``/``progress`` lists of the
        corresponding item types). This is the hook a framework adapter
        implements with its *structured* runtime state — never via a
        summary model's paraphrase.
        """
        manifest = StateManifest(
            session_id=getattr(ctx, "session_id", "") or "default",
            intent=getattr(ctx, "intent", "") or "",
            rules=list(getattr(ctx, "rules", []) or []),
            todos=list(getattr(ctx, "todos", []) or []),
            decisions=list(getattr(ctx, "decisions", []) or []),
            progress=list(getattr(ctx, "progress", []) or []),
            recovery_pointers=list(getattr(ctx, "recovery_pointers", []) or []),
        )
        self.store.save(manifest)
        return manifest

    def recover(
        self,
        ctx,
        messages: List[dict],
        compact_summary: str = "",
        token_budget: int = 2000,
    ) -> List[dict]:
        """Load the latest manifest for the session and inject the recovery
        block at the head of *messages*."""
        session_id = getattr(ctx, "session_id", "") or "default"
        manifest = self.store.load(session_id)
        if manifest is None:
            return list(messages)
        return self.injector.inject(messages, manifest, token_budget=token_budget)

    def status(self, session_id: Optional[str] = None) -> dict:
        """Summary of the latest manifest (counts + path) for debugging."""
        manifest = self.store.load(session_id)
        if manifest is None:
            return {"session_id": session_id, "manifest": None}
        files = self.store.list_manifests(manifest.session_id)
        return {
            "session_id": manifest.session_id,
            "created_at": manifest.created_at,
            "counts": manifest.counts(),
            "latest_file": str(files[-1]) if files else None,
            "manifest_count": len(files),
        }

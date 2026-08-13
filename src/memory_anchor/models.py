"""Data models for the compaction-aware memory layer.

Every item carries *verbatim* text — the whole point of this library is that
summary models must never be allowed to paraphrase rules, todos, decisions or
verification paths. JSON round-trips align with ``schemas/manifest.v1.json``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class RuleItem:
    """A governing rule (e.g. an AGENTS.md clause).

    ``immutable=True`` means the summarizer must never rewrite or paraphrase
    the text — recovery re-injects it verbatim and the L1 layer is never
    trimmed by token budgets.
    """

    rule_id: str
    text: str
    source: str = ""
    priority: int = 100
    immutable: bool = True


@dataclass
class TodoItem:
    todo_id: str
    title: str
    status: str = "pending"  # pending | in_progress | done | blocked
    next_action: str = ""
    depends_on: List[str] = field(default_factory=list)
    pointer: str = ""


@dataclass
class DecisionItem:
    decision_id: str
    title: str
    decision: str
    rationale: str = ""
    source: str = ""  # provenance: where the decision came from (file/thread/URL)
    evidence: str = ""  # provenance: what the decision was based on (fact/measurement/date)
    status: str = "made"  # made | tentative | superseded
    timestamp: str = ""


@dataclass
class ProgressItem:
    step: str
    artifacts: List[str] = field(default_factory=list)
    pending_verification: List[str] = field(default_factory=list)
    breadcrumbs: List[str] = field(default_factory=list)


@dataclass
class StateManifest:
    """Snapshot of the four lists taken *before* compaction.

    ``merge()`` implements incremental merge semantics across successive
    compactions of the same session: done todos never resurrect, superseded
    decisions never reappear, breadcrumbs are deduplicated.
    """

    schema_version: int = 1
    session_id: str = ""
    created_at: str = ""
    intent: str = ""
    rules: List[RuleItem] = field(default_factory=list)
    todos: List[TodoItem] = field(default_factory=list)
    decisions: List[DecisionItem] = field(default_factory=list)
    progress: List[ProgressItem] = field(default_factory=list)
    recovery_pointers: List[str] = field(default_factory=list)

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "StateManifest":
        data = json.loads(raw)
        if data.get("schema_version", 1) != 1:
            raise ValueError(
                f"unsupported schema_version {data.get('schema_version')}"
            )
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "StateManifest":
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            session_id=str(data.get("session_id", "")),
            created_at=str(data.get("created_at", "")),
            intent=str(data.get("intent", "")),
            rules=[RuleItem(**r) for r in data.get("rules", [])],
            todos=[TodoItem(**t) for t in data.get("todos", [])],
            decisions=[DecisionItem(**d) for d in data.get("decisions", [])],
            progress=[ProgressItem(**p) for p in data.get("progress", [])],
            recovery_pointers=[str(p) for p in data.get("recovery_pointers", [])],
        )

    # -- incremental merge ------------------------------------------------

    def merge(self, older: "StateManifest") -> "StateManifest":
        """Merge *older* manifest into this (newer) one, newest-wins.

        - todos marked ``done`` in the older manifest stay done in the result
          (a finished item is not resurrected by a newer snapshot that
          forgot about it)
        - decisions marked ``superseded`` in the older manifest stay
          superseded when the newer snapshot no longer carries that decision
          (a settled question is not resurrected by a snapshot that forgot
          it; a newer snapshot that *explicitly* re-decides the same id wins)
        - breadcrumbs are unioned and deduplicated
        """
        merged = StateManifest(
            schema_version=self.schema_version,
            session_id=self.session_id or older.session_id,
            created_at=self.created_at or older.created_at,
            intent=self.intent or older.intent,
            rules=list(self.rules),
            todos=list(self.todos),
            decisions=list(self.decisions),
            progress=list(self.progress),
            recovery_pointers=list(self.recovery_pointers),
        )

        # Carry over older rules not present in the newer snapshot.
        newer_rule_ids = {r.rule_id for r in merged.rules}
        merged.rules.extend(r for r in older.rules if r.rule_id not in newer_rule_ids)

        # Todos: never resurrect a done/blocked todo that the newer snapshot
        # downgraded or lost; newer snapshot's own items win on id collision
        # except for terminal statuses, which are irreversible.
        older_done = {t.todo_id: t.status for t in older.todos
                      if t.status in ("done", "blocked")}
        newer_todo_ids = {t.todo_id for t in merged.todos}
        for t in merged.todos:
            if t.todo_id in older_done and t.status not in ("done", "blocked"):
                t.status = older_done[t.todo_id]  # terminal status is irreversible
        for t in older.todos:
            if t.todo_id in newer_todo_ids:
                continue
            if t.todo_id in older_done:
                merged.todos.append(t)  # stays done/blocked
            elif t.status in ("done", "blocked"):
                merged.todos.append(t)
        merged.todos.sort(key=lambda t: (t.status == "done", t.todo_id))

        # Decisions: superseded decisions stay superseded.
        newer_dec_ids = {d.decision_id for d in merged.decisions}
        for d in older.decisions:
            if d.decision_id not in newer_dec_ids and d.status == "superseded":
                merged.decisions.append(d)

        # Progress: union breadcrumbs, keep pending_verification from both.
        seen_breadcrumbs: set[str] = set()
        for p in merged.progress:
            seen_breadcrumbs.update(p.breadcrumbs)
        older_progress_by_step = {p.step: p for p in older.progress}
        merged_steps = {p.step for p in merged.progress}
        for step, op in older_progress_by_step.items():
            if step in merged_steps:
                target = next(p for p in merged.progress if p.step == step)
                for bc in op.breadcrumbs:
                    if bc not in seen_breadcrumbs:
                        target.breadcrumbs.append(bc)
                        seen_breadcrumbs.add(bc)
                for pv in op.pending_verification:
                    if pv not in target.pending_verification:
                        target.pending_verification.append(pv)
                for art in op.artifacts:
                    if art not in target.artifacts:
                        target.artifacts.append(art)
            else:
                merged.progress.append(op)

        # Recovery pointers: union, dedup.
        for p in older.recovery_pointers:
            if p not in merged.recovery_pointers:
                merged.recovery_pointers.append(p)

        return merged

    # -- counts (used by status/verify) ------------------------------------

    def counts(self) -> dict:
        return {
            "rules": len(self.rules),
            "todos": len(self.todos),
            "decisions": len(self.decisions),
            "progress": len(self.progress),
            "recovery_pointers": len(self.recovery_pointers),
        }

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()

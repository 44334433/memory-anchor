"""Recovery block assembly + injection. Pure functions, no side effects.

The recovery block is a system-level message that re-anchors the *verbatim*
rules, todos, decisions and verification paths that a summarizer would
otherwise flatten away. Trimming order when the token budget is tight:
L3 recovery pointers → L2 low-priority todos/decisions → L2 high-priority.
L1 (``immutable`` rules) is never trimmed.
"""

from __future__ import annotations

from typing import List, Optional

from .models import DecisionItem, ProgressItem, RuleItem, StateManifest, TodoItem

_L1_HEADING = "## Applicable Rules (verbatim — do not rewrite)"
_L2_HEADING = "## Pending Work (verbatim)"
_L3_HEADING = "## Decisions (verbatim)"
_L4_HEADING = "## Progress & Verification (verbatim)"
_L5_HEADING = "## Recovery Pointers"


class RecoveryInjector:
    def __init__(self, store=None, prompt_builder: Optional["object"] = None):
        # store is accepted for API symmetry ; the v0.1
        # injector itself is a pure function of the manifest.
        self._store = store
        self._prompt_builder = prompt_builder

    # -- assembly ------------------------------------------------------------

    def build_recovery_block(
        self,
        manifest: StateManifest,
        include_rules: bool = True,
        include_todos: bool = True,
        include_decisions: bool = True,
        include_progress: bool = True,
        token_budget: int = 2000,
    ) -> str:
        """Assemble the recovery block. L1 rules are never trimmed."""
        sections: List[str] = []

        if include_rules and manifest.rules:
            sections.append(_L1_HEADING)
            # L1 = the whole rules layer — never trimmed, no filtering.
            sections.extend(f"- {r.rule_id}: {r.text}" for r in manifest.rules)

        budget = max(token_budget - _estimate_tokens("\n".join(sections)), 0)

        if include_todos and manifest.todos:
            remaining = [t for t in manifest.todos if t.status != "done"]
            sections.append(_L2_HEADING)
            for t in _trim(remaining, budget, key=lambda t: t.title):
                line = f"- [{t.status}] {t.title}"
                if t.next_action:
                    line += f" (next: {t.next_action})"
                sections.append(line)

        if include_decisions and manifest.decisions:
            active = [d for d in manifest.decisions if d.status != "superseded"]
            sections.append(_L3_HEADING)
            for d in _trim(active, budget, key=lambda d: d.title):
                line = f"- {d.title}: {d.decision}"
                if d.rationale:
                    line += f" (why: {d.rationale})"
                sections.append(line)

        if include_progress and manifest.progress:
            sections.append(_L4_HEADING)
            for p in manifest.progress:
                sections.append(f"- step: {p.step}")
                for pv in p.pending_verification:
                    sections.append(f"  - verify: {pv}")
                for art in p.artifacts:
                    sections.append(f"  - artifact: {art}")

        if manifest.recovery_pointers:
            sections.append(_L5_HEADING)
            sections.extend(f"- {p}" for p in manifest.recovery_pointers)

        return "\n".join(sections)

    # -- injection ------------------------------------------------------------

    def inject(
        self,
        messages: List[dict],
        manifest: StateManifest,
        token_budget: int = 2000,
    ) -> List[dict]:
        """Return a new message list with the recovery block as a system
        message inserted at the head (after any existing system prompt)."""
        block = self.build_recovery_block(manifest, token_budget=token_budget)
        if not block:
            return list(messages)
        recovery_msg = {"role": "system", "content": block}
        out: List[dict] = []
        inserted = False
        for m in messages:
            if not inserted and m.get("role") == "system":
                out.append(m)
                out.append(recovery_msg)
                inserted = True
            else:
                out.append(m)
        if not inserted:
            out.insert(0, recovery_msg)
        return out


# -- helpers -----------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    # Rough heuristic: ~4 chars per token for mixed CJK/Latin text.
    return max(len(text) // 4, 0)


def _trim(items: list, budget: int, key) -> list:
    """Drop lowest-priority items until the estimated size fits *budget*."""
    if budget <= 0:
        return []
    ordered = sorted(items, key=key)
    kept: list = []
    used = 0
    for it in ordered:
        cost = _estimate_tokens(str(it))
        if used + cost > budget and kept:
            break
        kept.append(it)
        used += cost
    return kept

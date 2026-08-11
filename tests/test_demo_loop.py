"""Demo closure test: preserve → (mock) compact → recover → assert hits.

Replicates the user-reported pain: a flash summarizer flattens todos,
rewrites decision rationale and drops verification paths. The recovery
block must restore them verbatim.
"""

from memory_anchor import (
    CompactableMemory,
    DecisionItem,
    ProgressItem,
    RuleItem,
    TodoItem,
)

R1 = "never paraphrase governing rules"


class FakeContext:
    def __init__(self):
        self.session_id = "demo-session"
        self.intent = "build feature X"
        self.rules = [RuleItem(rule_id="R1", text=R1, source="AGENTS.md")]
        self.todos = [
            TodoItem(todo_id="t1", title="wire RecoveryInjector",
                     next_action="edit recovery.py"),
            TodoItem(todo_id="t2", title="add CI badge"),
            TodoItem(todo_id="t3", title="write README", status="in_progress",
                     next_action="finish quickstart section"),
        ]
        self.decisions = [
            DecisionItem(decision_id="d1", title="storage backend",
                         decision="local JSON files (atomic tmp+rename)",
                         rationale="zero dependencies; SQLite deferred to v0.3"),
            DecisionItem(decision_id="d2", title="language",
                         decision="pure stdlib Python >=3.10",
                         rationale="no dep burden for an agent-framework library"),
        ]
        self.progress = [
            ProgressItem(step="core classes",
                         artifacts=["src/memory_anchor/models.py"],
                         pending_verification=[
                             "pytest tests/ -q must pass",
                             "README quickstart <= 5 min",
                         ]),
        ]
        self.recovery_pointers = ["search past sessions for 'feature X'"]


def _make_messages(n: int) -> list:
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message {i}"} for i in range(n)]


def _mock_compact(messages: list, keep: int = 5) -> list:
    """Simulate a lossy summarizer: keeps the tail, drops all detail."""
    return [{"role": "system", "content": "SUMMARY: work proceeded on feature X."}] + messages[-keep:]


def test_demo_closure(tmp_path):
    mem = CompactableMemory(base_dir=tmp_path / ".memory")
    ctx = FakeContext()

    # Step 1-2: preserve → manifest persisted with 4 non-empty lists
    manifest = mem.preserve(ctx)
    counts = manifest.counts()
    assert counts["rules"] == 1
    assert counts["todos"] == 3
    assert counts["decisions"] == 2
    assert counts["progress"] == 1

    # Step 3: mock compaction destroys detail (user's pain point)
    compacted = _mock_compact(_make_messages(50))

    # Step 4: recover re-injects the block
    restored = mem.recover(ctx, compacted)

    # Step 5: closure assertions — everything lost by the summarizer is back
    blocks = [m["content"] for m in restored if m["role"] == "system"]
    block = "\n".join(blocks)
    assert R1 in block                      # rules verbatim
    assert "wire RecoveryInjector" in block       # pending todos
    assert "write README" in block
    assert "local JSON files" in block            # decisions verbatim
    assert "zero dependencies" in block           # rationale kept
    assert "pytest tests/ -q must pass" in block  # verification paths verbatim
    assert "README quickstart <= 5 min" in block
    assert "search past sessions for 'feature X'" in block  # recovery pointer

    # Step 6: status reports counts
    st = mem.status("demo-session")
    assert st["counts"]["todos"] == 3
    assert st["manifest_count"] == 1

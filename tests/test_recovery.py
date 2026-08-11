"""Recovery tests: block assembly, L1-never-trimmed, injection order."""

from memory_anchor import (
    DecisionItem,
    ProgressItem,
    RecoveryInjector,
    RuleItem,
    StateManifest,
    TodoItem,
)


def _manifest() -> StateManifest:
    return StateManifest(
        session_id="s1",
        rules=[
            RuleItem(rule_id="R1", text="never paraphrase governing rules"),
            RuleItem(rule_id="R2", text="verify facts before acting", immutable=False),
        ],
        todos=[
            TodoItem(todo_id="t1", title="wire hook", next_action="edit hooks.py"),
            TodoItem(todo_id="t2", title="finished thing", status="done"),
        ],
        decisions=[DecisionItem(decision_id="d1", title="storage",
                                decision="JSON files", rationale="zero deps")],
        progress=[ProgressItem(step="step 1",
                               pending_verification=["pytest tests/ -q"])],
        recovery_pointers=["search past sessions for 'feature X'"],
    )


def test_block_contains_verbatim_items():
    block = RecoveryInjector().build_recovery_block(_manifest())
    assert "never paraphrase governing rules" in block
    assert "wire hook" in block
    assert "JSON files" in block
    assert "pytest tests/ -q" in block
    assert "search past sessions for 'feature X'" in block


def test_done_todos_excluded_from_block():
    block = RecoveryInjector().build_recovery_block(_manifest())
    assert "finished thing" not in block


def test_immutable_rules_never_trimmed_even_with_tiny_budget():
    inj = RecoveryInjector()
    block = inj.build_recovery_block(_manifest(), token_budget=10)
    assert "R1" in block  # L1 survives
    assert "verify facts before acting" in block  # non-immutable rule also present (rules section)
    assert "wire hook" not in block  # L2 trimmed


def test_inject_inserts_system_message_after_existing_system_prompt():
    messages = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "hi"},
    ]
    out = RecoveryInjector().inject(messages, _manifest())
    assert out[0] == messages[0]
    assert out[1]["role"] == "system"
    assert "R1" in out[1]["content"]
    assert out[2] == messages[1]


def test_inject_prepends_when_no_system_message():
    messages = [{"role": "user", "content": "hi"}]
    out = RecoveryInjector().inject(messages, _manifest())
    assert out[0]["role"] == "system"
    assert out[1] == messages[0]

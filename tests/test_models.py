"""Model tests: serialization round-trip + incremental merge semantics."""

from memory_anchor import (
    DecisionItem,
    ProgressItem,
    RuleItem,
    StateManifest,
    TodoItem,
)


def _manifest(**overrides):
    m = StateManifest(
        session_id="s1",
        intent="build feature X",
        rules=[RuleItem(rule_id="R1", text="never paraphrase governing rules")],
        todos=[TodoItem(todo_id="t1", title="wire hook", next_action="edit hooks.py")],
        decisions=[DecisionItem(decision_id="d1", title="use JSON", decision="JSON it is")],
        progress=[ProgressItem(step="step 1", pending_verification=["pytest tests/ -q"])],
        recovery_pointers=["search past sessions for 'feature X'"],
    )
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def test_json_round_trip_preserves_verbatim_text():
    m = _manifest()
    m2 = StateManifest.from_json(m.to_json())
    assert m2.session_id == "s1"
    assert m2.rules[0].text == "never paraphrase governing rules"  # verbatim
    assert m2.todos[0].next_action == "edit hooks.py"
    assert m2.decisions[0].rationale == ""
    assert m2.progress[0].pending_verification == ["pytest tests/ -q"]
    assert m2.counts() == {"rules": 1, "todos": 1, "decisions": 1,
                           "progress": 1, "recovery_pointers": 1}


def test_merge_never_resurrects_done_todo():
    newer = _manifest(todos=[])  # summarizer path lost the todo entirely
    older = _manifest()
    older.todos[0].status = "done"
    merged = newer.merge(older)
    statuses = {t.todo_id: t.status for t in merged.todos}
    assert statuses["t1"] == "done"  # stays done, not resurrected as pending


def test_merge_keeps_superseded_decision():
    newer = _manifest(decisions=[])
    older = _manifest()
    older.decisions[0].status = "superseded"
    merged = newer.merge(older)
    assert merged.decisions[0].status == "superseded"


def test_merge_dedups_breadcrumbs_and_rules():
    older = _manifest()
    newer = _manifest(progress=[ProgressItem(step="step 1", breadcrumbs=["a.py:42"])])
    older.progress[0].breadcrumbs = ["a.py:42", "b.py:7"]
    merged = newer.merge(older)
    crumbs = [bc for p in merged.progress for bc in p.breadcrumbs]
    assert crumbs.count("a.py:42") == 1  # deduped
    assert "b.py:7" in crumbs
    assert len(merged.rules) == 1  # rule not duplicated


def test_from_json_rejects_unknown_schema_version():
    import json
    raw = json.dumps({"schema_version": 99, "session_id": "x"})
    try:
        StateManifest.from_json(raw)
        assert False, "should have raised"
    except ValueError:
        pass


def test_decision_provenance_round_trip():
    """Provenance (source/evidence) must survive JSON round-trips (v0.3.1)."""
    d = DecisionItem(
        decision_id="d2",
        title="drop adapter layer",
        decision="no adapters until a real integration asks",
        rationale="no demand signals yet",
        source="benchmarks/run-1.md",
        evidence="measured retention 80% vs 60%",
    )
    m = StateManifest(session_id="s-prov", decisions=[d])
    m2 = StateManifest.from_json(m.to_json())
    d2 = m2.decisions[0]
    assert d2.source == "benchmarks/run-1.md"
    assert d2.evidence == "measured retention 80% vs 60%"
    assert d2.rationale == "no demand signals yet"


def test_legacy_decision_without_provenance_still_parses():
    """Old manifests (no source/evidence keys) must keep working."""
    m = StateManifest.from_dict({
        "schema_version": 1, "session_id": "s-legacy",
        "intent": "", "rules": [], "todos": [],
        "decisions": [{"decision_id": "d1", "title": "old",
                       "decision": "use JSON"}],
        "progress": [], "recovery_pointers": [],
    })
    assert m.decisions[0].source == ""
    assert m.decisions[0].evidence == ""


def test_from_dict_ignores_unknown_fields():
    """Forward-compatible parsing: fields added by newer schemas or by
    third-party generators must not crash strict dataclass construction."""
    m = StateManifest.from_dict({
        "schema_version": 1, "session_id": "future", "intent": "",
        "rules": [{"rule_id": "R1", "text": "keep verbatim", "owner": "future-schema"}],
        "todos": [{"todo_id": "T1", "title": "ship", "status": "pending", "priority": 9}],
        "decisions": [{"decision_id": "D1", "title": "t", "decision": "go", "score": 0.9}],
        "progress": [{"step": "audit", "pointer": "unknown-field"}],
        "recovery_pointers": [],
    })
    assert m.rules[0].text == "keep verbatim"
    assert m.todos[0].title == "ship"
    assert m.decisions[0].decision == "go"
    assert m.progress[0].step == "audit"

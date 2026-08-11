"""Tests for the compaction audit (`cam judge`, v0.3)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_anchor.judge import audit_manifest  # noqa: E402
from memory_anchor.models import (  # noqa: E402
    DecisionItem,
    ProgressItem,
    RuleItem,
    StateManifest,
    TodoItem,
)


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    return tmp_path / ".memory"


@pytest.fixture
def manifest() -> StateManifest:
    return StateManifest(
        session_id="s-judge",
        rules=[
            RuleItem(rule_id="R1", text="never paraphrase governing rules"),
            RuleItem(rule_id="R2", text="all writes must be atomic"),
        ],
        todos=[
            TodoItem(todo_id="t1", title="ship v0.3", status="pending"),
            TodoItem(todo_id="t2", title="write docs", status="pending"),
        ],
        decisions=[
            DecisionItem(decision_id="d1", title="compressor choice", decision="use extractive first"),
        ],
        progress=[
            ProgressItem(step="implement judge"),
        ],
    )


def test_verbatim_survives(manifest):
    after = (
        "never paraphrase governing rules. all writes must be atomic. "
        "ship v0.3. use extractive first. implement judge."
    )
    verdicts = audit_manifest(manifest, after)
    by_label = {v.label: v.verdict for v in verdicts}
    assert by_label["R1"] == "verbatim"
    assert by_label["R2"] == "verbatim"
    assert by_label["t1"] == "verbatim"
    assert by_label["d1"] == "verbatim"
    assert by_label["implement judge"] == "verbatim"


def test_lost_and_paraphrased(manifest):
    # R1 missing entirely; t2 paraphrased (similar but not identical);
    # t1 present verbatim.
    after = (
        "all writes must be atomic. ship v0.3. "
        "documentation needs to be authored"
    )
    verdicts = audit_manifest(manifest, after)
    by_label = {v.label: v.verdict for v in verdicts}
    assert by_label["R1"] == "lost"
    assert by_label["R2"] == "verbatim"
    assert by_label["t1"] == "verbatim"
    # "write docs" vs "documentation needs to be authored": low similarity -> lost
    assert by_label["t2"] in ("lost", "paraphrased")


def test_rule_paraphrase_is_lost(manifest):
    """Design contract #1: rules must survive verbatim or not at all."""
    after = "you should never rephrase the governing rules"
    verdicts = audit_manifest(manifest, after)
    by_label = {v.label: v.verdict for v in verdicts}
    assert by_label["R1"] == "lost"  # paraphrase of R1 graded as lost (L1 strict)
    assert by_label["R2"] == "lost"


def test_whitespace_folding(manifest):
    after = "never   paraphrase   governing\n rules.  all writes must be atomic."
    verdicts = audit_manifest(manifest, after)
    by_label = {v.label: v.verdict for v in verdicts}
    assert by_label["R1"] == "verbatim"
    assert by_label["R2"] == "verbatim"


def test_empty_after_loses_everything(manifest):
    verdicts = audit_manifest(manifest, "")
    assert all(v.verdict == "lost" for v in verdicts)


def test_short_item_in_long_corpus(manifest):
    """A 20-char item inside a 5000-char corpus must still be found."""
    corpus = ("filler text " * 300) + "ship v0.3" + (" trailing filler " * 100)
    verdicts = audit_manifest(manifest, corpus)
    by_label = {v.label: v.verdict for v in verdicts}
    assert by_label["t1"] == "verbatim"


def test_cli_judge_json(base_dir: Path):
    mf = base_dir.parent / "judge-m.json"
    mf.write_text(json.dumps({
        "schema_version": 1, "session_id": "sj", "intent": "",
        "rules": [{"rule_id": "R1", "text": "keep secrets out"}],
        "todos": [], "decisions": [], "progress": [], "recovery_pointers": [],
    }), encoding="utf-8")
    after = base_dir.parent / "judge-after.txt"
    after.write_text("keep secrets out. everything else was compressed away.",
                     encoding="utf-8")
    src = str(Path(__file__).resolve().parent.parent / "src")
    p = subprocess.run(
        [sys.executable, "-m", "memory_anchor.cli", "judge",
         "--before", str(mf), "--after", str(after), "--json"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": src},
    )
    assert p.returncode == 0, p.stderr
    report = json.loads(p.stdout)
    assert report["stats"]["total"] == 1
    assert report["stats"]["verbatim"] == 1
    assert report["stats"]["retention"] == 100.0
    assert report["items"][0]["verdict"] == "verbatim"


def test_cli_judge_gate_fails(base_dir: Path):
    mf = base_dir.parent / "judge-gate-m.json"
    mf.write_text(json.dumps({
        "schema_version": 1, "session_id": "sg", "intent": "",
        "rules": [{"rule_id": "R1", "text": "unique-rule-xyz-12345"}],
        "todos": [], "decisions": [], "progress": [], "recovery_pointers": [],
    }), encoding="utf-8")
    after = base_dir.parent / "judge-gate-after.txt"
    after.write_text("nothing here matches the rule.", encoding="utf-8")
    src = str(Path(__file__).resolve().parent.parent / "src")
    p = subprocess.run(
        [sys.executable, "-m", "memory_anchor.cli", "judge",
         "--before", str(mf), "--after", str(after),
         "--min-verbatim", "80"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": src},
    )
    assert p.returncode == 1
    assert "verbatim 0.0% < gate" in p.stderr


def test_cli_judge_stdin(base_dir: Path):
    mf = base_dir.parent / "judge-stdin-m.json"
    mf.write_text(json.dumps({
        "schema_version": 1, "session_id": "ss", "intent": "",
        "rules": [{"rule_id": "R1", "text": "pin model provider"}],
        "todos": [], "decisions": [], "progress": [], "recovery_pointers": [],
    }), encoding="utf-8")
    src = str(Path(__file__).resolve().parent.parent / "src")
    p = subprocess.run(
        [sys.executable, "-m", "memory_anchor.cli", "judge",
         "--before", str(mf), "--after", "-", "--json"],
        input="pin model provider was in the summary",
        capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": src},
    )
    assert p.returncode == 0, p.stderr
    report = json.loads(p.stdout)
    assert report["items"][0]["verdict"] == "verbatim"

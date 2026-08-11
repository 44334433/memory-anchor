"""CLI integration tests for `cam` (v0.2)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_anchor.cli import main  # noqa: E402


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    return tmp_path / ".memory"


def run_cli(base_dir: Path, *argv: str) -> subprocess.CompletedProcess:
    src = str(Path(__file__).resolve().parent.parent / "src")
    env = {**__import__("os").environ, "PYTHONPATH": src}
    return subprocess.run(
        [sys.executable, "-m", "memory_anchor.cli", "--base-dir", str(base_dir), *argv],
        capture_output=True, text=True, cwd=str(base_dir.parent), env=env,
    )


def test_before_status_after_roundtrip(base_dir: Path):
    p = run_cli(base_dir, "before", "sess-1",
                "--rule", "R1|never paraphrase rules|100",
                "--todo", "ship v0.2|pending|run drill",
                "--decision", "compressor|use extractive first|zero deps")
    assert p.returncode == 0, p.stderr
    assert "saved manifest" in p.stdout
    assert "1 rules, 1 todos" in p.stdout

    p = run_cli(base_dir, "status", "sess-1")
    assert p.returncode == 0
    st = json.loads(p.stdout)
    assert st["counts"] == {"rules": 1, "todos": 1, "decisions": 1, "progress": 0, "recovery_pointers": 0}
    assert st["manifest_count"] == 1

    messages = [{"role": "user", "content": "hi"}]
    msg_file = base_dir.parent / "messages.json"
    msg_file.write_text(json.dumps(messages), encoding="utf-8")
    p = run_cli(base_dir, "after", "sess-1", "--messages", str(msg_file))
    assert p.returncode == 0, p.stderr
    out = json.loads((msg_file.with_suffix(".recovered.json")).read_text(encoding="utf-8"))
    assert len(out) == 2  # recovery block + original user message
    assert any(m.get("role") == "system" and "Applicable Rules" in m["content"] for m in out)
    assert any("never paraphrase rules" in m["content"] for m in out)


def test_before_manifest_file_and_verify(base_dir: Path):
    manifest = {
        "schema_version": 1,
        "session_id": "s2",
        "intent": "demo",
        "rules": [{"rule_id": "R9", "text": "no secrets in output", "priority": 100}],
        "todos": [{"todo_id": "t1", "title": "write docs", "status": "pending"}],
        "decisions": [], "progress": [], "recovery_pointers": [],
    }
    mf = base_dir.parent / "m.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    p = run_cli(base_dir, "before", "s2", "--manifest", str(mf))
    assert p.returncode == 0, p.stderr

    p = run_cli(base_dir, "verify", "s2")
    assert p.returncode == 0, p.stderr
    assert "manifest OK" in p.stdout


def test_verify_rejects_bad_status(base_dir: Path):
    p = run_cli(base_dir, "before", "s3", "--todo", "bad|frozen|none")
    assert p.returncode == 1
    assert "bad todo status" in p.stderr


def test_after_stdin_stdout_pipe(base_dir: Path):
    run_cli(base_dir, "before", "s4", "--rule", "R1|keep verbatim|100")
    proc = subprocess.run(
        [sys.executable, "-m", "memory_anchor.cli", "--base-dir", str(base_dir),
         "after", "s4", "--messages", "-"],
        input=json.dumps([{"role": "user", "content": "hello"}]), capture_output=True, text=True,
        cwd=str(base_dir.parent),
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out[0]["role"] == "system"
    assert "keep verbatim" in out[0]["content"]


def test_before_empty_is_error(base_dir: Path):
    p = run_cli(base_dir, "before", "s5")
    assert p.returncode == 1
    assert "nothing to preserve" in p.stderr


def test_after_no_manifest_is_error(base_dir: Path):
    p = run_cli(base_dir, "after", "ghost", "--messages", "-")
    assert p.returncode == 1
    assert "no manifest" in p.stderr

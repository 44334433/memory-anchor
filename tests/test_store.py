"""Store tests: atomic write, load-latest-merged, corruption tolerance."""

from pathlib import Path

from memory_anchor import MemoryStore, StateManifest, TodoItem


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / ".memory")


def _m(session_id: str, todo_title: str) -> StateManifest:
    return StateManifest(
        session_id=session_id,
        intent="t",
        todos=[TodoItem(todo_id="t1", title=todo_title)],
    )


def test_save_and_load_round_trip(tmp_path):
    st = _store(tmp_path)
    m = _m("s1", "first")
    path = st.save(m)
    assert path.exists()
    loaded = st.load("s1")
    assert loaded is not None
    assert loaded.todos[0].title == "first"
    assert len(st.list_manifests("s1")) == 1


def test_load_merges_multiple_manifests_newest_effective(tmp_path):
    st = _store(tmp_path)
    old = _m("s1", "first")
    old.todos[0].status = "done"
    st.save(old)
    new = _m("s1", "second")
    st.save(new)
    loaded = st.load("s1")
    # merged: newer snapshot wins the todo, but done-state carries over
    assert loaded.todos[0].title == "second"
    assert loaded.todos[0].status == "done"
    assert len(st.list_manifests("s1")) == 2


def test_load_none_when_empty(tmp_path):
    st = _store(tmp_path)
    assert st.load("nope") is None
    assert st.list_manifests() == []


def test_corrupt_file_raises_on_load_by_path(tmp_path):
    st = _store(tmp_path)
    m = _m("s1", "x")
    path = st.save(m)
    path.write_text("{not json", encoding="utf-8")
    try:
        st.load_by_path(path)
        assert False, "should have raised"
    except Exception:
        pass


def test_no_leftover_tmp_files_after_save(tmp_path):
    st = _store(tmp_path)
    st.save(_m("s1", "x"))
    leftovers = [p for p in (tmp_path / ".memory").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []

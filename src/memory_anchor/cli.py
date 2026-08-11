"""cam — command-line front-end for memory-anchor (v0.2).

Scriptable compaction workflow:

    cam before my-session --rule "R1|Never run tests with -x|100" --todo "ship v0.2|pending|run drill"
    cam status my-session
    cam after  my-session --messages messages.json --budget 2000
    cam verify my-session

Exit code 0 = success, 1 = failure (message on stderr). Data goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .models import (
    DecisionItem,
    ProgressItem,
    RuleItem,
    StateManifest,
    TodoItem,
)
from .store import MemoryStore

_ITEM_SEP = "|"


def _split_item(raw: str, fields: int, label: str) -> list:
    parts = [p.strip() for p in raw.split(_ITEM_SEP)]
    if len(parts) < 1 or len(parts) > fields:
        sys.exit(
            f"cam: {label} must be '{_ITEM_SEP}'-separated with 1..{fields} fields, got: {raw!r}"
        )
    return parts + [""] * (fields - len(parts))


def _build_manifest(session_id: str, args) -> StateManifest:
    rules: list[RuleItem] = []
    todos: list[TodoItem] = []
    decisions: list[DecisionItem] = []
    progress: list[ProgressItem] = []

    for raw in args.rule or []:
        r_id, text, priority = _split_item(raw, 3, "rule")
        if not r_id or not text:
            sys.exit(f"cam: rule needs at least 'id|text', got: {raw!r}")
        try:
            prio = int(priority) if priority else 100
        except ValueError:
            sys.exit(f"cam: rule priority must be int, got: {priority!r}")
        rules.append(RuleItem(rule_id=r_id, text=text, priority=prio))

    for raw in args.todo or []:
        title, status, next_action = _split_item(raw, 3, "todo")
        if not title:
            sys.exit(f"cam: todo needs at least 'title', got: {raw!r}")
        if status and status not in ("pending", "in_progress", "done", "blocked"):
            sys.exit(f"cam: bad todo status {status!r}")
        todos.append(
            TodoItem(todo_id=title, title=title, status=status or "pending", next_action=next_action)
        )

    for raw in args.decision or []:
        title, decision, rationale = _split_item(raw, 3, "decision")
        if not title or not decision:
            sys.exit(f"cam: decision needs 'title|decision', got: {raw!r}")
        decisions.append(DecisionItem(decision_id=title, title=title, decision=decision, rationale=rationale))

    for raw in args.progress or []:
        step, *rest = _split_item(raw, 3, "progress")
        if not step:
            sys.exit(f"cam: progress needs at least 'step', got: {raw!r}")
        progress.append(ProgressItem(step=step, artifacts=[rest[0]] if rest[0] else []))

    return StateManifest(
        session_id=session_id,
        intent=args.intent or "",
        rules=rules,
        todos=todos,
        decisions=decisions,
        progress=progress,
        recovery_pointers=list(args.pointer or []),
    )


def _load_messages(path: str) -> list[dict]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        messages = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"cam: cannot parse messages JSON: {e}")
    if not isinstance(messages, list) or not all(
        isinstance(m, dict) and "role" in m and "content" in m for m in messages
    ):
        sys.exit("cam: messages must be a JSON list of {role, content} objects")
    return messages


def _verify_manifest(m: StateManifest) -> list[str]:
    """Zero-dependency schema check (mirrors schemas/manifest.v1.json)."""
    problems: list[str] = []
    if m.schema_version != 1:
        problems.append(f"schema_version must be 1, got {m.schema_version}")
    if not m.session_id:
        problems.append("session_id is required")
    for r in m.rules:
        if not r.rule_id or not r.text:
            problems.append("rule item needs non-empty rule_id and text")
    for t in m.todos:
        if not t.title:
            problems.append("todo item needs non-empty title")
        if t.status not in ("pending", "in_progress", "done", "blocked"):
            problems.append(f"todo status {t.status!r} not in allowed set")
    for d in m.decisions:
        if not d.title or not d.decision:
            problems.append("decision item needs non-empty title and decision")
        if d.status not in ("made", "tentative", "superseded"):
            problems.append(f"decision status {d.status!r} not in allowed set")
    for p in m.progress:
        if not p.step:
            problems.append("progress item needs non-empty step")
    return problems


def _cmd_before(store: MemoryStore, args) -> int:
    if args.manifest:
        manifest = StateManifest.from_json(Path(args.manifest).read_text(encoding="utf-8"))
        manifest.session_id = args.session
        problems = _verify_manifest(manifest)
        if problems:
            for p in problems:
                print(f"cam: manifest invalid: {p}", file=sys.stderr)
            return 1
    else:
        manifest = _build_manifest(args.session, args)
        if not (manifest.rules or manifest.todos or manifest.decisions or manifest.progress):
            sys.exit("cam: nothing to preserve — pass --rule/--todo/--decision/--progress or --manifest")
    path = store.save(manifest)
    counts = manifest.counts()
    print(
        f"saved manifest for session {manifest.session_id!r}: "
        f"{counts['rules']} rules, {counts['todos']} todos, "
        f"{counts['decisions']} decisions, {counts['progress']} progress items "
        f"-> {path}"
    )
    return 0


def _cmd_after(store: MemoryStore, args) -> int:
    manifest = store.load(args.session)
    if manifest is None:
        print(f"cam: no manifest found for session {args.session!r}", file=sys.stderr)
        return 1
    messages = _load_messages(args.messages)
    from .recovery import RecoveryInjector

    out = RecoveryInjector(store).inject(messages, manifest, token_budget=args.budget)
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    if args.messages == "-":
        sys.stdout.write(payload + "\n")
    else:
        out_path = Path(args.messages).with_suffix(".recovered.json")
        out_path.write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {len(out)} messages (recovery block injected) -> {out_path}")
    return 0


def _cmd_status(store: MemoryStore, args) -> int:
    manifest = store.load(args.session)
    if manifest is None:
        print(f"cam: no manifest for session {args.session!r}", file=sys.stderr)
        return 1
    files = store.list_manifests(args.session)
    print(json.dumps({
        "session_id": manifest.session_id,
        "created_at": manifest.created_at,
        "counts": manifest.counts(),
        "manifest_count": len(files),
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(store: MemoryStore, args) -> int:
    manifest = store.load(args.session)
    if manifest is None:
        print(f"cam: no manifest for session {args.session!r}", file=sys.stderr)
        return 1
    problems = _verify_manifest(manifest)
    if problems:
        for p in problems:
            print(f"cam: invalid: {p}", file=sys.stderr)
        return 1
    files = store.list_manifests(args.session)
    print(f"manifest OK: session={manifest.session_id} files={len(files)} counts={manifest.counts()}")
    return 0


def _cmd_judge(store: MemoryStore, args) -> int:
    from .judge import main as judge_main

    return judge_main([
        "--before", str(args.before),
        "--after", args.after,
    ] + (["--json"] if args.json else [])
      + (["--min-retention", str(args.min_retention)] if args.min_retention is not None else [])
      + (["--min-verbatim", str(args.min_verbatim)] if args.min_verbatim is not None else []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cam",
        description="memory-anchor CLI: preserve state before compaction, recover after.",
    )
    parser.add_argument("--version", action="version", version=f"memory-anchor {__version__}")
    parser.add_argument("--base-dir", type=Path, default=Path(".memory"), help="manifest storage dir")
    sub = parser.add_subparsers(dest="command", required=True)

    p_before = sub.add_parser("before", help="snapshot state into a manifest")
    p_before.add_argument("session", help="session id")
    p_before.add_argument("--intent", default="")
    p_before.add_argument("--rule", action="append", metavar="ID|TEXT|PRIORITY")
    p_before.add_argument("--todo", action="append", metavar="TITLE|STATUS|NEXT")
    p_before.add_argument("--decision", action="append", metavar="TITLE|DECISION|WHY")
    p_before.add_argument("--progress", action="append", metavar="STEP|ARTIFACT")
    p_before.add_argument("--pointer", action="append")
    p_before.add_argument("--manifest", metavar="FILE", help="load full manifest JSON from FILE")
    p_before.set_defaults(func=_cmd_before)

    p_after = sub.add_parser("after", help="inject recovery block into messages")
    p_after.add_argument("session")
    p_after.add_argument("--messages", required=True, metavar="FILE|'-'",
                         help="messages JSON (list of {role, content}); '-' = stdin")
    p_after.add_argument("--budget", type=int, default=2000, help="recovery block token budget")
    p_after.set_defaults(func=_cmd_after)

    p_status = sub.add_parser("status", help="show latest manifest summary")
    p_status.add_argument("session", nargs="?", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_verify = sub.add_parser("verify", help="validate latest manifest against schema")
    p_verify.add_argument("session")
    p_verify.set_defaults(func=_cmd_verify)

    p_judge = sub.add_parser(
        "judge",
        help="audit a compaction: classify manifest items as verbatim/paraphrased/lost "
             "against the after-text",
    )
    p_judge.add_argument("--before", required=True, metavar="MANIFEST.json",
                         help="manifest snapshot taken BEFORE compaction")
    p_judge.add_argument("--after", required=True, metavar="TEXT|'-'",
                         help="compressed context / summary text file; '-' = stdin")
    p_judge.add_argument("--json", action="store_true", help="machine-readable JSON report")
    p_judge.add_argument("--min-retention", type=float, default=None, metavar="PCT",
                         help="gate: exit 1 when retention (verbatim+paraphrased) < PCT")
    p_judge.add_argument("--min-verbatim", type=float, default=None, metavar="PCT",
                         help="gate: exit 1 when verbatim rate < PCT")
    p_judge.set_defaults(func=_cmd_judge)

    args = parser.parse_args(argv)
    store = MemoryStore(args.base_dir)
    return args.func(store, args)


if __name__ == "__main__":
    sys.exit(main())

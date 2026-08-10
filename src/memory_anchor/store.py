"""Local JSON manifest storage with atomic writes.

Layout::

    <base_dir>/manifest-<session_id>-<timestamp>.json

``save`` writes via tmp+rename (atomic on POSIX). ``load`` returns the most
recent manifest for a session; repeated compactions of the same session are
merged before being returned so callers always see the cumulative state.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from .models import StateManifest

_PREFIX = "manifest-"
_SUFFIX = ".json"


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # -- paths ------------------------------------------------------------

    @staticmethod
    def _filename(session_id: str, timestamp: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return f"{_PREFIX}{safe}-{timestamp}{_SUFFIX}"

    def _manifests(self, session_id: Optional[str] = None) -> List[Path]:
        files = sorted(self.base_dir.glob(f"{_PREFIX}*{_SUFFIX}"))
        if session_id is not None:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
            files = [f for f in files if f.stem.startswith(f"{_PREFIX}{safe}-")]
        return files

    # -- core API -----------------------------------------------------------

    def save(self, manifest: StateManifest) -> Path:
        """Atomically write *manifest* to disk (tmp + rename)."""
        if not manifest.session_id:
            raise ValueError("manifest.session_id is required")
        path = self.base_dir / self._filename(manifest.session_id, manifest.created_at)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self.base_dir, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(manifest.to_dict(), fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return path

    def load(self, session_id: Optional[str] = None) -> Optional[StateManifest]:
        """Most recent manifest for *session_id* (or across all sessions).

        If multiple manifests exist for a session, they are merged oldest →
        newest so the result reflects cumulative state after N compactions.
        """
        files = self._manifests(session_id)
        if not files:
            return None
        merged: Optional[StateManifest] = None
        for f in files:
            m = self.load_by_path(f)
            merged = m if merged is None else m.merge(merged)
        return merged

    def list_manifests(self, session_id: Optional[str] = None) -> List[Path]:
        return self._manifests(session_id)

    def load_by_path(self, path: Path) -> StateManifest:
        raw = Path(path).read_text(encoding="utf-8")
        return StateManifest.from_json(raw)

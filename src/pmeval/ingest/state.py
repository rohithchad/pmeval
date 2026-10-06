"""Persisted progress tracking so long ingestion jobs can resume after a crash."""

from __future__ import annotations

import json
from pathlib import Path


class ProgressState:
    """Set of keys (for example market tickers or URLs) already fully ingested, saved as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: set[str] = set()
        if path.exists():
            self.done = set(json.loads(path.read_text())["done"])

    def is_done(self, key: str) -> bool:
        return key in self.done

    def mark_done(self, key: str) -> None:
        """Record a key and save immediately so a crash loses at most one item."""
        self.done.add(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"done": sorted(self.done)}))
        temp.replace(self.path)  # atomic rename: never leaves a half-written state file

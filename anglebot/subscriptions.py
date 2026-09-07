"""Sources ajoutees a chaud via les commandes Discord."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .state import atomic_write_json

log = logging.getLogger(__name__)


class SubscriptionStore:
    """Liste de sources persistee en JSON, modifiable depuis Discord."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Abonnements illisibles (%s) : %s", self.path, exc)
            return
        entries = raw.get("sources") if isinstance(raw, dict) else raw
        if isinstance(entries, list):
            self.entries = [e for e in entries if isinstance(e, dict)]

    def save(self) -> None:
        atomic_write_json(self.path, {"sources": self.entries})

    def add(self, entry: dict[str, Any]) -> None:
        entry = {**entry, "origin": "discord"}
        self.entries.append(entry)
        self.save()

    def remove(self, predicate) -> int:
        before = len(self.entries)
        self.entries = [e for e in self.entries if not predicate(e)]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

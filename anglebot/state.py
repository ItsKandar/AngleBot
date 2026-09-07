"""Persistance des identifiants deja annonces (deduplication)."""

from __future__ import annotations

import json
import logging
import tempfile
from collections import deque
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


def atomic_write_json(path: Path, payload: object) -> None:
    """Ecrit un JSON sans risque de fichier tronque en cas d'arret brutal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class SeenStore:
    """Garde les N derniers identifiants vus par source."""

    def __init__(self, path: Path, history_size: int = 250) -> None:
        self.path = path
        self.history_size = history_size
        self._seen: dict[str, deque[str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Etat illisible (%s), reinitialisation : %s", self.path, exc)
            return
        for key, ids in (raw.get("seen") or {}).items():
            if isinstance(ids, list):
                self._seen[key] = deque(
                    (str(i) for i in ids[-self.history_size :]), maxlen=self.history_size
                )

    def save(self) -> None:
        payload = {"seen": {key: list(ids) for key, ids in self._seen.items()}}
        try:
            atomic_write_json(self.path, payload)
        except OSError as exc:
            log.error("Impossible d'ecrire l'etat %s : %s", self.path, exc)

    def is_known(self, source_key: str) -> bool:
        """True si la source a deja ete verifiee au moins une fois."""
        return source_key in self._seen

    def unseen(self, source_key: str, uids: Iterable[str]) -> list[str]:
        known = self._seen.get(source_key)
        if known is None:
            return list(uids)
        already = set(known)
        return [uid for uid in uids if uid not in already]

    def mark(self, source_key: str, uids: Iterable[str]) -> None:
        bucket = self._seen.setdefault(source_key, deque(maxlen=self.history_size))
        already = set(bucket)
        for uid in uids:
            if uid not in already:
                bucket.append(uid)
                already.add(uid)

    def forget(self, source_key: str) -> None:
        self._seen.pop(source_key, None)

"""Modele commun a toutes les sources surveillees."""

from __future__ import annotations

import hashlib
import html
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[^\S\n]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class SourceError(RuntimeError):
    """Erreur recuperable lors de la collecte d'une source."""


def strip_html(value: str | None, limit: int = 500) -> str:
    """Transforme un fragment HTML en texte lisible dans un embed."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = _INLINE_WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def stable_uid(*parts: str) -> str:
    """Identifiant court et stable pour un item sans id fiable."""
    digest = hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


@dataclass(slots=True)
class Item:
    """Une publication normalisee, prete a etre affichee."""

    uid: str
    url: str | None = None
    title: str = ""
    summary: str = ""
    author: str | None = None
    published: datetime | None = None
    thumbnail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Une source surveillee : un flux RSS ou un compte social."""

    type_name: str = "generic"
    color: int = 0x5865F2
    platform_label: str = "Source"
    platform_icon: str | None = None

    def __init__(self, raw: dict[str, Any], bridges: dict[str, str]) -> None:
        self.raw = raw
        self.bridges = bridges
        self.name: str = str(raw.get("name") or self.default_name())
        self.channel_id: int | None = as_int(raw.get("channel_id"))
        self.role_id: int | None = as_int(raw.get("role_id"))
        self.enabled: bool = bool(raw.get("enabled", True))
        self.origin: str = str(raw.get("origin") or "config")

    # -- identite ---------------------------------------------------------
    def default_name(self) -> str:
        return f"{self.platform_label} - {self.identifier}"

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Valeur qui distingue la source (URL, pseudo, id de chaine)."""

    @property
    def key(self) -> str:
        """Cle de deduplication, stable meme si le nom change."""
        return f"{self.type_name}:{self.identifier.lower()}"

    @property
    def profile_url(self) -> str | None:
        return None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<{type(self).__name__} {self.key}>"

    # -- collecte ---------------------------------------------------------
    @abstractmethod
    async def fetch(self, session: aiohttp.ClientSession) -> list[Item]:
        """Retourne les publications, de la plus recente a la plus ancienne."""


def as_int(value: Any) -> int | None:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        log.warning("Valeur numerique invalide ignoree : %r", value)
        return None


def order_by_recency(items: list[Item]) -> list[Item]:
    """Classe les publications de la plus recente a la plus ancienne.

    Tous les flux ne respectent pas la convention : certains CMS listent leurs
    entrees dans l'ordre chronologique. On reordonne des que toutes les
    publications sont datees, sinon l'ordre d'origine est le seul repere fiable.
    """
    if not items or any(item.published is None for item in items):
        return items

    def key(item: Item) -> datetime:
        published = item.published
        assert published is not None  # garanti par le test ci-dessus
        return published if published.tzinfo else published.replace(tzinfo=timezone.utc)

    return sorted(items, key=key, reverse=True)

"""Registre des types de sources surveillables."""

from __future__ import annotations

import logging
from typing import Any

from .base import Item, Source, SourceError, order_by_recency
from .bluesky import BlueskySource
from .rss import RssSource
from .social import (
    InstagramSource,
    ThreadsSource,
    TikTokSource,
    XSource,
    YouTubeSource,
)

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[Source]] = {
    "rss": RssSource,
    "atom": RssSource,
    "bluesky": BlueskySource,
    "tiktok": TikTokSource,
    "instagram": InstagramSource,
    "threads": ThreadsSource,
    "x": XSource,
    "twitter": XSource,
    "youtube": YouTubeSource,
}

SOCIAL_TYPES = ("bluesky", "tiktok", "instagram", "x", "threads")


def build_source(raw: dict[str, Any], bridges: dict[str, str]) -> Source:
    """Instancie une source depuis sa description. Leve SourceError si invalide."""
    type_name = str(raw.get("type") or "rss").strip().lower()
    cls = REGISTRY.get(type_name)
    if cls is None:
        raise SourceError(
            f"Type de source inconnu : {type_name!r} "
            f"(disponibles : {', '.join(sorted(REGISTRY))})"
        )
    return cls(raw, bridges)


def build_sources(
    raws: list[dict[str, Any]], bridges: dict[str, str]
) -> tuple[list[Source], list[str]]:
    """Construit toutes les sources valides et retourne les erreurs rencontrees."""
    sources: list[Source] = []
    errors: list[str] = []
    seen: set[str] = set()

    for raw in raws:
        try:
            source = build_source(raw, bridges)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        except (TypeError, ValueError) as exc:
            errors.append(f"Source invalide {raw!r} : {exc}")
            continue

        if not source.enabled:
            log.info("Source desactivee, ignoree : %s", source.name)
            continue
        if source.key in seen:
            log.info("Source en doublon, ignoree : %s", source.key)
            continue
        seen.add(source.key)
        sources.append(source)

    return sources, errors


__all__ = [
    "Item",
    "REGISTRY",
    "SOCIAL_TYPES",
    "Source",
    "SourceError",
    "build_source",
    "build_sources",
    "order_by_recency",
]

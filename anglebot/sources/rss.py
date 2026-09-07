"""Source RSS / Atom generique, socle des passerelles sociales."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import aiohttp
import feedparser

from .base import Item, Source, SourceError, order_by_recency, stable_uid, strip_html

log = logging.getLogger(__name__)

_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def _entry_datetime(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(attr)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _entry_thumbnail(entry: Any) -> str | None:
    for media in entry.get("media_thumbnail") or []:
        if media.get("url"):
            return media["url"]
    for media in entry.get("media_content") or []:
        if media.get("url") and str(media.get("medium", "image")) == "image":
            return media["url"]
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image/"):
            return link.get("href")
    for field in ("content", "summary_detail"):
        blocks = entry.get(field)
        blocks = blocks if isinstance(blocks, list) else [blocks] if blocks else []
        for block in blocks:
            match = _IMG_RE.search(str(block.get("value", "")))
            if match:
                return match.group(1)
    return None


def _entry_body(entry: Any) -> str:
    content = entry.get("content")
    if isinstance(content, list) and content:
        return str(content[0].get("value", ""))
    return str(entry.get("summary") or entry.get("description") or "")


class RssSource(Source):
    """Surveille un flux RSS/Atom accessible en HTTP."""

    type_name = "rss"
    color = 0xF26522
    platform_label = "RSS"

    #: nombre d'entrees conservees par flux (les plus recentes)
    max_entries = 25
    #: garde-fou sur les flux qui exposent tout leur historique
    parse_limit = 200

    def __init__(self, raw: dict[str, Any], bridges: dict[str, str]) -> None:
        self._url = self.build_url(raw, bridges)
        if not self._url:
            raise SourceError(f"Source {raw.get('type')!r} : URL de flux introuvable.")
        parsed = urlparse(self._url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SourceError(f"URL de flux invalide : {self._url!r}")
        super().__init__(raw, bridges)

    def build_url(self, raw: dict[str, Any], bridges: dict[str, str]) -> str:
        return str(raw.get("url") or "").strip()

    @property
    def url(self) -> str:
        return self._url

    @property
    def identifier(self) -> str:
        return self._url

    @property
    def profile_url(self) -> str | None:
        return self._url

    def default_name(self) -> str:
        host = urlparse(self._url).netloc or self._url
        return f"{self.platform_label} - {host}"

    async def fetch(self, session: aiohttp.ClientSession) -> list[Item]:
        payload = await self._download(session)
        # feedparser est synchrone et peut etre lent sur un gros flux.
        parsed = await asyncio.to_thread(feedparser.parse, payload)

        if getattr(parsed, "bozo", 0) and not parsed.entries:
            raise SourceError(
                f"Flux illisible ({getattr(parsed, 'bozo_exception', 'erreur inconnue')})"
            )

        feed_author = str((parsed.feed or {}).get("title") or "") or None
        items = [
            item
            for entry in parsed.entries[: self.parse_limit]
            if (item := self._to_item(entry, feed_author)) is not None
        ]
        # Le tri precede la troncature : certains flux listent du plus ancien
        # au plus recent, tronquer d'abord jetterait les nouveautes.
        return order_by_recency(items)[: self.max_entries]

    async def _download(self, session: aiohttp.ClientSession) -> bytes:
        try:
            async with session.get(self._url) as resp:
                if resp.status != 200:
                    raise SourceError(f"HTTP {resp.status} sur {self._url}")
                return await resp.read()
        except aiohttp.ClientError as exc:
            raise SourceError(f"Echec reseau sur {self._url} : {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SourceError(f"Delai depasse sur {self._url}") from exc

    def _to_item(self, entry: Any, feed_author: str | None) -> Item | None:
        link = str(entry.get("link") or "").strip() or None
        raw_id = str(entry.get("id") or entry.get("guid") or "").strip()
        title = strip_html(entry.get("title"), limit=240)
        body = _entry_body(entry)

        uid = raw_id or link or (stable_uid(title, body[:200]) if (title or body) else "")
        if not uid:
            return None

        author = str(entry.get("author") or "") or feed_author
        return Item(
            uid=uid,
            url=link,
            title=title,
            summary=strip_html(body),
            author=author,
            published=_entry_datetime(entry),
            thumbnail=_entry_thumbnail(entry),
        )

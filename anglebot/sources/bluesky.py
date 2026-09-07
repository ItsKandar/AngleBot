"""Source Bluesky via l'API AppView publique (aucune authentification)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .base import Item, Source, SourceError, order_by_recency, strip_html

log = logging.getLogger(__name__)

API_BASE = "https://public.api.bsky.app/xrpc"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _embed_thumbnail(embed: dict[str, Any] | None) -> str | None:
    if not isinstance(embed, dict):
        return None
    images = embed.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            thumb = first.get("thumb") or first.get("fullsize")
            if thumb:
                return str(thumb)
    external = embed.get("external")
    if isinstance(external, dict) and external.get("thumb"):
        return str(external["thumb"])
    # Post cite / record-with-media : la media reelle est imbriquee.
    return _embed_thumbnail(embed.get("media"))


class BlueskySource(Source):
    """Suit les publications d'un compte Bluesky."""

    type_name = "bluesky"
    color = 0x1185FE
    platform_label = "Bluesky"

    limit = 20

    def __init__(self, raw: dict[str, Any], bridges: dict[str, str]) -> None:
        handle = str(raw.get("handle") or raw.get("actor") or "").strip().lstrip("@")
        if not handle:
            raise SourceError("Source bluesky : 'handle' manquant (ex. nom.bsky.social).")
        self.handle = handle
        self.include_reposts = bool(raw.get("include_reposts", False))
        self.include_replies = bool(raw.get("include_replies", False))
        super().__init__(raw, bridges)

    @property
    def identifier(self) -> str:
        return self.handle

    @property
    def profile_url(self) -> str:
        return f"https://bsky.app/profile/{self.handle}"

    def default_name(self) -> str:
        return f"Bluesky - @{self.handle}"

    async def fetch(self, session: aiohttp.ClientSession) -> list[Item]:
        params = {
            "actor": self.handle,
            "limit": str(self.limit),
            "filter": "posts_with_replies" if self.include_replies else "posts_no_replies",
        }
        url = f"{API_BASE}/app.bsky.feed.getAuthorFeed?{urlencode(params)}"
        try:
            async with session.get(url, headers={"Accept": "application/json"}) as resp:
                if resp.status == 400:
                    body = await resp.json(content_type=None)
                    raise SourceError(
                        f"Compte Bluesky introuvable ({self.handle}) : "
                        f"{body.get('message', 'requete refusee')}"
                    )
                if resp.status != 200:
                    raise SourceError(f"HTTP {resp.status} sur l'API Bluesky ({self.handle})")
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise SourceError(f"Echec reseau sur l'API Bluesky : {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SourceError("Delai depasse sur l'API Bluesky") from exc

        items: list[Item] = []
        for entry in payload.get("feed") or []:
            item = self._to_item(entry)
            if item is not None:
                items.append(item)
        return order_by_recency(items)

    def _to_item(self, entry: dict[str, Any]) -> Item | None:
        if not isinstance(entry, dict):
            return None
        is_repost = isinstance(entry.get("reason"), dict)
        if is_repost and not self.include_reposts:
            return None

        post = entry.get("post")
        if not isinstance(post, dict):
            return None
        uri = str(post.get("uri") or "")
        if not uri:
            return None

        author = post.get("author") or {}
        author_handle = str(author.get("handle") or self.handle)
        display = str(author.get("displayName") or "") or f"@{author_handle}"
        record = post.get("record") or {}
        text = strip_html(str(record.get("text") or ""), limit=1000)

        rkey = uri.rsplit("/", 1)[-1]
        post_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}"

        prefix = "Repost - " if is_repost else ""
        # Les reposts partagent l'uri du post original : on prefixe l'uid pour
        # ne pas confondre les deux evenements.
        uid = f"repost:{uri}" if is_repost else uri

        return Item(
            uid=uid,
            url=post_url,
            title=f"{prefix}{display}",
            summary=text,
            author=f"@{author_handle}",
            published=_parse_iso(record.get("createdAt")) or _parse_iso(post.get("indexedAt")),
            thumbnail=_embed_thumbnail(post.get("embed")),
            extra={
                "likes": post.get("likeCount"),
                "reposts": post.get("repostCount"),
                "replies": post.get("replyCount"),
                "avatar": author.get("avatar"),
            },
        )

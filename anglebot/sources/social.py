"""Sources sociales reposant sur une passerelle RSS (RSSHub).

TikTok, Instagram, X et Threads n'exposent aucune API publique gratuite pour
suivre un compte. On passe donc par une passerelle qui traduit le profil en
flux RSS. La passerelle est configurable : renseignez `url` dans la source pour
utiliser n'importe quel autre pont.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from .base import SourceError
from .rss import RssSource


def _rsshub(bridges: dict[str, str], route: str) -> str:
    base = (bridges.get("rsshub") or "").rstrip("/")
    if not base:
        raise SourceError(
            "Aucune passerelle RSSHub configuree : renseignez RSSHUB_BASE dans .env "
            "ou fournissez directement 'url' pour cette source."
        )
    url = f"{base}/{route.lstrip('/')}"
    key = bridges.get("rsshub_key")
    if key:
        url = f"{url}?{urlencode({'key': key})}"
    return url


class HandleSource(RssSource):
    """Base des sources identifiees par un pseudo de compte."""

    def build_url(self, raw: dict[str, Any], bridges: dict[str, str]) -> str:
        explicit = str(raw.get("url") or "").strip()
        if explicit:
            return explicit
        handle = self._handle(raw)
        return self.bridge_url(handle, bridges)

    @staticmethod
    def _handle(raw: dict[str, Any]) -> str:
        handle = str(raw.get("handle") or raw.get("account") or raw.get("user") or "").strip()
        handle = handle.lstrip("@").strip("/")
        if not handle:
            raise SourceError(f"Source {raw.get('type')!r} : 'handle' manquant.")
        return handle

    def bridge_url(self, handle: str, bridges: dict[str, str]) -> str:  # pragma: no cover
        raise NotImplementedError

    def __init__(self, raw: dict[str, Any], bridges: dict[str, str]) -> None:
        self.handle = self._handle(raw)
        super().__init__(raw, bridges)

    @property
    def identifier(self) -> str:
        return self.handle

    def default_name(self) -> str:
        return f"{self.platform_label} - @{self.handle}"


class TikTokSource(HandleSource):
    type_name = "tiktok"
    color = 0x00F2EA
    platform_label = "TikTok"

    def bridge_url(self, handle: str, bridges: dict[str, str]) -> str:
        return _rsshub(bridges, f"tiktok/user/@{quote(handle)}")

    @property
    def profile_url(self) -> str:
        return f"https://www.tiktok.com/@{self.handle}"


class InstagramSource(HandleSource):
    type_name = "instagram"
    color = 0xE1306C
    platform_label = "Instagram"

    def bridge_url(self, handle: str, bridges: dict[str, str]) -> str:
        return _rsshub(bridges, f"instagram/user/{quote(handle)}")

    @property
    def profile_url(self) -> str:
        return f"https://www.instagram.com/{self.handle}/"


class ThreadsSource(HandleSource):
    type_name = "threads"
    color = 0x101010
    platform_label = "Threads"

    def bridge_url(self, handle: str, bridges: dict[str, str]) -> str:
        return _rsshub(bridges, f"threads/{quote(handle)}")

    @property
    def profile_url(self) -> str:
        return f"https://www.threads.net/@{self.handle}"


class XSource(HandleSource):
    type_name = "x"
    color = 0x1D9BF0
    platform_label = "X"

    def bridge_url(self, handle: str, bridges: dict[str, str]) -> str:
        # La route twitter de RSSHub demande des cookies de session cote
        # passerelle. Pour un autre pont, renseignez 'url' sur la source.
        return _rsshub(bridges, f"twitter/user/{quote(handle)}")

    @property
    def profile_url(self) -> str:
        return f"https://x.com/{self.handle}"


class YouTubeSource(RssSource):
    """Flux officiel d'une chaine YouTube (aucune passerelle necessaire)."""

    type_name = "youtube"
    color = 0xFF0000
    platform_label = "YouTube"

    def build_url(self, raw: dict[str, Any], bridges: dict[str, str]) -> str:
        self._label = str(
            raw.get("channel") or raw.get("playlist") or raw.get("url") or ""
        ).strip()
        explicit = str(raw.get("url") or "").strip()
        if explicit:
            return explicit
        channel = str(raw.get("channel") or "").strip()
        if channel.startswith("UC"):
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={quote(channel)}"
        playlist = str(raw.get("playlist") or "").strip()
        if playlist:
            return f"https://www.youtube.com/feeds/videos.xml?playlist_id={quote(playlist)}"
        raise SourceError(
            "Source youtube : fournissez 'channel' (identifiant UC...), 'playlist' ou 'url'."
        )

    def default_name(self) -> str:
        return f"{self.platform_label} - {getattr(self, '_label', None) or self.identifier}"

"""Construction et envoi des notifications Discord."""

from __future__ import annotations

import logging

import discord

from .sources import Item, Source

log = logging.getLogger(__name__)

EMBED_DESCRIPTION_LIMIT = 4000
EMBED_TITLE_LIMIT = 256


class ChannelError(RuntimeError):
    """Salon cible inutilisable (introuvable, mauvais type, droits manquants)."""


def build_embed(source: Source, item: Item) -> discord.Embed:
    title = item.title or f"Nouvelle publication - {source.name}"
    embed = discord.Embed(
        title=title[:EMBED_TITLE_LIMIT],
        url=item.url or source.profile_url,
        description=(item.summary or "")[:EMBED_DESCRIPTION_LIMIT] or None,
        colour=discord.Colour(source.color),
        timestamp=item.published,
    )

    author_name = item.author or source.name
    embed.set_author(name=author_name[:256], url=source.profile_url)
    if avatar := item.extra.get("avatar"):
        embed.set_thumbnail(url=str(avatar))
    if item.thumbnail:
        embed.set_image(url=item.thumbnail)

    stats = " · ".join(
        f"{label} {value}"
        for label, key in (("♥", "likes"), ("🔁", "reposts"), ("💬", "replies"))
        if isinstance(value := item.extra.get(key), int)
    )
    embed.set_footer(text=f"{source.platform_label} · {stats}" if stats else source.platform_label)
    return embed


def resolve_channel(
    client: discord.Client, channel_id: int
) -> discord.abc.Messageable:
    """Retourne le salon cible ou leve ChannelError."""
    channel = client.get_channel(channel_id)
    if channel is None:
        raise ChannelError(
            f"Salon {channel_id} introuvable : le bot y a-t-il acces ?"
        )
    if not isinstance(channel, discord.abc.Messageable):
        raise ChannelError(f"Le salon {channel_id} n'accepte pas de messages.")
    return channel


async def send_item(
    channel: discord.abc.Messageable,
    source: Source,
    item: Item,
    role_id: int | None = None,
) -> None:
    """Publie une notification. Les erreurs Discord remontent a l'appelant."""
    content = f"<@&{role_id}>" if role_id else None
    await channel.send(
        content=content,
        embed=build_embed(source, item),
        allowed_mentions=discord.AllowedMentions(
            everyone=False, users=False, roles=bool(role_id)
        ),
    )

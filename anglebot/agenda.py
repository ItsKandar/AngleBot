"""Agenda du site Angle Gauche depuis Discord (/agenda ...).

Les evenements sont crees directement dans la collection Directus `events`,
que le site lit pour son agenda : un evenement cree ici est en ligne
immediatement (le Flow Directus de vidage du cache du site se declenche a
chaque creation). Le jeton utilise (DIRECTUS_AGENDA_TOKEN) n'a que les droits
de creer et lire des evenements et d'envoyer leur affiche.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import AngleBot

log = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
MAX_POSTER_BYTES = 10 * 1024 * 1024

_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]

#: "14/10/2026 18:00", "14/10/2026 18h30", "14/10/26 18h", "14/10 18:00"...
_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?"
    r"(?:\s+|\s*[aà]\s*|\s*,\s*)(\d{1,2})\s*[h:](\s*\d{2})?\s*$",
    re.IGNORECASE,
)


class AgendaError(RuntimeError):
    """Erreur a montrer telle quelle a l'utilisateur."""


@dataclass(slots=True)
class DraftEvent:
    title: str
    starts_at: datetime
    location: str
    organizer: str
    description: str
    link: str | None


def parse_start(raw: str, now: datetime | None = None) -> datetime:
    """Lit une date/heure saisie a la francaise, en heure de Paris.

    Sans annee, prend la prochaine occurrence (un 14/01 saisi en decembre
    designe l'annee suivante).
    """
    match = _DATE_RE.match(raw)
    if not match:
        raise AgendaError(
            f"Date non reconnue : « {raw} ». Format attendu : `14/10/2026 18:00` "
            "(ou `14/10 18h30`)."
        )
    day, month, year, hour, minute = match.groups()
    now = (now or datetime.now(PARIS)).astimezone(PARIS)
    try:
        if year is None:
            candidate = datetime(now.year, int(month), int(day), int(hour),
                                 int(minute or 0), tzinfo=PARIS)
            if candidate < now:
                candidate = candidate.replace(year=now.year + 1)
            return candidate
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        return datetime(full_year, int(month), int(day), int(hour), int(minute or 0),
                        tzinfo=PARIS)
    except ValueError as exc:
        raise AgendaError(f"Date impossible : « {raw} » ({exc}).") from None


def french_datetime(moment: datetime) -> str:
    local = moment.astimezone(PARIS)
    return (
        f"{_DAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]} "
        f"{local.year} a {local:%H:%M}"
    )


class DirectusAgenda:
    """Appels a l'API Directus limites a ce dont l'agenda a besoin."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _json(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            payload = {}
        if response.status in (401, 403):
            raise AgendaError(
                "Directus refuse le jeton du bot (DIRECTUS_AGENDA_TOKEN) : "
                "verifiez qu'il correspond au compte « Bot Agenda »."
            )
        if response.status >= 400:
            errors = payload.get("errors") if isinstance(payload, dict) else None
            detail = errors[0].get("message") if errors else f"HTTP {response.status}"
            raise AgendaError(f"Directus a refuse la requete : {detail}")
        return payload if isinstance(payload, dict) else {}

    async def upload_poster(self, attachment: discord.Attachment, title: str) -> str:
        content = await attachment.read()
        form = aiohttp.FormData()
        form.add_field("title", f"Affiche - {title}")
        form.add_field(
            "file",
            content,
            filename=attachment.filename,
            content_type=attachment.content_type or "application/octet-stream",
        )
        async with self.session.post(
            f"{self.base_url}/files", data=form, headers=self.headers
        ) as response:
            payload = await self._json(response)
        file_id = (payload.get("data") or {}).get("id")
        if not file_id:
            raise AgendaError("Directus n'a pas renvoye d'identifiant pour l'affiche.")
        return str(file_id)

    async def create_event(self, draft: DraftEvent, image_id: str | None) -> str:
        body: dict[str, Any] = {
            "status": "published",
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": draft.title,
            "starts_at": draft.starts_at.isoformat(timespec="seconds"),
            "location_label": draft.location or None,
            "organizer": draft.organizer or None,
            "description": draft.description or None,
            "link": draft.link,
        }
        if image_id:
            body["image"] = image_id
        async with self.session.post(
            f"{self.base_url}/items/events",
            json=body,
            headers={**self.headers, "Content-Type": "application/json"},
        ) as response:
            payload = await self._json(response)
        event_id = (payload.get("data") or {}).get("id")
        if event_id is None:
            raise AgendaError("Directus n'a pas renvoye l'identifiant de l'evenement.")
        return str(event_id)

    async def upcoming(self, limit: int = 10) -> list[dict[str, Any]]:
        params = {
            "filter[status][_eq]": "published",
            "filter[starts_at][_gte]": "$NOW",
            "sort": "starts_at",
            "fields": "id,title,starts_at,location_label,organizer",
            "limit": str(limit),
        }
        async with self.session.get(
            f"{self.base_url}/items/events", params=params, headers=self.headers
        ) as response:
            payload = await self._json(response)
        data = payload.get("data")
        return data if isinstance(data, list) else []


class EventModal(discord.ui.Modal, title="Ajouter a l'agenda du site"):
    """Formulaire Discord (5 champs maximum, limite imposee par Discord)."""

    titre = discord.ui.TextInput(label="Titre", max_length=200)
    date = discord.ui.TextInput(
        label="Date et heure (heure de Paris)", placeholder="14/10/2026 18:00", max_length=40
    )
    lieu = discord.ui.TextInput(
        label="Lieu", placeholder="Maison des syndicats, Reims", required=False, max_length=200
    )
    organisateur = discord.ui.TextInput(
        label="Organisateur", placeholder="AG antifa Reims, CGT 51...", required=False,
        max_length=200,
    )
    description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, required=False,
        max_length=4000,
    )

    def __init__(self, cog: "AgendaCog", link: str | None,
                 poster: discord.Attachment | None) -> None:
        super().__init__()
        self.cog = cog
        self.link = link
        self.poster = poster

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.publish(interaction, self)


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class AgendaCog(commands.GroupCog, name="agenda", description="Agenda du site Angle Gauche"):
    """Publication d'evenements sur l'agenda du site, reservee aux admins."""

    def __init__(self, bot: "AngleBot") -> None:
        self.bot = bot
        super().__init__()

    def _client(self) -> DirectusAgenda:
        assert self.bot.session is not None
        agenda = self.bot.config.agenda
        return DirectusAgenda(self.bot.session, agenda.directus_url, agenda.token)

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and member.guild_permissions.administrator

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # default_permissions ne fait que masquer la commande : un admin du
        # serveur peut la rouvrir a d'autres dans Integrations. On reverifie.
        if self._is_admin(interaction):
            return True
        await interaction.response.send_message(
            "Commande reservee aux administrateurs du serveur.", ephemeral=True
        )
        return False

    @app_commands.command(name="ajouter", description="Publier un evenement sur l'agenda du site")
    @app_commands.describe(
        lien="Lien vers plus d'infos (facultatif)",
        affiche="Affiche de l'evenement, en image (facultatif)",
    )
    async def ajouter(
        self,
        interaction: discord.Interaction,
        lien: str | None = None,
        affiche: discord.Attachment | None = None,
    ) -> None:
        if lien is not None:
            lien = lien.strip()
            if not lien.startswith(("http://", "https://")):
                await interaction.response.send_message(
                    "Le lien doit commencer par `https://`.", ephemeral=True
                )
                return
        if affiche is not None:
            if not (affiche.content_type or "").startswith("image/"):
                await interaction.response.send_message(
                    "L'affiche doit etre une image (PNG, JPEG, WebP...).", ephemeral=True
                )
                return
            if affiche.size > MAX_POSTER_BYTES:
                await interaction.response.send_message(
                    "L'affiche depasse 10 Mo.", ephemeral=True
                )
                return
        await interaction.response.send_modal(EventModal(self, lien, affiche))

    async def publish(self, interaction: discord.Interaction, modal: EventModal) -> None:
        typed = (
            f"Titre : {modal.titre.value}\nDate : {modal.date.value}\n"
            f"Lieu : {modal.lieu.value}\nOrganisateur : {modal.organisateur.value}\n"
            f"Description : {modal.description.value}"
        )
        try:
            starts_at = parse_start(modal.date.value)
        except AgendaError as exc:
            # Le formulaire se ferme sur erreur : on renvoie la saisie pour
            # qu'elle puisse etre recopiee au lieu d'etre perdue.
            await interaction.response.send_message(
                f"{exc}\n\nVotre saisie, a recopier :\n```\n{typed[:1700]}\n```",
                ephemeral=True,
            )
            return
        if starts_at < datetime.now(PARIS):
            await interaction.response.send_message(
                f"Cette date est deja passee ({french_datetime(starts_at)}).\n\n"
                f"Votre saisie, a recopier :\n```\n{typed[:1700]}\n```",
                ephemeral=True,
            )
            return

        draft = DraftEvent(
            title=modal.titre.value.strip(),
            starts_at=starts_at,
            location=modal.lieu.value.strip(),
            organizer=modal.organisateur.value.strip(),
            description=modal.description.value.strip(),
            link=modal.link,
        )
        await interaction.response.defer(ephemeral=True, thinking=True)

        client = self._client()
        try:
            image_id = (
                await client.upload_poster(modal.poster, draft.title) if modal.poster else None
            )
            event_id = await client.create_event(draft, image_id)
        except AgendaError as exc:
            await interaction.followup.send(
                f"Evenement non publie : {exc}\n\nVotre saisie :\n```\n{typed[:1700]}\n```",
                ephemeral=True,
            )
            return
        except aiohttp.ClientError as exc:
            log.warning("Directus injoignable : %s", exc)
            await interaction.followup.send(
                f"Directus est injoignable ({exc.__class__.__name__}), rien n'a ete publie.\n\n"
                f"Votre saisie :\n```\n{typed[:1700]}\n```",
                ephemeral=True,
            )
            return

        url = f"{self.bot.config.agenda.site_url.rstrip('/')}/evenement.php?id={event_id}"
        log.info("Evenement %s publie par %s : %s", event_id, interaction.user, draft.title)
        embed = discord.Embed(
            title=draft.title, url=url, description=draft.description[:500] or None,
            colour=discord.Colour.green(),
        )
        embed.add_field(name="Quand", value=french_datetime(draft.starts_at), inline=False)
        if draft.location:
            embed.add_field(name="Lieu", value=draft.location, inline=True)
        if draft.organizer:
            embed.add_field(name="Organisateur", value=draft.organizer, inline=True)
        if draft.link:
            embed.add_field(name="Lien", value=draft.link, inline=False)
        if modal.poster:
            embed.set_thumbnail(url=modal.poster.url)
        embed.set_footer(text="Publie sur l'agenda du site · modifiable dans Directus")
        await interaction.followup.send("Evenement publie sur le site.", embed=embed,
                                        ephemeral=True)

    @app_commands.command(name="prochains", description="Lister les prochains evenements du site")
    async def prochains(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            events = await self._client().upcoming()
        except (AgendaError, aiohttp.ClientError) as exc:
            await interaction.followup.send(f"Lecture impossible : {exc}", ephemeral=True)
            return
        site = self.bot.config.agenda.site_url.rstrip("/")
        lines = []
        for event in events:
            try:
                when = french_datetime(datetime.fromisoformat(
                    str(event.get("starts_at", "")).replace("Z", "+00:00")))
            except ValueError:
                when = "date inconnue"
            extra = " · ".join(
                part for part in (event.get("location_label"), event.get("organizer")) if part
            )
            lines.append(
                f"**[{event.get('title', '?')}]({site}/evenement.php?id={event.get('id')})** — "
                f"{when}" + (f"\n{extra}" if extra else "")
            )
        embed = discord.Embed(
            title="Prochains evenements",
            description="\n\n".join(lines) if lines else "Aucun evenement a venir.",
            colour=discord.Colour.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

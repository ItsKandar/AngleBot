"""Commandes slash de gestion de la veille (/veille ...)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from .notifier import build_embed
from .sources import REGISTRY, SourceError, build_source, order_by_recency

if TYPE_CHECKING:
    from .bot import AngleBot

log = logging.getLogger(__name__)

TYPE_CHOICES = [
    app_commands.Choice(name="RSS / Atom", value="rss"),
    app_commands.Choice(name="Bluesky", value="bluesky"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="Instagram", value="instagram"),
    app_commands.Choice(name="X (Twitter)", value="x"),
    app_commands.Choice(name="Threads", value="threads"),
    app_commands.Choice(name="YouTube", value="youtube"),
]

#: cle de configuration attendue selon le type de source
TARGET_FIELD = {"rss": "url", "youtube": "channel"}


def _describe(target: str, type_name: str) -> dict[str, Any]:
    """Traduit (type, cible) en description de source."""
    field = TARGET_FIELD.get(type_name, "handle")
    if type_name == "youtube" and target.startswith(("http://", "https://")):
        field = "url"
    return {"type": type_name, field: target.strip()}


class WatchCog(commands.GroupCog, name="veille", description="Gerer la veille RSS et sociale"):
    """Ajout, retrait et inspection des sources surveillees."""

    def __init__(self, bot: "AngleBot") -> None:
        self.bot = bot
        super().__init__()

    # -- helpers ----------------------------------------------------------
    def _source_lines(self) -> list[str]:
        lines = []
        for source in sorted(self.bot.sources, key=lambda s: (s.type_name, s.name.lower())):
            status = self.bot.statuses.get(source.key)
            channel_id = self.bot.channel_for(source)
            target = f"<#{channel_id}>" if channel_id else "**aucun salon**"
            mark = "✅"
            detail = ""
            if status and status.last_error:
                mark = "⚠️"
                detail = f" — {status.last_error[:120]}"
            elif status and status.last_check is None:
                mark = "⏳"
            origin = "config" if source.origin == "config" else "/veille"
            lines.append(
                f"{mark} **{source.name}** · `{source.type_name}` → {target} "
                f"*({origin})*{detail}"
            )
        return lines

    # -- commandes --------------------------------------------------------
    @app_commands.command(name="liste", description="Lister les sources surveillees")
    async def liste(self, interaction: discord.Interaction) -> None:
        lines = self._source_lines()
        embed = discord.Embed(
            title="Sources surveillees",
            description="\n".join(lines) if lines else "Aucune source configuree.",
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(
            text=f"{len(lines)} source(s) · verification toutes les "
            f"{self.bot.config.poll_interval_seconds}s"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ajouter", description="Ajouter une source a surveiller")
    @app_commands.describe(
        type="Plateforme ou type de flux",
        cible="URL du flux, pseudo du compte, ou identifiant de chaine YouTube",
        salon="Salon de publication (defaut : salon global)",
        role="Role a mentionner a chaque notification",
        nom="Nom affiche dans les notifications",
    )
    @app_commands.choices(type=TYPE_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def ajouter(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        cible: str,
        salon: discord.TextChannel | None = None,
        role: discord.Role | None = None,
        nom: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        entry = _describe(cible, type.value)
        if nom:
            entry["name"] = nom
        if salon:
            entry["channel_id"] = salon.id
        if role:
            entry["role_id"] = role.id

        try:
            source = build_source(entry, self.bot.config.bridges)
        except SourceError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        if any(existing.key == source.key for existing in self.bot.sources):
            await interaction.followup.send(
                f"⚠️ **{source.name}** est deja surveillee.", ephemeral=True
            )
            return

        # On verifie que la source repond avant de l'enregistrer.
        try:
            items = await source.fetch(self.bot.session)
        except SourceError as exc:
            await interaction.followup.send(
                f"❌ Source injoignable : {exc}\n"
                f"Flux teste : `{getattr(source, 'url', source.identifier)}`",
                ephemeral=True,
            )
            return

        self.bot.subscriptions.add(entry)
        self.bot.reload_sources()

        # Le premier passage sert de reference : pas de rattrapage d'historique.
        self.bot.seen.mark(source.key, [item.uid for item in items])
        self.bot.seen.save()

        channel_id = self.bot.channel_for(source)
        await interaction.followup.send(
            f"✅ **{source.name}** ajoutee ({len(items)} publication(s) en reference).\n"
            f"Notifications dans "
            f"{f'<#{channel_id}>' if channel_id else '**aucun salon defini**'}.",
            ephemeral=True,
        )

    @app_commands.command(name="retirer", description="Retirer une source ajoutee via /veille")
    @app_commands.describe(source="Source a retirer")
    @app_commands.default_permissions(manage_guild=True)
    async def retirer(self, interaction: discord.Interaction, source: str) -> None:
        target = next((s for s in self.bot.sources if s.key == source), None)
        if target is None:
            await interaction.response.send_message(
                "❌ Source inconnue. Utilisez l'autocompletion.", ephemeral=True
            )
            return
        if target.origin == "config":
            await interaction.response.send_message(
                f"❌ **{target.name}** vient de `config.yaml` : "
                "retirez-la du fichier puis redemarrez le bot.",
                ephemeral=True,
            )
            return

        removed = self.bot.subscriptions.remove(
            lambda entry: _entry_key(entry, self.bot) == source
        )
        self.bot.reload_sources()
        self.bot.seen.forget(source)
        self.bot.seen.save()
        await interaction.response.send_message(
            f"🗑️ **{target.name}** retiree ({removed} entree(s))." , ephemeral=True
        )

    @retirer.autocomplete("source")
    async def retirer_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        needle = current.lower()
        choices = [
            app_commands.Choice(name=f"{s.name} [{s.type_name}]"[:100], value=s.key[:100])
            for s in self.bot.sources
            if s.origin != "config" and (needle in s.name.lower() or needle in s.key.lower())
        ]
        return choices[:25]

    @app_commands.command(name="verifier", description="Forcer une verification immediate")
    @app_commands.default_permissions(manage_guild=True)
    async def verifier(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await self.bot.check_all()
        lines = [
            f"🔍 {report.checked} source(s) verifiee(s)",
            f"📣 {report.announced} notification(s) envoyee(s)",
        ]
        if report.errors:
            details = "\n".join(f"• {err[:200]}" for err in report.errors[:5])
            lines.append(f"⚠️ {len(report.errors)} erreur(s) :\n{details}")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="apercu", description="Tester une source et voir sa derniere publication"
    )
    @app_commands.describe(type="Plateforme ou type de flux", cible="URL ou pseudo du compte")
    @app_commands.choices(type=TYPE_CHOICES)
    async def apercu(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        cible: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            source = build_source(_describe(cible, type.value), self.bot.config.bridges)
            items = order_by_recency(await source.fetch(self.bot.session))
        except SourceError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send(
                f"⚠️ **{source.name}** repond mais ne contient aucune publication.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{source.name}** — {len(items)} publication(s), voici la plus recente :",
            embed=build_embed(source, items[0]),
            ephemeral=True,
        )

    @app_commands.command(name="etat", description="Etat du bot et de la boucle de veille")
    async def etat(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        next_run = bot.poll_loop.next_iteration
        errored = [s for s in bot.sources if (st := bot.statuses.get(s.key)) and st.last_error]
        embed = discord.Embed(title="Etat d'AngleBot", colour=discord.Colour.blurple())
        embed.add_field(name="Sources", value=str(len(bot.sources)))
        embed.add_field(name="En erreur", value=str(len(errored)))
        embed.add_field(name="Latence", value=f"{bot.latency * 1000:.0f} ms")
        embed.add_field(
            name="Prochaine verification",
            value=discord.utils.format_dt(next_run, "R") if next_run else "boucle arretee",
        )
        embed.add_field(
            name="Demarre", value=discord.utils.format_dt(bot.started_at, "R"), inline=False
        )
        embed.add_field(
            name="Types disponibles", value=", ".join(sorted(REGISTRY)), inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _entry_key(entry: dict[str, Any], bot: "AngleBot") -> str | None:
    """Recalcule la cle d'une entree d'abonnement pour la comparer."""
    try:
        return build_source(entry, bot.config.bridges).key
    except (SourceError, TypeError, ValueError):
        return None

"""Attribution automatique d'un role aux nouveaux membres.

Necessite l'intent privilegie *Server Members* et la permission *Gerer les roles*,
avec le role du bot place **au-dessus** du role attribue dans la hierarchie.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import AngleBot

log = logging.getLogger(__name__)

#: pause entre deux attributions lors d'un rattrapage
GRANT_DELAY_SECONDS = 0.6
#: plafond de membres traites par appel a /autorole rattraper
BACKFILL_LIMIT = 500


def diagnose(guild: discord.Guild, role_id: int) -> tuple[discord.Role | None, list[str]]:
    """Retourne le role vise et la liste des problemes bloquants."""
    problems: list[str] = []
    role = guild.get_role(role_id)
    if role is None:
        return None, [f"Le role `{role_id}` n'existe pas sur ce serveur."]

    me = guild.me
    if me is None:
        return role, ["Le bot n'est pas encore initialise sur ce serveur."]
    if not me.guild_permissions.manage_roles:
        problems.append("Le bot n'a pas la permission **Gerer les roles**.")
    if role >= me.top_role:
        problems.append(
            f"**{role.name}** est au-dessus ou au meme niveau que le role du bot "
            f"(**{me.top_role.name}**) : remontez le role du bot dans la hierarchie."
        )
    if role.managed:
        problems.append(
            f"**{role.name}** est gere par une integration : Discord interdit "
            "de l'attribuer manuellement."
        )
    if role.is_default():
        problems.append("Le role `@everyone` ne peut pas etre attribue.")
    return role, problems


class AutoRoleCog(commands.Cog):
    """Donne le role configure a chaque nouvelle arrivee."""

    def __init__(self, bot: "AngleBot") -> None:
        self.bot = bot
        self.config = bot.config.autorole
        self.granted = 0
        self.last_error: str | None = None
        # Evite de repeter la meme alerte a chaque arrivee.
        self._warned: set[int] = set()

    # -- evenements -------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self.config.wait_for_screening and member.pending:
            # Ecran de regles actif : on attend la validation (on_member_update).
            log.debug("%s doit valider le reglement, attribution differee.", member)
            return
        await self.grant(member, "arrivee")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.pending and not after.pending:
            await self.grant(after, "validation du reglement")

    # -- attribution ------------------------------------------------------
    async def grant(self, member: discord.Member, cause: str) -> bool:
        """Attribue le role. Retourne True si le role a bien ete ajoute."""
        role_id = self.config.role_id
        if role_id is None:
            return False
        if member.bot and not self.config.include_bots:
            return False

        role = member.guild.get_role(role_id)
        if role is None:
            # Le bot peut etre present sur d'autres serveurs : ce n'est pas une erreur.
            self._warn_once(
                member.guild.id,
                f"Role {role_id} absent de {member.guild.name!r} : autorole inactif ici.",
            )
            return False
        if role in member.roles:
            return False

        try:
            await member.add_roles(role, reason=f"AngleBot - autorole ({cause})")
        except discord.Forbidden:
            _, problems = diagnose(member.guild, role_id)
            self.last_error = " ".join(problems) or "Droits insuffisants."
            self._warn_once(
                member.guild.id,
                f"Attribution de {role.name!r} refusee : {self.last_error}",
            )
            return False
        except discord.HTTPException as exc:
            self.last_error = str(exc)
            log.warning("Echec d'attribution a %s : %s", member, exc)
            return False

        self.granted += 1
        self.last_error = None
        self._warned.discard(member.guild.id)
        log.info("Role %s attribue a %s (%s).", role.name, member, cause)
        return True

    def _warn_once(self, guild_id: int, message: str) -> None:
        if guild_id not in self._warned:
            self._warned.add(guild_id)
            log.error("%s", message)

    # -- commandes --------------------------------------------------------
    autorole = app_commands.Group(
        name="autorole",
        description="Role automatique des nouveaux membres",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @autorole.command(name="etat", description="Verifier la configuration du role automatique")
    async def etat(self, interaction: discord.Interaction) -> None:
        cfg = self.config
        if not cfg.active:
            await interaction.response.send_message(
                "⚪ Autorole desactive. Renseignez `autorole.enabled` et "
                "`autorole.role_id` dans `config.yaml`, puis redemarrez le bot.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None  # garanti par guild_only
        role, problems = diagnose(interaction.guild, cfg.role_id or 0)
        embed = discord.Embed(
            title="Role automatique",
            colour=discord.Colour.red() if problems else discord.Colour.green(),
        )
        embed.add_field(
            name="Role", value=role.mention if role else f"`{cfg.role_id}` (introuvable)"
        )
        embed.add_field(
            name="Intent membres",
            value="✅ actif" if self.bot.intents.members else "❌ desactive",
        )
        embed.add_field(name="Bots inclus", value="oui" if cfg.include_bots else "non")
        embed.add_field(
            name="Ecran de regles",
            value="attend la validation" if cfg.wait_for_screening else "ignore",
        )
        embed.add_field(name="Attributions depuis le demarrage", value=str(self.granted))
        embed.description = (
            "❌ " + "\n❌ ".join(problems)
            if problems
            else "✅ Tout est en place : les nouveaux membres recevront le role."
        )
        if self.last_error and not problems:
            embed.add_field(name="Derniere erreur", value=self.last_error[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole.command(
        name="rattraper",
        description="Donner le role aux membres actuels qui ne l'ont pas encore",
    )
    async def rattraper(self, interaction: discord.Interaction) -> None:
        cfg = self.config
        if not cfg.active:
            await interaction.response.send_message("⚪ Autorole desactive.", ephemeral=True)
            return

        assert interaction.guild is not None  # garanti par guild_only
        guild = interaction.guild
        role, problems = diagnose(guild, cfg.role_id or 0)
        if problems or role is None:
            await interaction.response.send_message(
                "❌ " + "\n❌ ".join(problems or ["Role introuvable."]), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        granted = failed = 0
        truncated = False
        try:
            async for member in guild.fetch_members(limit=None):
                if role in member.roles or (member.bot and not cfg.include_bots):
                    continue
                if granted + failed >= BACKFILL_LIMIT:
                    truncated = True
                    break
                if await self.grant(member, "rattrapage manuel"):
                    granted += 1
                else:
                    failed += 1
                await asyncio.sleep(GRANT_DELAY_SECONDS)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"⚠️ Rattrapage interrompu apres {granted} attribution(s) : {exc}",
                ephemeral=True,
            )
            return

        lines = [f"✅ {granted} membre(s) ont recu {role.mention}."]
        if failed:
            lines.append(f"⚠️ {failed} echec(s) — voir `/autorole etat`.")
        if truncated:
            lines.append(
                f"⏸️ Plafond de {BACKFILL_LIMIT} atteint : relancez la commande "
                "pour continuer."
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

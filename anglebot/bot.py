"""Client Discord : boucle de veille et commandes de gestion."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from .config import STATE_PATH, SUBSCRIPTIONS_PATH, Config
from .notifier import ChannelError, resolve_channel, send_item
from .sources import Item, Source, SourceError, build_sources, order_by_recency
from .state import SeenStore
from .subscriptions import SubscriptionStore

log = logging.getLogger(__name__)

#: nombre de sources interrogees en parallele
FETCH_CONCURRENCY = 5
#: pause entre deux envois pour rester loin des limites de debit Discord
SEND_DELAY_SECONDS = 1.0


@dataclass(slots=True)
class SourceStatus:
    """Derniere issue connue d'une source, affichee par /veille liste."""

    last_check: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_errors: int = 0
    announced: int = 0


@dataclass(slots=True)
class CheckReport:
    announced: int = 0
    errors: list[str] = field(default_factory=list)
    checked: int = 0


class AngleBot(commands.Bot):
    """Bot de veille RSS et reseaux sociaux."""

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        if config.autorole.active:
            # Intent privilegie, a activer aussi dans le portail developpeur :
            # sans lui, aucun evenement d'arrivee de membre n'est recu.
            intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.config = config
        self.seen = SeenStore(STATE_PATH, config.seen_history_size)
        self.subscriptions = SubscriptionStore(SUBSCRIPTIONS_PATH)
        self.sources: list[Source] = []
        self.statuses: dict[str, SourceStatus] = {}
        self.session: aiohttp.ClientSession | None = None
        self._check_lock = asyncio.Lock()
        self.started_at = datetime.now(timezone.utc)

    # -- cycle de vie -----------------------------------------------------
    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.config.http.timeout_seconds)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": self.config.http.user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                "text/xml, application/json;q=0.9, */*;q=0.8",
            },
        )
        self.reload_sources()

        from .autorole import AutoRoleCog
        from .commands import WatchCog

        await self.add_cog(WatchCog(self))
        await self.add_cog(AutoRoleCog(self))
        if self.config.autorole.active:
            log.info(
                "Autorole actif : role %s attribue aux nouveaux membres.",
                self.config.autorole.role_id,
            )
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commandes synchronisees sur la guilde %s.", self.config.guild_id)
        else:
            await self.tree.sync()
            log.info("Commandes synchronisees globalement (propagation possible en ~1h).")

        self.poll_loop.change_interval(seconds=self.config.poll_interval_seconds)
        self.poll_loop.start()

    async def close(self) -> None:
        self.poll_loop.cancel()
        self.seen.save()
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info(
            "Connecte comme %s - %d source(s) surveillee(s), verification toutes les %ds.",
            self.user,
            len(self.sources),
            self.config.poll_interval_seconds,
        )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.sources)} source(s)",
            )
        )

    # -- gestion des sources ----------------------------------------------
    def reload_sources(self) -> list[str]:
        """Reconstruit la liste des sources (config + abonnements Discord)."""
        raws = [
            {**raw, "origin": raw.get("origin", "config")} for raw in self.config.sources
        ] + self.subscriptions.entries
        sources, errors = build_sources(raws, self.config.bridges)
        self.sources = sources
        for error in errors:
            log.error("Source ignoree : %s", error)
        keys = {source.key for source in sources}
        self.statuses = {key: self.statuses.get(key, SourceStatus()) for key in keys}
        return errors

    def channel_for(self, source: Source) -> int | None:
        return source.channel_id or self.config.default_channel_id

    # -- boucle de veille -------------------------------------------------
    @tasks.loop(seconds=300)
    async def poll_loop(self) -> None:
        report = await self.check_all()
        log.info(
            "Verification terminee : %d source(s), %d notification(s), %d erreur(s).",
            report.checked,
            report.announced,
            len(report.errors),
        )

    @poll_loop.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()

    async def check_all(self, only: Source | None = None) -> CheckReport:
        """Interroge les sources et publie les nouveautes."""
        report = CheckReport()
        if self.session is None or self.session.closed:
            report.errors.append("Session HTTP indisponible.")
            return report

        targets = [only] if only else list(self.sources)
        if not targets:
            return report

        async with self._check_lock:
            semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

            async def collect(source: Source) -> tuple[Source, list[Item] | Exception]:
                async with semaphore:
                    try:
                        items = await source.fetch(self.session)
                        return source, order_by_recency(items)
                    except (SourceError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        return source, exc
                    except Exception as exc:  # noqa: BLE001 - une source ne doit pas tuer la boucle
                        log.exception("Erreur inattendue sur %s", source.key)
                        return source, exc

            results = await asyncio.gather(*(collect(src) for src in targets))

            for source, outcome in results:
                status = self.statuses.setdefault(source.key, SourceStatus())
                status.last_check = datetime.now(timezone.utc)
                report.checked += 1

                if isinstance(outcome, Exception):
                    status.consecutive_errors += 1
                    status.last_error = str(outcome) or type(outcome).__name__
                    message = f"{source.name} : {status.last_error}"
                    report.errors.append(message)
                    log.warning("Echec de collecte - %s", message)
                    continue

                status.consecutive_errors = 0
                status.last_error = None
                status.last_success = status.last_check

                try:
                    announced = await self._announce_new(source, outcome)
                except ChannelError as exc:
                    status.last_error = str(exc)
                    report.errors.append(f"{source.name} : {exc}")
                    log.error("Notification impossible - %s : %s", source.name, exc)
                    continue
                status.announced += announced
                report.announced += announced

            self.seen.save()
        return report

    async def _announce_new(self, source: Source, items: list[Item]) -> int:
        """Publie les items inconnus et memorise ceux traites."""
        uids = [item.uid for item in items]
        if not uids:
            return 0

        first_run = not self.seen.is_known(source.key)
        new_uids = set(self.seen.unseen(source.key, uids))
        if not new_uids:
            return 0

        if first_run and not self.config.announce_on_first_run:
            self.seen.mark(source.key, uids)
            log.info(
                "Premiere lecture de %s : %d publication(s) memorisee(s) sans notification.",
                source.name,
                len(new_uids),
            )
            return 0

        # Les sources renvoient du plus recent au plus ancien : on garde les N
        # plus recents et on publie dans l'ordre chronologique.
        fresh = [item for item in items if item.uid in new_uids]
        skipped = fresh[self.config.max_items_per_check :]
        to_send = list(reversed(fresh[: self.config.max_items_per_check]))

        if skipped:
            log.info(
                "%s : %d publication(s) plus ancienne(s) ignoree(s) (limite par cycle).",
                source.name,
                len(skipped),
            )
            self.seen.mark(source.key, [item.uid for item in skipped])

        channel_id = self.channel_for(source)
        if channel_id is None:
            raise ChannelError(
                "Aucun salon defini : renseignez DISCORD_DEFAULT_CHANNEL_ID "
                "ou 'channel_id' sur la source."
            )
        channel = resolve_channel(self, channel_id)

        sent = 0
        for index, item in enumerate(to_send):
            try:
                await send_item(channel, source, item, source.role_id)
            except discord.Forbidden as exc:
                raise ChannelError(
                    f"Droits insuffisants dans <#{channel_id}> ({exc.text})"
                ) from exc
            except discord.HTTPException as exc:
                # Item probleme (embed refuse, image invalide) : on le marque vu
                # pour ne pas bloquer la file, et on continue.
                log.warning("Envoi refuse pour %s (%s) : %s", source.name, item.url, exc)
                self.seen.mark(source.key, [item.uid])
                continue

            self.seen.mark(source.key, [item.uid])
            sent += 1
            if index + 1 < len(to_send):
                await asyncio.sleep(SEND_DELAY_SECONDS)

        return sent

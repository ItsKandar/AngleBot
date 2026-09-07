#!/usr/bin/env python3
"""Point d'entree d'AngleBot : `python main.py`."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from dotenv import load_dotenv


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    # AngleBot n'utilise pas la voix : on masque les avertissements associes.
    logging.getLogger("discord.client").addFilter(
        lambda record: "voice will NOT be supported" not in record.getMessage()
    )


async def run() -> int:
    from anglebot.bot import AngleBot
    from anglebot.config import Config, ConfigError

    log = logging.getLogger("anglebot")
    try:
        config = Config.load()
    except ConfigError as exc:
        log.error("Configuration invalide : %s", exc)
        return 2

    bot = AngleBot(config)
    try:
        await bot.start(config.token)
    except discord.LoginFailure:
        log.error("Token Discord refuse : verifiez DISCORD_TOKEN dans le .env.")
        return 3
    except discord.PrivilegedIntentsRequired:
        log.error(
            "L'intent privilegie 'Server Members' est requis par autorole mais desactive. "
            "Activez-le dans le portail developpeur (onglet Bot > Privileged Gateway "
            "Intents > Server Members Intent), ou mettez autorole.enabled a false."
        )
        return 4
    finally:
        if not bot.is_closed():
            await bot.close()
    return 0


def main() -> int:
    load_dotenv()
    setup_logging()
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("anglebot").info("Arret demande, etat sauvegarde.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

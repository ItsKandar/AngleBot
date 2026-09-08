#!/usr/bin/env python3
"""Verification hors ligne : configuration lisible, cogs chargeables, sources valides.

Ne contacte ni Discord ni les flux : conçu pour la CI et pour un controle rapide
avant de deployer. Sortie 0 = deployable, 1 = probleme bloquant.

    DISCORD_TOKEN=x DISCORD_DEFAULT_CHANNEL_ID=1 python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Valeurs factices : Config.load() exige un token, mais rien ne se connecte.
os.environ.setdefault("DISCORD_TOKEN", "smoke-test")
os.environ.setdefault("DISCORD_DEFAULT_CHANNEL_ID", "1")

ATTENDU = {
    "veille liste",
    "veille ajouter",
    "veille retirer",
    "veille verifier",
    "veille apercu",
    "veille etat",
    "autorole etat",
    "autorole rattraper",
}


def annoter(niveau: str, message: str) -> None:
    """Emet une annotation GitHub Actions, ou une ligne lisible en local."""
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{niveau}::{message}")
    else:
        print(f"[{niveau}] {message}")


async def main() -> int:
    from anglebot.autorole import AutoRoleCog
    from anglebot.bot import AngleBot
    from anglebot.commands import WatchCog
    from anglebot.config import Config, ConfigError
    from anglebot.sources import build_sources

    try:
        config = Config.load()
    except ConfigError as exc:
        annoter("error", f"config.yaml invalide : {exc}")
        return 1
    print(f"config.yaml lu : intervalle {config.poll_interval_seconds}s")

    sources, erreurs = build_sources(config.sources, config.bridges)
    print(f"{len(sources)} source(s) valide(s) : {', '.join(s.name for s in sources)}")
    for erreur in erreurs:
        # Une source cassee n'empeche pas le bot de tourner : on avertit.
        annoter("warning", f"source ignoree : {erreur}")

    bot = AngleBot(config)
    try:
        await bot.add_cog(WatchCog(bot))
        await bot.add_cog(AutoRoleCog(bot))
        commandes = {
            cmd.qualified_name
            for cmd in bot.tree.walk_commands()
            if not hasattr(cmd, "commands")
        }
        manquantes = ATTENDU - commandes
        if manquantes:
            annoter("error", f"commandes absentes de l'arbre : {sorted(manquantes)}")
            return 1
        print(f"{len(commandes)} commande(s) enregistree(s)")

        if config.autorole.active and not bot.intents.members:
            annoter("error", "autorole actif mais l'intent members n'est pas demande")
            return 1
        print(
            "autorole : "
            + (f"role {config.autorole.role_id}" if config.autorole.active else "desactive")
        )
    finally:
        await bot.close()

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

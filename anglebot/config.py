"""Chargement et validation de la configuration."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("ANGLEBOT_CONFIG", ROOT / "config.yaml"))
DATA_DIR = Path(os.getenv("ANGLEBOT_DATA_DIR", ROOT / "data"))
STATE_PATH = DATA_DIR / "state.json"
SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"


class ConfigError(RuntimeError):
    """Configuration absente ou invalide."""


def _env_snowflake(name: str) -> int | None:
    """Lit un identifiant Discord depuis l'environnement."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ConfigError(f"{name} doit etre un identifiant numerique Discord.")
    return int(raw)


def _expand(value: Any) -> Any:
    """Remplace recursivement les ${VAR} par les variables d'environnement.

    Une variable absente donne une chaine vide, jamais le litteral `${VAR}` :
    sans cela une passerelle non configuree produirait une URL absurde au lieu
    du message d'erreur explicatif. Seule la forme `${VAR}` est reconnue, pour
    ne pas avaler un `$` litteral dans un user-agent ou une URL.
    """
    if isinstance(value, str):
        return _VAR_RE.sub(lambda m: os.getenv(m.group(1), ""), value).strip()
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


@dataclass(slots=True)
class HttpConfig:
    timeout_seconds: int = 20
    user_agent: str = "AngleBot/1.0"


@dataclass(slots=True)
class AutoRoleConfig:
    """Attribution automatique d'un role aux nouveaux membres."""

    enabled: bool = False
    role_id: int | None = None
    include_bots: bool = False
    wait_for_screening: bool = True

    @property
    def active(self) -> bool:
        return self.enabled and self.role_id is not None


@dataclass(slots=True)
class Config:
    token: str
    default_channel_id: int | None
    guild_id: int | None = None
    poll_interval_seconds: int = 300
    announce_on_first_run: bool = False
    max_items_per_check: int = 5
    seen_history_size: int = 250
    http: HttpConfig = field(default_factory=HttpConfig)
    bridges: dict[str, str] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    autorole: AutoRoleConfig = field(default_factory=AutoRoleConfig)

    @classmethod
    def load(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "DISCORD_TOKEN est absent. Copiez .env.example vers .env "
                "et renseignez le token du bot."
            )

        raw: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            loaded = yaml.safe_load(CONFIG_PATH.read_text("utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ConfigError(f"{CONFIG_PATH} doit contenir un mapping YAML.")
            raw = _expand(loaded)
        else:
            log.warning("%s introuvable : configuration par defaut.", CONFIG_PATH)

        default_channel_id = _env_snowflake("DISCORD_DEFAULT_CHANNEL_ID")
        guild_id = _env_snowflake("DISCORD_GUILD_ID")

        http_raw = raw.get("http") or {}
        http = HttpConfig(
            timeout_seconds=int(http_raw.get("timeout_seconds", 20)),
            user_agent=str(http_raw.get("user_agent") or "AngleBot/1.0"),
        )

        bridges = {
            key: str(value)
            for key, value in (raw.get("bridges") or {}).items()
            if value not in (None, "")
        }

        autorole_raw = raw.get("autorole") or {}
        if not isinstance(autorole_raw, dict):
            raise ConfigError("La cle 'autorole' doit etre un mapping.")
        autorole_role = autorole_raw.get("role_id")
        if autorole_role is not None and not str(autorole_role).isdigit():
            raise ConfigError("autorole.role_id doit etre un identifiant numerique Discord.")
        autorole = AutoRoleConfig(
            enabled=bool(autorole_raw.get("enabled", False)),
            role_id=int(autorole_role) if autorole_role is not None else None,
            include_bots=bool(autorole_raw.get("include_bots", False)),
            wait_for_screening=bool(autorole_raw.get("wait_for_screening", True)),
        )
        if autorole.enabled and autorole.role_id is None:
            raise ConfigError("autorole.enabled vaut true mais autorole.role_id est absent.")

        sources = raw.get("sources") or []
        if not isinstance(sources, list):
            raise ConfigError("La cle 'sources' doit etre une liste.")

        cfg = cls(
            token=token,
            default_channel_id=default_channel_id,
            guild_id=guild_id,
            poll_interval_seconds=max(60, int(raw.get("poll_interval_seconds", 300))),
            announce_on_first_run=bool(raw.get("announce_on_first_run", False)),
            max_items_per_check=max(1, int(raw.get("max_items_per_check", 5))),
            seen_history_size=max(20, int(raw.get("seen_history_size", 250))),
            http=http,
            bridges=bridges,
            sources=[s for s in sources if isinstance(s, dict)],
            autorole=autorole,
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return cfg

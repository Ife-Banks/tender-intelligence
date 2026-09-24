""":mod:`tender_intelligence.config.settings` — operator env config."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    """Operator-supplied configuration, read from the environment (prefix ``TI_``).

    These are the *non-managed* values that never live in the database. Runtime-configurable
    values (recipients, providers, sources, triage) live in the DB (``docs/04`` §4.1); this
    class only holds the bootstrap seam, secrets, and infra pointers.
    """

    model_config = SettingsConfigDict(
        env_prefix="TI_", env_file=".env", extra="ignore", populate_by_name=True
    )

    database_url: str = Field(default="postgresql+psycopg://tender:tender@localhost:5432/tender_intelligence")
    master_key: str = Field(default="")
    dev_alert_email: str = Field(default="")
    log_level: str = Field(default="INFO")
    storage_dir: str = Field(default="./data/documents")
    #: HMAC key for secure expiring download links (docs/08 §8.9). When empty, the master
    #: key is used — both live outside the database, so a captured DB cannot forge links.
    link_signing_secret: str = Field(default="")
    #: Public root for generated secure links. The serving host is an open decision (O10),
    #: so this is a placeholder until the archive service gets a confirmed address.
    link_base_url: str = Field(default="https://archive.example.invalid")

    @property
    def has_master_key(self) -> bool:
        return bool(self.master_key)

    @property
    def storage_path(self) -> str:
        return self.storage_dir


@lru_cache
def get_env_settings() -> EnvSettings:
    """Return the (cached) environment settings singleton."""
    return EnvSettings()

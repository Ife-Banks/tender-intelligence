""":mod:`tender_intelligence.config.seed` — YAML bootstrap seed (write-only config)."""

from __future__ import annotations

import yaml
from sqlalchemy.orm import Session

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.crypto.secrets import encrypt_secret
from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.recipients import Recipient, is_valid_email
from tender_intelligence.db.models.sources import Source


class SeedError(Exception):
    """Raised when the bootstrap YAML is invalid or cannot be applied."""


def _note_actor_from_env() -> str:
    return get_correlation_id() or "env-seed"


def seed_dev_alert_recipient(session: Session, email: str) -> Recipient:
    """Seed the first dev-alert recipient from an environment variable (docs/04 §4.9).

    If an active dev recipient already exists, the seed is a no-op (host's value never
    clobbers the maintained list).
    """
    if not email or not is_valid_email(email):
        raise SeedError(f"invalid dev alert email: {email!r}")
    existing = (
        session.query(Recipient)
        .filter(Recipient.list_type == "dev_alert", Recipient.active.is_(True))
        .first()
    )
    if existing is not None:
        return existing
    recipient = Recipient(
        email=email,
        list_type="dev_alert",
        delivery="to",
        receives_filter="all",
        min_severity="info",
        active=True,
    )
    session.add(recipient)
    session.flush()
    log_config_change(
        session,
        actor=_note_actor_from_env(),
        entity="Recipient",
        entity_id=recipient.id,
        changed_fields={"email": email, "list_type": "dev_alert", "action": "seeded"},
    )
    return recipient


def ensure_settings_row(session: Session) -> Setting:
    """Ensure the settings singleton row (id=1) exists with Test Mode ON by default."""
    row = session.get(Setting, 1)
    if row is None:
        row = Setting.seed_default()
        row.test_mode = True
        row.test_mode_enabled_at = None
        row.test_mode_reason = "seeded default: test mode on"
        session.add(row)
        session.flush()
        log_config_change(
            session,
            actor="env-seed",
            entity="Setting",
            entity_id=1,
            changed_fields={"test_mode": True, "action": "created"},
        )
    return row


def seed_from_yaml(session: Session, path: str) -> dict[str, int]:
    """Load the bootstrap YAML and insert anything not already present.

    Supported top-level sections (recognising that source/provider config lives in the DB):
    ``dev_alert_email``, ``sources``, ``tender_recipients``. Secret values (``auth``) are
    encrypted with the master key at rest and never stored or logged in plaintext.
    """
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    ensure_settings_row(session)
    counts = {"dev_alert": 0, "sources": 0, "tender_recipients": 0}

    if "dev_alert_email" in data:
        dev_email = str(data["dev_alert_email"])
        seed_dev_alert_recipient(session, dev_email)
        counts["dev_alert"] = 1

    for item in data.get("sources") or []:
        name = item.get("name")
        if session.query(Source).filter(Source.name == name).first() is None:
            source = Source(
                name=name,
                source_type=item.get("type", "html"),
                base_url=item.get("base_url") or item.get("url") or "",
                listing_url=item.get("listing_url"),
                parser_config=item.get("parser_config"),
                crawl_frequency_minutes=item.get("crawl_frequency_minutes"),
                active=bool(item.get("active", True)),
                expected_languages=item.get("expected_languages"),
                recipient_scope=item.get("recipient_scope"),
            )
            if item.get("auth"):
                source.auth_encrypted = encrypt_secret(str(item["auth"]))
            session.add(source)
            session.flush()
            log_config_change(
                session,
                actor="env-seed",
                entity="Source",
                entity_id=source.id,
                changed_fields={"name": name, "action": "seeded"},
            )
            counts["sources"] += 1

    for item in data.get("tender_recipients") or []:
        email = str(item.get("email") or "")
        if not email or not is_valid_email(email):
            raise SeedError(f"invalid tender recipient email: {email!r}")
        if (
            session.query(Recipient)
            .filter(Recipient.email == email, Recipient.list_type == "tender")
            .first()
            is not None
        ):
            continue
        recipient = Recipient(
            email=email,
            name=item.get("name"),
            list_type="tender",
            delivery=str(item.get("delivery", "to")),
            receives_filter=str(item.get("receives_filter", "all")),
            active=True,
        )
        session.add(recipient)
        session.flush()
        log_config_change(
            session,
            actor="env-seed",
            entity="Recipient",
            entity_id=recipient.id,
            changed_fields={"email": email, "list_type": "tender", "action": "seeded"},
        )
        counts["tender_recipients"] += 1

    return counts

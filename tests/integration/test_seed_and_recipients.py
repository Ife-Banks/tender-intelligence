"""Integration tests for config seeding: YAML bootstrap, dev-recipient env seeding,
and the last-active-dev-recipient guard (docs/04 §4.9, docs/03 Recipient)."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from tender_intelligence.config.seed import (
    SeedError,
    ensure_settings_row,
    seed_dev_alert_recipient,
    seed_from_yaml,
)
from tender_intelligence.crypto.secrets import decrypt_secret, generate_master_key
from tender_intelligence.db.models.config import ConfigChangeLog, Setting
from tender_intelligence.db.models.recipients import Recipient
from tender_intelligence.db.models.sources import Source
from tender_intelligence.notifications.recipients import RecipientGuard


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(generate_master_key()).decode())


class TestSeed:
    def test_ensure_settings_row_creates_with_test_mode_on(self, db_session):
        row = ensure_settings_row(db_session)
        assert row.id == 1 and row.test_mode is True
        db_session.commit()
        assert db_session.get(Setting, 1).test_mode is True

    def test_dev_recipient_seeded_from_env(self, db_session):
        rec = seed_dev_alert_recipient(db_session, "dev@opex.example")
        db_session.commit()
        assert rec.list_type == "dev_alert" and rec.active
        log = db_session.scalar(
            select(ConfigChangeLog).where(ConfigChangeLog.entity == "Recipient")
        )
        assert log is not None

    def test_seed_is_noop_when_active_dev_exists(self, db_session):
        seed_dev_alert_recipient(db_session, "first@dev.example")
        db_session.commit()
        seed_dev_alert_recipient(db_session, "second@dev.example")
        db_session.commit()
        recipients = db_session.scalars(
            select(Recipient).where(Recipient.list_type == "dev_alert")
        ).all()
        assert len(recipients) == 1
        assert recipients[0].email == "first@dev.example"

    def test_invalid_dev_email_rejected(self, db_session):
        with pytest.raises(SeedError):
            seed_dev_alert_recipient(db_session, "not-an-email")

    def test_yaml_seed_sources_and_recipients(self, db_session, tmp_path):
        yaml_file = tmp_path / "seed.yml"
        yaml_file.write_text(
            """
            dev_alert_email: dev@corp.example
            sources:
              - name: WAHO
                type: html
                base_url: https://afro.who.int/programmes/health-stewardship
                auth: waho-secret-token
            tender_recipients:
              - email: owner@opex.example
                name: Owner
                delivery: to
            """,
            encoding="utf-8",
        )
        counts = seed_from_yaml(db_session, str(yaml_file))
        db_session.commit()
        assert counts == {"dev_alert": 1, "sources": 1, "tender_recipients": 1}
        source = db_session.scalar(select(Source))
        assert source.name == "WAHO"
        assert decrypt_secret(source.auth_encrypted) == "waho-secret-token"
        recipient = db_session.scalar(
            select(Recipient).where(Recipient.list_type == "tender")
        )
        assert recipient.email == "owner@opex.example"

    def test_yaml_seed_idempotent(self, db_session, tmp_path):
        yaml_file = tmp_path / "seed.yml"
        yaml_file.write_text(
            "dev_alert_email: d@x.io\nsources:\n  - name: WAHO\n    type: html\n    base_url: http://x\n",
            encoding="utf-8",
        )
        seed_from_yaml(db_session, str(yaml_file))
        db_session.commit()
        seed_from_yaml(db_session, str(yaml_file))
        db_session.commit()
        assert len(db_session.scalars(select(Source)).all()) == 1


class TestRecipientGuard:
    def test_last_dev_recipient_guard(self, db_session):
        seed_dev_alert_recipient(db_session, "last@dev.example")
        db_session.commit()
        assert RecipientGuard(db_session).refuse_last_dev_removal()

    def test_allows_removal_when_more_than_one(self, db_session):
        db_session.add_all(
            [
                Recipient(email="one@dev.example", list_type="dev_alert", active=True),
                Recipient(email="two@dev.example", list_type="dev_alert", active=True),
            ]
        )
        db_session.commit()
        big = RecipientGuard(db_session)
        assert big.active_dev_count() == 2
        # With two active, removing one is fine...
        assert not big.refuse_last_dev_removal()
        # ...but removing the remaining one leaves zero active, which is refused.
        recs = big.dev_recipients()
        for rec in recs:
            rec.active = False
        db_session.commit()
        assert RecipientGuard(db_session).refuse_last_dev_removal()

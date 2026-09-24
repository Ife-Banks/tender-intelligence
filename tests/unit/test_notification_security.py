"""Security and signed-archive tests for the Prompt 11 notification layer."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from tender_intelligence.crypto.secrets import generate_master_key
from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.config import ConfigChangeLog
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.mail.links import SecureLinkSigner
from tender_intelligence.mail.sendlib import SENDLIB_FREE_CAPABILITIES
from tender_intelligence.notifications.archive import SecureDocumentAccess, SecureLinkDenied
from tender_intelligence.notifications.providers import MailProviderAdmin
from tender_intelligence.notifications.service import _capabilities_for
from tender_intelligence.notifications.test_mode import TestModePolicy
from tender_intelligence.storage.local import LocalFileSystemStorage


def test_signed_archive_access_checks_identity_and_expiry(
    db_session, session_factory_gr, tmp_path
) -> None:
    key = b"k" * 32
    storage = LocalFileSystemStorage(tmp_path)
    storage.put("tenders/1/a.pdf", b"archived", "application/pdf")
    source = Source(name="WAHO", source_type="html", base_url="https://example.test")
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id="t-1",
        url="https://example.test/t-1",
        title="Tender",
        correlation_id="c" * 32,
    )
    db_session.add(tender)
    db_session.flush()
    document = Document(
        tender_id=tender.id,
        filename="a.pdf",
        source_url="https://example.test/a.pdf",
        storage_path="tenders/1/a.pdf",
        checksum="a" * 64,
        download_status="downloaded",
    )
    db_session.add(document)
    db_session.commit()
    signer = SecureLinkSigner(key, "https://archive.example.test")
    now = datetime(2026, 9, 24, tzinfo=UTC)
    link = signer.sign(
        tender_id=tender.id,
        document_id=document.id,
        filename=document.filename,
        now=now,
    )
    access = SecureDocumentAccess(session_factory_gr, storage, signer)
    resolved = access.open(
        tender_id=tender.id,
        document_id=document.id,
        expires=int(link.expires_at.timestamp()),
        signature=link.url.split("sig=", 1)[1],
        now=now,
    )
    assert resolved.content == b"archived"
    with pytest.raises(SecureLinkDenied):
        access.open(
            tender_id=tender.id,
            document_id=document.id + 1,
            expires=int(link.expires_at.timestamp()),
            signature=link.url.split("sig=", 1)[1],
            now=now,
        )
    with pytest.raises(SecureLinkDenied):
        access.open(
            tender_id=tender.id,
            document_id=document.id,
            expires=int(link.expires_at.timestamp()),
            signature=link.url.split("sig=", 1)[1],
            now=now + timedelta(days=15),
        )


def test_provider_credentials_are_write_only_and_audit_is_recursive(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(generate_master_key()).decode())
    admin = MailProviderAdmin(db_session)
    row = admin.add(
        name="sendlib-test",
        provider_type="sendlib",
        credentials={"api_key": "never-log-this"},
        from_address="tenders@example.test",
        capabilities=None,
    )
    db_session.commit()
    view = admin.view(row.id)
    assert view.credentials_set is True
    assert "never-log-this" not in repr(view)
    assert "credentials_encrypted" not in repr(view)

    log_config_change(
        db_session,
        actor="test",
        entity="provider",
        changed_fields={"outer": {"credentials": {"api_key": "nested-secret"}}},
    )
    db_session.commit()
    event = db_session.query(ConfigChangeLog).order_by(ConfigChangeLog.id.desc()).first()
    assert "nested-secret" not in str(event.changed_fields)


def test_sendlib_admin_does_not_persist_unbounded_values_over_adapter_defaults(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(generate_master_key()).decode())
    admin = MailProviderAdmin(db_session)
    row = admin.add(
        name="sendlib-defaults",
        provider_type="sendlib",
        credentials={"api_key": "test-only-key"},
        from_address="tenders@example.test",
        capabilities=None,
    )

    assert "max_attachments" not in (row.capabilities or {})
    assert _capabilities_for(row) == SENDLIB_FREE_CAPABILITIES


def test_test_mode_guard_is_an_allow_list() -> None:
    policy = TestModePolicy(True)
    assert policy.guard_tender_recipients(list_types=["dev_alert"])
    assert not policy.guard_tender_recipients(list_types=["dev_alert", "tender"])
    assert not policy.guard_tender_recipients(list_types=["dev_alert", "unknown"])
    assert not policy.guard_tender_recipients(list_types=[])

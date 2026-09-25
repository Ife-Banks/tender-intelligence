"""Versioned, authenticated Admin API over the shared Prompt 04–14 models/services.

Handlers serialize explicit safe DTOs and never return ORM instances. Operational writes
remain owned by the worker; this module only mutates configuration or reads persisted state.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tender_intelligence.admin.auth import AdminActor, CurrentActor
from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.core.correlation import new_correlation_id
from tender_intelligence.crypto.secrets import SecretError, encrypt_secret, get_master_key
from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models import (
    AlertEvent,
    ConfigChangeLog,
    KnowledgeBaseVersion,
    LLMCall,
    LLMProfile,
    LLMRoleAssignment,
    MailProvider,
    NotificationLog,
    Recipient,
    RunHistory,
    Setting,
    Source,
    Tender,
    Verdict,
)
from tender_intelligence.db.models.recipients import is_valid_email
from tender_intelligence.db.repositories.notifications import MailProviderRepository
from tender_intelligence.notifications.recipients import RecipientGuard
from tender_intelligence.notifications.test_mode import TestModePolicy
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.storage.local import LocalFileSystemStorage

router = APIRouter(prefix="/api/v1", tags=["admin"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceWrite(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8, max_length=1024)
    listing_url: str | None = Field(default=None, max_length=1024)
    parser_config: dict[str, Any] | None = None
    crawl_frequency_minutes: int | None = Field(default=None, gt=0)
    active: bool = True
    expected_languages: list[str] | None = None
    recipient_scope: list[int] | None = None
    auth: dict[str, Any] | None = None

    @field_validator("name", "source_type")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("base_url", "listing_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is not None and not value.lower().startswith(("https://", "http://")):
            raise ValueError("URL must use http or https")
        if value is not None:
            _reject_url_secrets(value)
        return value


class ActiveWrite(StrictModel):
    active: bool


class AlertThresholdsWrite(StrictModel):
    kb_token_warning_share: float | None = Field(default=None, ge=0, le=1)


class SettingsWrite(StrictModel):
    test_mode: bool | None = None
    test_mode_reason: str | None = Field(default=None, max_length=1024)
    urgency_window_days: int | None = Field(default=None, ge=0)
    triage_threshold: float | None = Field(default=None, ge=0, le=1)
    monthly_ai_budget: float | None = Field(default=None, ge=0)
    alert_thresholds: AlertThresholdsWrite | None = None
    retention_months: int | None = Field(default=None, ge=1)
    link_expiry_days: int | None = Field(default=None, ge=1)


class TriageWrite(StrictModel):
    include_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    sectors: list[str] | None = None
    regions: list[str] | None = None
    minimum_contract_value: float | None = Field(default=None, ge=0)
    relevance_threshold: float | None = Field(default=None, ge=0, le=1)
    urgency_window_days: int | None = Field(default=None, ge=0)

    @field_validator("include_keywords", "exclude_keywords", "sectors", "regions")
    @classmethod
    def validate_terms(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not item.strip() for item in value):
            raise ValueError("terms must not be blank")
        return [item.strip() for item in value] if value is not None else None


class LLMProfileWrite(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=8, max_length=1024)
    model: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, min_length=1)
    context_window_tokens: int = Field(default=8000, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int = Field(default=60, gt=0, le=600)
    supports_json: bool = True
    supports_vision: bool = False
    cost_per_1k_input: float | None = Field(default=None, ge=0)
    cost_per_1k_output: float | None = Field(default=None, ge=0)
    approved_for_company_docs: bool = False
    active: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.lower().startswith(("https://", "http://")):
            raise ValueError("URL must use http or https")
        _reject_url_secrets(value)
        return value


class RoleWrite(StrictModel):
    role: Literal["triage", "verdict", "embeddings", "vision_ocr"]
    profile_id: int = Field(gt=0)
    fallback_profile_id: int | None = Field(default=None, gt=0)


class RecipientWrite(StrictModel):
    email: str = Field(min_length=3, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    list_type: Literal["tender", "dev_alert"]
    delivery: Literal["to", "cc", "bcc"] = "to"
    source_scope: list[int] | None = None
    receives_filter: Literal["all", "apply", "urgent"] = "all"
    alert_types: list[str] | None = None
    min_severity: Literal["info", "warning", "critical"] = "critical"
    active: bool = True

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not is_valid_email(value):
            raise ValueError("a valid email address is required")
        return value


class MailProviderWrite(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    provider_type: str = Field(min_length=1, max_length=64)
    credentials: dict[str, Any] | None = None
    from_address: str = Field(min_length=3, max_length=255)
    from_name: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=1, ge=1)
    active: bool = True
    capabilities: dict[str, Any] | None = None

    @field_validator("from_address", "reply_to")
    @classmethod
    def validate_provider_email(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_email(value):
            raise ValueError("a valid email address is required")
        return value


class KBWrite(StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)


class TestEmailWrite(StrictModel):
    recipient_id: int = Field(gt=0)
    subject: str = Field(default="Admin API delivery test", min_length=1, max_length=255)
    text: str = Field(
        default="[TEST] Tender Intelligence mail provider verification.",
        min_length=1,
        max_length=5000,
    )


def db_session(request: Request):
    maker = getattr(request.app.state, "session_factory", None)
    if maker is None:
        raise HTTPException(503, detail={"code": "database_unavailable"})
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Depends(db_session)


def _session(request: Request, session: Session | None) -> Session:
    # FastAPI passes the dependency-bound session to each endpoint; helper keeps handlers terse.
    if session is None:  # pragma: no cover - protects direct/non-FastAPI calls
        raise HTTPException(503, detail={"code": "database_unavailable"})
    return session


def _commit_audit(
    session: Session,
    actor: CurrentActor,
    entity: str,
    entity_id: int | None,
    fields: dict[str, Any],
) -> None:
    log_config_change(
        session, actor=actor.identity, entity=entity, entity_id=entity_id, changed_fields=fields
    )


def _not_found(resource: str) -> HTTPException:
    return HTTPException(404, detail={"code": "not_found", "resource": resource})


def _source(row: Source) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "source_type": row.source_type,
        "base_url": _safe_tree(row.base_url),
        "listing_url": _safe_tree(row.listing_url),
        "parser_config": _safe_tree(row.parser_config or {}),
        "crawl_frequency_minutes": row.crawl_frequency_minutes,
        "active": row.active,
        "expected_languages": row.expected_languages or [],
        "recipient_scope": row.recipient_scope or [],
        "auth_configured": bool(row.auth_encrypted),
        "last_run_at": _iso(row.last_run_at),
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _reject_url_secrets(value: str) -> None:
    parsed = urlsplit(value)
    sensitive = ("api_key", "apikey", "token", "secret", "password", "credential", "auth")
    if (
        parsed.username
        or parsed.password
        or any(any(term in key.lower() for term in sensitive) for key, _ in parse_qsl(parsed.query))
    ):
        raise ValueError("credentials must be supplied through encrypted fields")


def _safe_error_code(value: Any, fallback: str) -> str:
    rendered = str(value or "")
    return rendered if re.fullmatch(r"[A-Za-z0-9_]{1,64}", rendered) else fallback


_REDACT_KEYS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "auth",
    "authorization",
    "cookie",
    "storage_path",
    "content_ref",
    "private_key",
)


def _safe_tree(value: Any, key: str = "") -> Any:
    """Remove secret/private storage values recursively from API read models."""
    normalized = key.lower().replace("-", "_")
    if any(term in normalized for term in _REDACT_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _safe_tree(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_tree(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_tree(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        parsed = urlsplit(value)
        query = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if not any(term in k.lower() for term in _REDACT_KEYS)
        ]
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), parsed.fragment))
    return value


def _after(value: datetime, cutoff: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value >= cutoff


def _source_types(request: Request) -> tuple[str, ...]:
    registry = getattr(request.app.state, "adapter_registry", None) or AdapterRegistry.default()
    return registry.source_types


def _require_supported_source(request: Request, source_type: str) -> None:
    if source_type.strip().lower() not in _source_types(request):
        raise HTTPException(
            422,
            detail={
                "code": "unsupported_source_type",
                "supported_types": list(_source_types(request)),
            },
        )


def _profile(row: LLMProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "base_url": _safe_tree(row.base_url),
        "model": row.model,
        "api_key_configured": bool(row.api_key_encrypted),
        "context_window_tokens": row.context_window_tokens,
        "max_output_tokens": row.max_output_tokens,
        "temperature": row.temperature,
        "timeout_seconds": row.timeout_seconds,
        "extra_headers_configured": bool(row.extra_headers),
        "extra_header_names": sorted((row.extra_headers or {}).keys()),
        "supports_json": row.supports_json,
        "supports_vision": row.supports_vision,
        "cost_per_1k_input": row.cost_per_1k_input,
        "cost_per_1k_output": row.cost_per_1k_output,
        "approved_for_company_docs": row.approved_for_company_docs,
        "active": row.active,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _recipient(row: Recipient) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "name": row.name,
        "role": row.role,
        "list_type": row.list_type,
        "delivery": row.delivery,
        "source_scope": row.source_scope,
        "receives_filter": row.receives_filter,
        "alert_types": row.alert_types,
        "min_severity": row.min_severity,
        "active": row.active,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _provider(row: MailProvider) -> dict[str, Any]:
    return MailProviderRepository.safe_view(row)


def _setting(row: Setting) -> dict[str, Any]:
    return {
        "test_mode": row.test_mode,
        "test_mode_reason": row.test_mode_reason,
        "test_mode_enabled_at": _iso(row.test_mode_enabled_at),
        "test_mode_enabled_by": row.test_mode_enabled_by,
        "urgency_window_days": row.urgency_window_days,
        "triage_threshold": row.triage_threshold,
        "triage_rules": _safe_tree(row.triage_rules),
        "monthly_ai_budget": row.monthly_ai_budget,
        "alert_thresholds": _safe_tree(row.alert_thresholds),
        "retention_months": row.retention_months,
        "link_expiry_days": row.link_expiry_days,
        "version": row.version,
        "updated_at": _iso(row.updated_at),
    }


def _secret_envelope(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return encrypt_secret(value)
    except SecretError as exc:
        raise HTTPException(503, detail={"code": "secret_encryption_unavailable"}) from exc


def _safe_profile_exists(session: Session, profile_id: int | None) -> LLMProfile | None:
    if profile_id is None:
        return None
    profile = session.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(422, detail={"code": "profile_not_found"})
    return profile


@router.get("/sources/supported-types")
def source_types(request: Request, actor: CurrentActor):
    return {"items": list(_source_types(request))}


@router.get("/sources")
def list_sources(
    request: Request,
    actor: CurrentActor,
    session: Session = SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows = session.scalars(select(Source).order_by(Source.id).offset(offset).limit(limit)).all()
    total = session.scalar(select(func.count()).select_from(Source)) or 0
    return {
        "items": [_source(row) for row in rows],
        "pagination": {"offset": offset, "limit": limit, "total": total},
    }


@router.post("/sources", status_code=201)
def create_source(
    payload: SourceWrite, request: Request, actor: AdminActor, session: Session = SessionDep
):
    _require_supported_source(request, payload.source_type)
    values = payload.model_dump(exclude={"auth"})
    values["auth_encrypted"] = _secret_envelope(
        json.dumps(payload.auth) if payload.auth is not None else None
    )
    row = Source(**values)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail={"code": "source_name_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "Source",
        row.id,
        {
            "created": True,
            "source_type": row.source_type,
            "active": row.active,
            "auth_configured": bool(payload.auth),
        },
    )
    return _source(row)


@router.get("/sources/{source_id}")
def get_source(source_id: int, actor: CurrentActor, session: Session = SessionDep):
    row = session.get(Source, source_id)
    if row is None:
        raise _not_found("source")
    return _source(row)


@router.put("/sources/{source_id}")
def update_source(
    source_id: int,
    payload: SourceWrite,
    request: Request,
    actor: AdminActor,
    session: Session = SessionDep,
):
    row = session.get(Source, source_id)
    if row is None:
        raise _not_found("source")
    _require_supported_source(request, payload.source_type)
    changes = payload.model_dump(exclude={"auth"})
    if payload.auth is not None:
        changes["auth_encrypted"] = _secret_envelope(json.dumps(payload.auth))
    for key, value in changes.items():
        setattr(row, key, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "source_name_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "Source",
        row.id,
        {
            "fields": sorted(key for key in changes if key != "auth_encrypted"),
            "auth_configured": payload.auth is not None,
        },
    )
    return _source(row)


@router.patch("/sources/{source_id}/active")
def set_source_active(
    source_id: int, payload: ActiveWrite, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(Source, source_id)
    if row is None:
        raise _not_found("source")
    row.active = payload.active
    session.flush()
    _commit_audit(session, actor, "Source", row.id, {"active": row.active})
    return _source(row)


@router.post("/sources/{source_id}/test")
def test_source(source_id: int, request: Request, actor: AdminActor, session: Session = SessionDep):
    row = session.get(Source, source_id)
    if row is None:
        raise _not_found("source")
    # Release the read transaction before the coordinator opens its own pipeline session.
    # This also keeps SQLite's shared in-memory test connection from nesting transactions.
    session.rollback()
    coordinator = getattr(request.app.state, "run_coordinator", None)
    if coordinator is None:
        raise HTTPException(503, detail={"code": "source_dry_run_unavailable"})
    try:
        report = coordinator.run_source(source_id, dry_run=True)
    except Exception as exc:  # external adapter boundary; do not disclose raw exception text
        raise HTTPException(
            502,
            detail={
                "code": _safe_error_code(getattr(exc, "error_code", None), "source_test_failed"),
                "correlation_id": getattr(exc, "correlation_id", None),
            },
        ) from exc
    if not report.dry_run or report.run_history_id is not None:
        raise HTTPException(500, detail={"code": "source_dry_run_contract_violation"})
    discovery = next(
        (stage for stage in report.stages if stage.stage.value == "04-discovery"), None
    )
    return {
        "status": str(report.status),
        "correlation_id": report.correlation_id,
        "dry_run": True,
        "persisted": False,
        "email_sent": False,
        "source_id": report.source_id,
        "source_name": report.source_name,
        "config_version": report.config_version,
        "stages": {stage.stage.value: str(stage.status) for stage in report.stages},
        "candidate_count": discovery.item_count if discovery else 0,
        "tenders_considered": report.tenders_considered,
        "planned_actions": list(report.planned_actions),
        "error_code": report.error_code,
    }


@router.get("/tenders")
def list_tenders(
    actor: CurrentActor,
    session: Session = SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    source_id: int | None = Query(None, gt=0),
    tender_status: str | None = Query(None, alias="status"),
    recommendation: str | None = None,
):
    query = select(Tender).order_by(Tender.first_seen_at.desc(), Tender.id.desc())
    if source_id is not None:
        query = query.where(Tender.source_id == source_id)
    if tender_status:
        query = query.where(Tender.status == tender_status)
    if recommendation:
        query = query.join(Verdict).where(Verdict.recommendation == recommendation)
    rows = session.scalars(query.offset(offset).limit(limit)).unique().all()
    count_query = select(func.count(func.distinct(Tender.id)))
    if source_id is not None:
        count_query = count_query.where(Tender.source_id == source_id)
    if tender_status:
        count_query = count_query.where(Tender.status == tender_status)
    if recommendation:
        count_query = count_query.join(Verdict).where(Verdict.recommendation == recommendation)
    total = session.scalar(count_query) or 0
    return {
        "items": [
            _tender_view(session, row, include_private=actor.role == "admin") for row in rows
        ],
        "pagination": {"offset": offset, "limit": limit, "total": total},
    }


def _tender_view(session: Session, row: Tender, *, include_private: bool) -> dict[str, Any]:
    deadline = row.deadline_resolution
    verdict = session.scalar(
        select(Verdict).where(Verdict.tender_id == row.id).order_by(Verdict.generated_at.desc())
    )
    return {
        "id": row.id,
        "source_id": row.source_id,
        "source_name": row.source.name if row.source else None,
        "external_id": row.external_id,
        "title": row.title,
        "url": _safe_tree(row.url),
        "status": row.status,
        "first_seen_at": _iso(row.first_seen_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "correlation_id": row.correlation_id,
        "deadline": {
            "state": deadline.deadline_source if deadline else "unresolved",
            "utc": _iso(deadline.deadline_resolved) if deadline else None,
            "timezone": deadline.deadline_timezone if deadline else None,
        },
        "verdict": (
            {
                "id": verdict.id,
                "recommendation": verdict.recommendation,
                "confidence": verdict.confidence,
                "urgency": verdict.urgency_flag,
            }
            if verdict
            else None
        ),
        "notice": _safe_tree(row.raw_metadata) if include_private else None,
    }


@router.get("/tenders/{tender_id}")
def get_tender(tender_id: int, actor: CurrentActor, session: Session = SessionDep):
    row = session.get(Tender, tender_id)
    if row is None:
        raise _not_found("tender")
    return _tender_view(session, row, include_private=actor.role == "admin")


@router.get("/tenders/{tender_id}/timeline")
def get_timeline(tender_id: int, actor: CurrentActor, session: Session = SessionDep):
    row = session.get(Tender, tender_id)
    if row is None:
        raise _not_found("tender")
    query = TimelineService(session).reconstruct_by_correlation(row.correlation_id)
    return {
        "tender_id": tender_id,
        "correlation_id": query.correlation_id,
        "run_ids": query.run_ids or [],
        "events": [
            {
                "timestamp": _iso(event.timestamp),
                "stage": event.stage,
                "status": event.status,
                "correlation_id": event.correlation_id,
                "tender_id": event.tender_id,
                "run_id": event.run_id,
                "source": event.source,
                "document_ids": event.document_ids,
                "processing_info": _safe_tree(event.processing_info),
                "incomplete_inputs": event.incomplete_inputs,
                "notification_id": event.notification_id,
                "provider_used": event.provider_used,
                "failure_code": event.failure_code,
            }
            for event in query.events
        ],
    }


@router.get("/tenders/{tender_id}/verdicts")
def list_tender_verdicts(tender_id: int, actor: AdminActor, session: Session = SessionDep):
    if session.get(Tender, tender_id) is None:
        raise _not_found("tender")
    rows = session.scalars(
        select(Verdict).where(Verdict.tender_id == tender_id).order_by(Verdict.generated_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "tender_id": row.tender_id,
                "recommendation": row.recommendation,
                "confidence": row.confidence,
                "background_summary": row.background_summary,
                "requirements_summary": row.requirements_summary,
                "gap_analysis": row.gap_analysis,
                "urgency": row.urgency_flag,
                "incomplete_inputs": row.incomplete_inputs,
                "generated_at": _iso(row.generated_at),
                "llm_profile_id": row.llm_profile_id,
                "provider": row.provider,
                "model": row.model,
                "knowledge_base_version_id": row.knowledge_base_version_id,
                "prompt_version": row.prompt_version,
                "schema_version": row.schema_version,
                "map_reduce_used": row.map_reduce_used,
                "deadline": {
                    "status": row.deadline_status,
                    "utc": row.deadline_utc,
                    "date": row.deadline_date,
                    "time": row.deadline_time,
                    "timezone": row.deadline_timezone,
                    "source_timezone": row.source_timezone,
                },
            }
            for row in rows
        ]
    }


@router.get("/settings")
def get_settings(actor: CurrentActor, session: Session = SessionDep):
    row = session.get(Setting, 1)
    if row is None:
        return {
            "test_mode": True,
            "test_mode_reason": None,
            "test_mode_enabled_at": None,
            "test_mode_enabled_by": None,
            "urgency_window_days": None,
            "triage_threshold": None,
            "triage_rules": None,
            "monthly_ai_budget": None,
            "alert_thresholds": None,
            "retention_months": None,
            "link_expiry_days": None,
            "version": 0,
            "updated_at": None,
        }
    data = _setting(row)
    if actor.role != "admin":
        data["triage_rules"] = None
        data["monthly_ai_budget"] = None
    return data


@router.put("/settings")
def update_settings(payload: SettingsWrite, actor: AdminActor, session: Session = SessionDep):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, detail={"code": "empty_update"})
    if "test_mode_reason" in changes and "test_mode" not in changes:
        raise HTTPException(422, detail={"code": "test_mode_reason_requires_test_mode_change"})
    if changes.get("test_mode") is False and not (changes.get("test_mode_reason") or "").strip():
        raise HTTPException(422, detail={"code": "test_mode_off_requires_reason"})
    row = session.get(Setting, 1)
    if row is None:
        row = Setting.seed_default()
        session.add(row)
        session.flush()
    test_mode = changes.pop("test_mode", None)
    reason = changes.pop("test_mode_reason", None)
    changed = {}
    for name, value in changes.items():
        if name == "alert_thresholds" and value is not None:
            value = value.model_dump(exclude_none=True)
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed[name] = value
    if test_mode is not None and test_mode != row.test_mode:
        row.test_mode = test_mode
        row.test_mode_reason = reason
        row.test_mode_enabled_at = datetime.now(UTC)
        row.test_mode_enabled_by = actor.identity
        changed["test_mode"] = test_mode
        if reason:
            changed["test_mode_reason"] = True
    if changed:
        row.version = int(row.version or 0) + 1
        _commit_audit(session, actor, "Setting", 1, changed)
    session.flush()
    return _setting(row)


@router.get("/triage")
def get_triage(actor: CurrentActor, session: Session = SessionDep):
    row = session.get(Setting, 1)
    rules = (row.triage_rules or {}) if row else {}
    return {
        "include_keywords": rules.get("include_keywords"),
        "exclude_keywords": rules.get("exclude_keywords"),
        "sectors": rules.get("target_sectors"),
        "regions": rules.get("target_regions"),
        "minimum_contract_value": rules.get("minimum_contract_value"),
        "relevance_threshold": row.triage_threshold if row else None,
        "urgency_window_days": row.urgency_window_days if row else None,
    }


@router.put("/triage")
def update_triage(payload: TriageWrite, actor: AdminActor, session: Session = SessionDep):
    row = session.get(Setting, 1)
    if row is None:
        row = Setting.seed_default()
        session.add(row)
        session.flush()
    data = dict(row.triage_rules or {})
    patch = payload.model_dump(exclude_unset=True)
    urgency = patch.pop("urgency_window_days", None)
    threshold = patch.pop("relevance_threshold", None)
    for key, value in patch.items():
        data[{"sectors": "target_sectors", "regions": "target_regions"}.get(key, key)] = value
    if patch:
        row.triage_rules = data
    changed: dict[str, Any] = {"triage_rules": sorted(patch)} if patch else {}
    if threshold is not None and threshold != row.triage_threshold:
        row.triage_threshold = threshold
        changed["triage_threshold"] = threshold
    if urgency is not None and urgency != row.urgency_window_days:
        row.urgency_window_days = urgency
        changed["urgency_window_days"] = urgency
    if changed:
        row.version = int(row.version or 0) + 1
        _commit_audit(session, actor, "Setting", 1, changed)
    session.flush()
    return get_triage(actor, session)


@router.get("/llm/profiles")
def list_llm_profiles(
    actor: CurrentActor,
    session: Session = SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows = session.scalars(
        select(LLMProfile).order_by(LLMProfile.id).offset(offset).limit(limit)
    ).all()
    total = session.scalar(select(func.count()).select_from(LLMProfile)) or 0
    items = (
        [_profile(row) for row in rows]
        if actor.role == "admin"
        else [
            {
                "id": row.id,
                "name": row.name,
                "model": row.model,
                "active": row.active,
                "approved_for_company_docs": row.approved_for_company_docs,
            }
            for row in rows
        ]
    )
    return {"items": items, "pagination": {"offset": offset, "limit": limit, "total": total}}


@router.post("/llm/profiles", status_code=201)
def create_llm_profile(payload: LLMProfileWrite, actor: AdminActor, session: Session = SessionDep):
    data = payload.model_dump(exclude={"api_key"})
    data["api_key_encrypted"] = _secret_envelope(payload.api_key)
    row = LLMProfile(**data)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail={"code": "llm_profile_name_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "LLMProfile",
        row.id,
        {
            "created": True,
            "api_key_configured": bool(payload.api_key),
            "approved_for_company_docs": row.approved_for_company_docs,
            "model": row.model,
            "provider": row.base_url,
        },
    )
    return _profile(row)


@router.get("/llm/profiles/{profile_id}")
def get_llm_profile(profile_id: int, actor: CurrentActor, session: Session = SessionDep):
    row = session.get(LLMProfile, profile_id)
    if row is None:
        raise _not_found("llm_profile")
    if actor.role != "admin":
        return {
            "id": row.id,
            "name": row.name,
            "model": row.model,
            "active": row.active,
            "approved_for_company_docs": row.approved_for_company_docs,
        }
    return _profile(row)


@router.put("/llm/profiles/{profile_id}")
def update_llm_profile(
    profile_id: int, payload: LLMProfileWrite, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(LLMProfile, profile_id)
    if row is None:
        raise _not_found("llm_profile")
    values = payload.model_dump(exclude={"api_key"})
    secret_set = payload.api_key is not None
    if secret_set:
        values["api_key_encrypted"] = _secret_envelope(payload.api_key)
    for key, value in values.items():
        setattr(row, key, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "llm_profile_name_conflict"}) from exc
    changed = {
        "fields": sorted(key for key in values if key != "api_key_encrypted"),
        "api_key_configured": bool(row.api_key_encrypted),
        "api_key_updated": secret_set,
    }
    _commit_audit(session, actor, "LLMProfile", row.id, changed)
    return _profile(row)


@router.delete("/llm/profiles/{profile_id}", status_code=204)
def delete_llm_profile(profile_id: int, actor: AdminActor, session: Session = SessionDep):
    row = session.get(LLMProfile, profile_id)
    if row is None:
        raise _not_found("llm_profile")
    if session.scalar(
        select(func.count())
        .select_from(LLMRoleAssignment)
        .where(
            or_(
                LLMRoleAssignment.profile_id == profile_id,
                LLMRoleAssignment.fallback_profile_id == profile_id,
            )
        )
    ):
        raise HTTPException(409, detail={"code": "profile_in_use"})
    _commit_audit(session, actor, "LLMProfile", row.id, {"deleted": True})
    session.delete(row)
    return Response(status_code=204)


@router.get("/llm/roles")
def list_roles(actor: CurrentActor, session: Session = SessionDep):
    rows = session.scalars(select(LLMRoleAssignment).order_by(LLMRoleAssignment.role)).all()
    if actor.role != "admin":
        return {
            "items": [
                {
                    "role": row.role,
                    "profile_id": row.profile_id,
                    "fallback_profile_id": row.fallback_profile_id,
                }
                for row in rows
            ]
        }
    return {
        "items": [
            {
                "role": row.role,
                "profile": _profile(row.profile),
                "fallback_profile": _profile(row.fallback_profile)
                if row.fallback_profile
                else None,
            }
            for row in rows
        ]
    }


@router.get("/llm/usage")
def llm_usage(actor: CurrentActor, session: Session = SessionDep):
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    calls = session.scalars(select(LLMCall).where(LLMCall.created_at >= month_start)).all()
    settings = session.get(Setting, 1)
    spent = sum(float(call.est_cost) for call in calls if call.est_cost is not None)
    return {
        "period_start": month_start.isoformat(),
        "calls": len(calls),
        "known_cost": spent,
        "cost_unknown_calls": sum(call.est_cost is None for call in calls),
        "monthly_budget": settings.monthly_ai_budget if settings else None,
        "budget_open": settings is None or settings.monthly_ai_budget is None,
    }


@router.put("/llm/roles/{role}")
def set_role(role: str, payload: RoleWrite, actor: AdminActor, session: Session = SessionDep):
    if role != payload.role:
        raise HTTPException(422, detail={"code": "role_path_mismatch"})
    profile = _safe_profile_exists(session, payload.profile_id)
    fallback = _safe_profile_exists(session, payload.fallback_profile_id)
    if not profile.active or (fallback and not fallback.active):
        raise HTTPException(409, detail={"code": "inactive_profile"})
    row = session.scalar(select(LLMRoleAssignment).where(LLMRoleAssignment.role == role))
    if row is None:
        row = LLMRoleAssignment(role=role)
        session.add(row)
    row.profile_id = payload.profile_id
    row.fallback_profile_id = payload.fallback_profile_id
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "role_assignment_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "LLMRoleAssignment",
        row.id,
        {
            "role": role,
            "profile_id": profile.id,
            "fallback_profile_id": fallback.id if fallback else None,
        },
    )
    return {
        "role": role,
        "profile": _profile(profile),
        "fallback_profile": _profile(fallback) if fallback else None,
    }


@router.post("/llm/profiles/{profile_id}/test")
def test_llm_connection(
    profile_id: int, request: Request, actor: AdminActor, session: Session = SessionDep
):
    profile = session.get(LLMProfile, profile_id)
    if profile is None:
        raise _not_found("llm_profile")
    factory = getattr(request.app.state, "llm_client_factory", None)
    if factory is None:
        raise HTTPException(503, detail={"code": "llm_test_unavailable"})
    from tender_intelligence.crypto.secrets import decrypt_secret
    from tender_intelligence.interfaces.llm import LLMMessage

    correlation_id = new_correlation_id()
    started = time.monotonic()
    try:
        key = get_master_key()
        api_key = (
            decrypt_secret(profile.api_key_encrypted, key) if profile.api_key_encrypted else ""
        )
        client = factory(profile=profile, api_key=api_key)
        result = client.chat([LLMMessage("user", "Reply with the word OK.")], max_tokens=16)
        latency_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:  # safe external/provider boundary
        session.add(
            LLMCall(
                correlation_id=correlation_id,
                role="admin_test",
                profile_id=profile.id,
                provider=profile.name,
                model=profile.model,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="failed",
                error_code=_safe_error_code(
                    getattr(exc, "error_code", None), "provider_test_failed"
                ),
            )
        )
        session.flush()
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "provider_test_failed",
                    "category": _safe_error_code(
                        getattr(exc, "error_code", None), "provider_error"
                    ),
                }
            },
        )
    usage = getattr(result, "usage", None)
    session.add(
        LLMCall(
            correlation_id=correlation_id,
            role="admin_test",
            profile_id=profile.id,
            provider=result.profile_name,
            model=result.model,
            tokens_in=getattr(usage, "prompt_tokens", None),
            tokens_out=getattr(usage, "completion_tokens", None),
            est_cost=getattr(usage, "estimated_cost_usd", None),
            latency_ms=latency_ms,
            status="success",
        )
    )
    session.flush()
    return {
        "status": "success",
        "provider": profile.name,
        "model": result.model,
        "latency_ms": latency_ms,
        "supports_json": profile.supports_json,
        "supports_vision": profile.supports_vision,
    }


@router.get("/recipients")
def list_recipients(
    actor: CurrentActor,
    session: Session = SessionDep,
    list_type: Literal["tender", "dev_alert"] | None = None,
):
    query = select(Recipient).order_by(Recipient.list_type, Recipient.id)
    if list_type:
        query = query.where(Recipient.list_type == list_type)
    rows = session.scalars(query).all()
    if actor.role != "admin":
        return {
            "items": [
                {"id": row.id, "name": row.name, "list_type": row.list_type, "active": row.active}
                for row in rows
            ]
        }
    return {"items": [_recipient(row) for row in rows]}


@router.post("/recipients", status_code=201)
def create_recipient(payload: RecipientWrite, actor: AdminActor, session: Session = SessionDep):
    row = Recipient(**payload.model_dump())
    session.add(row)
    session.flush()
    _commit_audit(
        session,
        actor,
        "Recipient",
        row.id,
        {"created": True, "list_type": row.list_type, "email": row.email},
    )
    return _recipient(row)


def _protect_last_dev(session: Session, row: Recipient) -> None:
    if row.list_type != "dev_alert" or not row.active:
        return
    # Serialize last-recipient mutations around the existing singleton row on PostgreSQL.
    session.scalar(select(Setting.id).where(Setting.id == 1).with_for_update())
    if RecipientGuard(session).refuse_last_dev_removal():
        raise HTTPException(409, detail={"code": "last_dev_recipient"})


@router.put("/recipients/{recipient_id}")
def update_recipient(
    recipient_id: int, payload: RecipientWrite, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(Recipient, recipient_id)
    if row is None:
        raise _not_found("recipient")
    values = payload.model_dump()
    if (
        row.list_type == "dev_alert"
        and row.active
        and (values["list_type"] != "dev_alert" or not values["active"])
    ):
        _protect_last_dev(session, row)
    for key, value in values.items():
        setattr(row, key, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "recipient_email_conflict"}) from exc
    _commit_audit(session, actor, "Recipient", row.id, {"fields": sorted(values)})
    return _recipient(row)


@router.delete("/recipients/{recipient_id}", status_code=204)
def delete_recipient(recipient_id: int, actor: AdminActor, session: Session = SessionDep):
    row = session.get(Recipient, recipient_id)
    if row is None:
        raise _not_found("recipient")
    _protect_last_dev(session, row)
    _commit_audit(
        session, actor, "Recipient", row.id, {"deleted": True, "list_type": row.list_type}
    )
    session.delete(row)
    return Response(status_code=204)


@router.get("/mail/providers")
def list_mail_providers(actor: CurrentActor, session: Session = SessionDep):
    rows = session.scalars(
        select(MailProvider).order_by(MailProvider.priority, MailProvider.id)
    ).all()
    if actor.role != "admin":
        return {
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "active": row.active,
                    "breaker_state": row.breaker_state,
                }
                for row in rows
            ]
        }
    return {"items": [_provider(row) for row in rows]}


@router.post("/mail/providers", status_code=201)
def create_mail_provider(
    payload: MailProviderWrite, actor: AdminActor, session: Session = SessionDep
):
    row = MailProvider(
        name=payload.name,
        provider_type=payload.provider_type,
        credentials_encrypted=_secret_envelope(
            json.dumps(payload.credentials) if payload.credentials is not None else None
        ),
        from_address=payload.from_address,
        from_name=payload.from_name,
        reply_to=payload.reply_to,
        priority=payload.priority,
        active=payload.active,
        capabilities=payload.capabilities,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "mail_provider_name_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "MailProvider",
        row.id,
        {
            "created": True,
            "credentials_configured": bool(payload.credentials),
            "provider_type": row.provider_type,
            "active": row.active,
        },
    )
    return _provider(row)


@router.put("/mail/providers/{provider_id}")
def update_mail_provider(
    provider_id: int, payload: MailProviderWrite, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(MailProvider, provider_id)
    if row is None:
        raise _not_found("mail_provider")
    values = payload.model_dump(exclude={"credentials"})
    if payload.credentials is not None:
        values["credentials_encrypted"] = _secret_envelope(json.dumps(payload.credentials))
    for key, value in values.items():
        setattr(row, key, value)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "mail_provider_name_conflict"}) from exc
    _commit_audit(
        session,
        actor,
        "MailProvider",
        row.id,
        {
            "fields": sorted(key for key in values if key != "credentials_encrypted"),
            "credentials_configured": bool(row.credentials_encrypted),
            "credentials_updated": payload.credentials is not None,
        },
    )
    return _provider(row)


@router.patch("/mail/providers/{provider_id}/active")
def set_mail_provider_active(
    provider_id: int, payload: ActiveWrite, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(MailProvider, provider_id)
    if row is None:
        raise _not_found("mail_provider")
    row.active = payload.active
    session.flush()
    _commit_audit(session, actor, "MailProvider", row.id, {"active": row.active})
    return _provider(row)


@router.get("/knowledge-base/versions")
def list_kb_versions(actor: AdminActor, session: Session = SessionDep):
    rows = session.scalars(
        select(KnowledgeBaseVersion).order_by(
            KnowledgeBaseVersion.created_at.desc(), KnowledgeBaseVersion.id.desc()
        )
    ).all()
    settings = session.get(Setting, 1)
    warning_share = (
        (settings.alert_thresholds or {}).get("kb_token_warning_share") if settings else None
    )
    return {
        "current": (
            {
                "id": rows[0].id,
                "content_hash": rows[0].content_hash,
                "token_count": rows[0].token_count,
                "created_at": _iso(rows[0].created_at),
            }
            if rows
            else None
        ),
        "items": [
            {
                "id": row.id,
                "content_hash": row.content_hash,
                "token_count": row.token_count,
                "created_at": _iso(row.created_at),
                "created_by": row.created_by,
                "note": row.note,
            }
            for row in rows
        ],
        "token_budget": {
            "latest_token_count": rows[0].token_count if rows else None,
            "warning_share": warning_share,
        },
    }


@router.get("/knowledge-base/versions/{version_id}")
def get_kb_version(
    version_id: int, actor: AdminActor, request: Request, session: Session = SessionDep
):
    row = session.get(KnowledgeBaseVersion, version_id)
    if row is None:
        raise _not_found("knowledge_base_version")
    content = _storage(request).get(row.content_ref).decode("utf-8")
    return {
        "id": row.id,
        "content": content,
        "content_hash": row.content_hash,
        "token_count": row.token_count,
        "created_at": _iso(row.created_at),
        "created_by": row.created_by,
        "note": row.note,
    }


def _storage(request: Request):
    storage = getattr(request.app.state, "object_storage", None)
    return storage or LocalFileSystemStorage(get_env_settings().storage_dir)


def _validate_kb_file(filename: str, content: bytes) -> str:
    safe = PurePosixPath(filename.replace("\\", "/")).name
    if safe != filename or safe in {"", ".", ".."}:
        raise HTTPException(422, detail={"code": "invalid_filename"})
    ext = PurePosixPath(safe).suffix.lower()
    if ext not in {".docx", ".pdf", ".md", ".txt"}:
        raise HTTPException(415, detail={"code": "unsupported_file_type"})
    if len(content) > get_env_settings().admin_max_kb_upload_bytes:
        raise HTTPException(413, detail={"code": "file_too_large"})
    if ext == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(422, detail={"code": "malformed_file"})
    if ext == ".docx" and not content.startswith(b"PK"):
        raise HTTPException(422, detail={"code": "malformed_file"})
    if ext in {".md", ".txt"}:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, detail={"code": "malformed_file"}) from exc
    return safe


def _extract_kb_text(content: bytes, filename: str) -> bytes:
    ext = PurePosixPath(filename).suffix.lower()
    if ext in {".md", ".txt"}:
        return content
    try:
        if ext == ".docx":
            from tender_intelligence.processing.docx import extract_docx

            text = extract_docx(content).text
        else:
            from tender_intelligence.processing.pdf import extract_pdf

            text = extract_pdf(content).text
    except Exception as exc:
        raise HTTPException(422, detail={"code": "knowledge_base_extraction_failed"}) from exc
    if not text.strip():
        raise HTTPException(422, detail={"code": "knowledge_base_extraction_empty"})
    return text.encode("utf-8")


@router.post("/knowledge-base/versions", status_code=201)
def upload_kb(payload: KBWrite, request: Request, actor: AdminActor, session: Session = SessionDep):
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(422, detail={"code": "invalid_base64"}) from exc
    filename = _validate_kb_file(payload.filename, content)
    content = _extract_kb_text(content, filename)
    digest = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(KnowledgeBaseVersion).where(KnowledgeBaseVersion.content_hash == digest)
    )
    if existing:
        raise HTTPException(
            409, detail={"code": "knowledge_base_version_exists", "version_id": existing.id}
        )
    key = f"knowledge-base/{digest}/{filename}"
    try:
        _storage(request).put(key, content, _content_type(filename))
    except Exception as exc:
        raise HTTPException(503, detail={"code": "knowledge_base_storage_failed"}) from exc
    token_count = _token_count(content, "kb.txt")
    row = KnowledgeBaseVersion(
        content_ref=key,
        content_hash=digest,
        token_count=token_count,
        created_by=actor.identity,
        note=payload.note,
    )
    session.add(row)
    session.flush()
    _commit_audit(
        session,
        actor,
        "KnowledgeBaseVersion",
        row.id,
        {"created": True, "content_hash": digest, "token_count": token_count},
    )
    return {
        "id": row.id,
        "content_hash": digest,
        "token_count": token_count,
        "token_count_method": "whitespace_word_estimate",
        "created_at": _iso(row.created_at),
        "note": row.note,
    }


def _content_type(filename: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(PurePosixPath(filename).suffix.lower(), "application/octet-stream")


def _token_count(content: bytes, filename: str) -> int:
    ext = PurePosixPath(filename).suffix.lower()
    if ext in {".txt", ".md"}:
        return max(1, len(content.decode("utf-8").split()))
    # Binary formats lack a configured tokenizer/extractor at the KB boundary.
    return 0


@router.get("/knowledge-base/diff")
def diff_kb(
    actor: AdminActor,
    request: Request,
    session: Session = SessionDep,
    from_id: int = Query(gt=0),
    to_id: int = Query(gt=0),
):
    first, second = (
        session.get(KnowledgeBaseVersion, from_id),
        session.get(KnowledgeBaseVersion, to_id),
    )
    if first is None or second is None:
        raise _not_found("knowledge_base_version")
    left = _storage(request).get(first.content_ref).decode("utf-8", errors="replace").splitlines()
    right = _storage(request).get(second.content_ref).decode("utf-8", errors="replace").splitlines()
    return {
        "from_id": from_id,
        "to_id": to_id,
        "diff": list(
            difflib.unified_diff(left, right, fromfile=str(from_id), tofile=str(to_id), lineterm="")
        ),
    }


@router.get("/audit")
def list_audit(
    actor: AdminActor,
    session: Session = SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows = session.scalars(
        select(ConfigChangeLog)
        .order_by(ConfigChangeLog.created_at.desc(), ConfigChangeLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    total = session.scalar(select(func.count()).select_from(ConfigChangeLog)) or 0
    return {
        "items": [
            {
                "id": row.id,
                "actor": row.actor,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "changed_fields": _safe_tree(row.changed_fields or {}),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "pagination": {"offset": offset, "limit": limit, "total": total},
    }


@router.post("/mail/test")
def send_test_email(
    payload: TestEmailWrite, request: Request, actor: AdminActor, session: Session = SessionDep
):
    settings = session.get(Setting, 1)
    if settings is None or not settings.test_mode:
        raise HTTPException(409, detail={"code": "test_mode_required"})
    recipient = session.get(Recipient, payload.recipient_id)
    if recipient is None or recipient.list_type != "dev_alert" or not recipient.active:
        raise HTTPException(422, detail={"code": "active_dev_recipient_required"})
    service = getattr(request.app.state, "notification_service", None)
    if service is None or not hasattr(service, "send_test_email"):
        raise HTTPException(503, detail={"code": "test_email_service_unavailable"})
    correlation_id = new_correlation_id()
    try:
        subject = TestModePolicy(True).apply_subject_prefix(payload.subject)
        body = payload.text if payload.text.startswith("[TEST]") else f"[TEST] {payload.text}"
        result = service.send_test_email(
            recipient.email, subject, body, correlation_id=correlation_id
        )
    except Exception as exc:
        raise HTTPException(502, detail={"code": "test_email_failed"}) from exc
    _commit_audit(
        session,
        actor,
        "TestEmail",
        getattr(result, "notification_log_id", None),
        {
            "recipient_id": recipient.id,
            "status": getattr(result, "status", "failed"),
            "provider": getattr(result, "provider_used", None),
        },
    )
    if getattr(result, "status", None) != "sent":
        session.commit()
        raise HTTPException(
            502, detail={"code": "test_email_failed", "correlation_id": correlation_id}
        )
    return {
        "status": result.status,
        "provider": result.provider_used,
        "correlation_id": correlation_id,
        "notification_log_id": result.notification_log_id,
    }


@router.post("/mail/providers/{provider_id}/test")
def test_mail_provider(
    provider_id: int, request: Request, actor: AdminActor, session: Session = SessionDep
):
    row = session.get(MailProvider, provider_id)
    if row is None:
        raise _not_found("mail_provider")
    settings = session.get(Setting, 1)
    if settings is None or not settings.test_mode:
        raise HTTPException(409, detail={"code": "test_mode_required"})
    recipient = session.scalar(
        select(Recipient)
        .where(Recipient.list_type == "dev_alert", Recipient.active.is_(True))
        .order_by(Recipient.id)
    )
    if recipient is None:
        raise HTTPException(409, detail={"code": "active_dev_recipient_required"})
    service = getattr(request.app.state, "notification_service", None)
    if service is None or not hasattr(service, "send_test_email"):
        raise HTTPException(503, detail={"code": "mail_provider_test_unavailable"})
    correlation_id = new_correlation_id()
    try:
        result = service.send_test_email(
            recipient.email,
            "[TEST] Provider connection test",
            "[TEST] Tender Intelligence mail provider verification.",
            correlation_id=correlation_id,
            preferred_provider_id=provider_id,
        )
    except Exception as exc:
        raise HTTPException(502, detail={"code": "mail_provider_test_failed"}) from exc
    _commit_audit(
        session,
        actor,
        "MailProviderTest",
        provider_id,
        {"status": result.status, "provider": result.provider_used, "recipient_id": recipient.id},
    )
    if result.status != "sent":
        session.commit()
        raise HTTPException(
            502, detail={"code": "mail_provider_test_failed", "correlation_id": correlation_id}
        )
    return {
        "status": result.status,
        "provider": result.provider_used,
        "correlation_id": correlation_id,
        "notification_log_id": result.notification_log_id,
    }


@router.get("/health/dashboard")
def admin_health(actor: CurrentActor, session: Session = SessionDep):
    now = datetime.now(UTC)
    since7, since30 = now - timedelta(days=7), now - timedelta(days=30)
    sources = session.scalars(select(Source).order_by(Source.id)).all()
    items = []
    for source in sources:
        runs = session.scalars(
            select(RunHistory)
            .where(RunHistory.source_id == source.id)
            .order_by(RunHistory.started_at.desc())
        ).all()
        success = next(
            (r for r in runs if r.ended_at and r.error_count == 0 and not r.failed_stage), None
        )
        failure = next((r for r in runs if r.ended_at and (r.error_count or r.failed_stage)), None)
        items.append(
            {
                "source_id": source.id,
                "name": source.name,
                "active": source.active,
                "last_successful_run": _iso(success.ended_at) if success else None,
                "last_failed_run": _iso(failure.ended_at) if failure else None,
                "failure_category": failure.error_code if failure else None,
                "new_tenders_7d": sum(r.new_count for r in runs if _after(r.started_at, since7)),
                "new_tenders_30d": sum(r.new_count for r in runs if _after(r.started_at, since30)),
                "run_count_7d": sum(1 for r in runs if _after(r.started_at, since7)),
                "run_count_30d": sum(1 for r in runs if _after(r.started_at, since30)),
                "verdicts_7d": session.scalar(
                    select(func.count())
                    .select_from(Verdict)
                    .join(Tender, Tender.id == Verdict.tender_id)
                    .where(Tender.source_id == source.id, Verdict.generated_at >= since7)
                )
                or 0,
                "verdicts_30d": session.scalar(
                    select(func.count())
                    .select_from(Verdict)
                    .join(Tender, Tender.id == Verdict.tender_id)
                    .where(Tender.source_id == source.id, Verdict.generated_at >= since30)
                )
                or 0,
                "notification_failures_7d": session.scalar(
                    select(func.count())
                    .select_from(NotificationLog)
                    .join(Tender, Tender.id == NotificationLog.tender_id)
                    .where(
                        Tender.source_id == source.id,
                        NotificationLog.created_at >= since7,
                        NotificationLog.status.in_(["failed", "pending_retry"]),
                    )
                )
                or 0,
                "notification_failures_30d": session.scalar(
                    select(func.count())
                    .select_from(NotificationLog)
                    .join(Tender, Tender.id == NotificationLog.tender_id)
                    .where(
                        Tender.source_id == source.id,
                        NotificationLog.created_at >= since30,
                        NotificationLog.status.in_(["failed", "pending_retry"]),
                    )
                )
                or 0,
            }
        )
    verdicts7 = (
        session.scalar(
            select(func.count()).select_from(Verdict).where(Verdict.generated_at >= since7)
        )
        or 0
    )
    verdicts30 = (
        session.scalar(
            select(func.count()).select_from(Verdict).where(Verdict.generated_at >= since30)
        )
        or 0
    )
    notif_fail7 = (
        session.scalar(
            select(func.count())
            .select_from(NotificationLog)
            .where(
                NotificationLog.created_at >= since7,
                NotificationLog.status.in_(["failed", "pending_retry"]),
                NotificationLog.notification_kind != "test",
            )
        )
        or 0
    )
    notif_fail30 = (
        session.scalar(
            select(func.count())
            .select_from(NotificationLog)
            .where(
                NotificationLog.created_at >= since30,
                NotificationLog.status.in_(["failed", "pending_retry"]),
                NotificationLog.notification_kind != "test",
            )
        )
        or 0
    )
    stuck = (
        session.scalar(
            select(func.count())
            .select_from(NotificationLog)
            .where(
                NotificationLog.status == "sending",
                NotificationLog.notification_kind != "test",
                or_(NotificationLog.claim_until.is_(None), NotificationLog.claim_until < now),
            )
        )
        or 0
    )
    providers = session.scalars(select(MailProvider).order_by(MailProvider.priority)).all()
    open_alerts = (
        session.scalar(
            select(func.count()).select_from(AlertEvent).where(AlertEvent.state == "open")
        )
        or 0
    )
    return {
        "as_of": now.isoformat(),
        "sources": items,
        "verdict_counts": {"7d": verdicts7, "30d": verdicts30},
        "notification_failures": {"7d": notif_fail7, "30d": notif_fail30},
        "provider_chain": [
            {
                "provider_id": p.id,
                "name": p.name,
                "active": p.active,
                "breaker_state": p.breaker_state,
            }
            for p in providers
        ],
        "stuck_notification_count": stuck,
        "open_alert_count": open_alerts,
        "health_status": "degraded" if stuck or open_alerts else "ok",
    }

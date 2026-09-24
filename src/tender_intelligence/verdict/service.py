"""Evidence-grounded Stage B assessment (docs/07; Prompt 14)."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from tender_intelligence.audit.alert_manager import AlertManager, AlertTrigger
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.deadline import TenderDeadlineResolution
from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.interfaces.llm import LLMClient, LLMError, LLMMessage
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.storage.interface import ObjectStorage

log = logging.getLogger("tender_intelligence.verdict")
SCHEMA_VERSION = "verdict.v1"
PROMPT_VERSION = "stage-b.v1"
ROLE = "verdict"
TRANSIENT_ERRORS = {"ai_call_timeout", "timeout", "rate_limit", "http_429", "http_5xx"}


class OutcomeStatus(StrEnum):
    AVAILABLE = "VERDICT_AVAILABLE"
    FAILED = "VERDICT_FAILED"
    BUDGET = "AWAITING_BUDGET"
    APPROVAL = "AWAITING_APPROVED_PROVIDER"


class VerdictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: int
    location: StrictStr
    quote: StrictStr


class KBCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: StrictStr
    quote: StrictStr


class RequirementAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirement: StrictStr
    tender_evidence: list[VerdictDocument]
    company_evidence: list[KBCitation]
    status: StrictStr
    assessment: StrictStr
    gap: StrictStr | None


class VerdictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictStr
    background: StrictStr
    requirements: list[StrictStr]
    deadline_status: StrictStr
    deadline_utc: StrictStr | None
    deadline_date: StrictStr | None
    deadline_time: StrictStr | None
    deadline_timezone: StrictStr | None
    source_timezone: StrictStr | None
    assessments: list[RequirementAssessment]
    gaps: list[StrictStr]
    verdict: StrictStr
    confidence: float = Field(ge=0.0, le=1.0)
    urgency: StrictBool
    incomplete_inputs: StrictBool
    limitations: list[StrictStr]


@dataclass(frozen=True)
class VerdictOutcome:
    tender_id: int
    status: OutcomeStatus
    verdict_id: int | None = None
    error_code: str | None = None
    automatic_assessment_unavailable: bool = False
    incomplete_inputs: bool = False
    correlation_id: str | None = None
    run_id: int | None = None


@dataclass(frozen=True)
class VerdictContent:
    """Notification-ready content only; contains no recipient/provider decision."""

    status: OutcomeStatus
    background: str | None
    requirements: list[dict[str, Any]]
    gaps: list[str]
    deadline: dict[str, Any]
    applicability: str | None
    verdict: str | None
    confidence: float | None
    urgency: bool
    incomplete_warning: str | None
    incomplete_inputs: bool
    assessment_unavailable: bool


ClientFactory = Callable[[LLMProfile], LLMClient]


class VerdictEngine:
    def __init__(
        self,
        session: Session,
        storage: ObjectStorage,
        client_factory: ClientFactory,
        *,
        run_id: int | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session, self.storage, self.client_factory = session, storage, client_factory
        self.run_id = run_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleeper = sleeper

    def generate(self, tender_id: int) -> VerdictOutcome:
        self.session.flush()
        tender = self.session.get(Tender, tender_id)
        if tender is None:
            return VerdictOutcome(
                tender_id,
                OutcomeStatus.FAILED,
                error_code="tender_not_found",
                automatic_assessment_unavailable=True,
                run_id=self.run_id,
            )
        triage = self.session.scalars(
            select(TriageResult)
            .where(TriageResult.tender_id == tender_id)
            .order_by(desc(TriageResult.created_at), desc(TriageResult.id))
        ).first()
        if triage is None or triage.status != "passed":
            return self._failure(tender, "triage_not_passed", persist=False)
        assignment = self.session.scalars(
            select(LLMRoleAssignment).where(LLMRoleAssignment.role == ROLE)
        ).one_or_none()
        if assignment is None:
            return self._failure(
                tender, "verdict_profile_unassigned", status=OutcomeStatus.APPROVAL
            )
        primary = self.session.get(LLMProfile, assignment.profile_id)
        fallback = (
            self.session.get(LLMProfile, assignment.fallback_profile_id)
            if assignment.fallback_profile_id
            else None
        )
        profiles = [p for p in (primary, fallback) if p is not None and p.active]
        if not profiles:
            return self._failure(
                tender, "verdict_profile_unavailable", status=OutcomeStatus.APPROVAL
            )
        selected = next((p for p in profiles if p.approved_for_company_docs), None)
        if selected is None:
            return self._failure(
                tender, "provider_not_approved_for_company_docs", status=OutcomeStatus.APPROVAL
            )
        settings = self.session.get(Setting, 1)
        if settings is not None and settings.monthly_ai_budget is not None:
            warning_share = (settings.alert_thresholds or {}).get(
                "monthly_budget_warning_share", 0.8
            )
            if isinstance(warning_share, (int, float)) and not isinstance(warning_share, bool):
                if self._budget_spent(settings) >= float(settings.monthly_ai_budget) * float(
                    warning_share
                ):
                    self._alert(
                        tender,
                        "budget_80_percent",
                        "warning",
                        "Monthly AI budget warning threshold reached",
                    )
        if not self._within_budget(settings):
            tender.status = "awaiting_budget"
            self.session.flush()
            self._alert(
                tender, "budget_100_percent", "warning", "AI budget reached; verdict deferred"
            )
            return VerdictOutcome(
                tender_id,
                OutcomeStatus.BUDGET,
                error_code="budget_exhausted",
                incomplete_inputs=False,
                correlation_id=tender.correlation_id,
                run_id=self.run_id,
            )
        bundle = ExtractionStore(self.storage).read_bundle(tender_id)
        if bundle is None:
            return self._failure(tender, "document_bundle_unavailable")
        incomplete = bool(bundle.incomplete_inputs)
        try:
            kb, kb_version = self._load_kb()
        except (KeyError, ValueError, UnicodeDecodeError):
            return self._failure(
                tender, "knowledge_base_unavailable", incomplete_inputs=incomplete
            )
        deadline_row = (
            tender.deadline_resolution
            or self.session.scalars(
                select(TenderDeadlineResolution).where(
                    TenderDeadlineResolution.tender_id == tender_id
                )
            ).one_or_none()
        )
        deadline = _deadline_context(deadline_row)
        docs = _bundle_documents(bundle)
        source_docs = docs
        # The entire KB is retained in the reduce request. If it alone cannot fit, fail safely.
        kb_tokens = max(kb_version.token_count, _estimate_tokens(kb))
        warning_share = (
            (settings.alert_thresholds or {}).get("kb_token_warning_share") if settings else None
        )
        if isinstance(warning_share, (int, float)) and not isinstance(warning_share, bool):
            if kb_tokens >= selected.context_window_tokens * float(warning_share):
                log.warning(
                    "KB token warning threshold reached",
                    extra={
                        "stage": "verdict",
                        "status": "kb_token_warning",
                        "tender_id": tender.id,
                        "correlation_id": tender.correlation_id,
                        "kb_version_id": kb_version.id,
                        "kb_tokens": kb_tokens,
                        "context_window_tokens": selected.context_window_tokens,
                    },
                )
        overhead = 1400
        output_reserve = selected.max_output_tokens or 1024
        map_reduce = False
        if (
            kb_tokens + _estimate_tokens(json.dumps(docs)) + overhead + output_reserve
            > selected.context_window_tokens
        ):
            if kb_tokens + overhead + output_reserve >= selected.context_window_tokens:
                return self._failure(tender, "kb_exceeds_context_window", incomplete_inputs=incomplete)
            map_reduce = True
            docs, map_error = self._map_documents(
                tender, selected, docs, kb, kb_version, deadline, incomplete
            )
            if map_error:
                return self._failure(tender, map_error, incomplete_inputs=incomplete)
            if (
                kb_tokens + _estimate_tokens(json.dumps(docs)) + overhead + output_reserve
                > selected.context_window_tokens
            ):
                return self._failure(
                    tender, "reduced_context_exceeds_window", incomplete_inputs=incomplete
                )
        messages = _verdict_messages(tender, docs, kb, kb_version.id, deadline, incomplete)
        result, profile, error = self._invoke_with_policy(tender, selected, fallback, messages, None)
        if error:
            return self._failure(tender, error, incomplete_inputs=incomplete)
        try:
            payload = VerdictPayload.model_validate_json(result.content)
            _validate_semantics(
                payload, source_docs, kb, deadline, incomplete, settings, self.clock()
            )
        except (ValidationError, ValueError, TypeError):
            # Exactly one validation retry. The response itself is deliberately not logged.
            retry_messages = messages + [
                LLMMessage(role="assistant", content=result.content),
                LLMMessage(
                    role="user",
                    content="Correct the output to satisfy the supplied JSON schema and evidence rules. Return JSON only.",
                ),
            ]
            retry_response, retry_profile, retry_error = self._invoke_with_policy(
                tender, profile, fallback, retry_messages, selected.max_output_tokens
            )
            if retry_error:
                return self._failure(tender, retry_error, incomplete_inputs=incomplete)
            profile, result = retry_profile, retry_response
            try:
                payload = VerdictPayload.model_validate_json(result.content)
                _validate_semantics(
                    payload, source_docs, kb, deadline, incomplete, settings, self.clock()
                )
            except (ValidationError, ValueError, TypeError):
                self._alert(
                    tender,
                    "invalid_ai_output",
                    "critical",
                    "Verdict validation failed after one retry",
                )
                return self._failure(
                    tender, "verdict_invalid_output", incomplete_inputs=incomplete
                )
        recommendation = payload.verdict
        record = Verdict(
            tender_id=tender_id,
            recommendation=recommendation,
            confidence=payload.confidence,
            background_summary=payload.background,
            requirements_summary=[a.model_dump() for a in payload.assessments],
            gap_analysis=[{"gap": g} for g in payload.gaps],
            urgency_flag=payload.urgency,
            llm_profile_id=profile.id,
            model=result.model,
            knowledge_base_version_id=kb_version.id,
            prompt_version=PROMPT_VERSION,
            incomplete_inputs=incomplete,
            schema_version=SCHEMA_VERSION,
            provider=_provider(profile),
            map_reduce_used=map_reduce,
            run_id=self.run_id,
            deadline_status=deadline["status"],
            deadline_utc=deadline["deadline_utc"],
            deadline_date=deadline["date"],
            deadline_time=deadline["time"],
            deadline_timezone=deadline["timezone"],
            source_timezone=deadline["source_timezone"],
        )
        self.session.add(record)
        tender.status = "processed"
        self.session.flush()
        return VerdictOutcome(
            tender_id,
            OutcomeStatus.AVAILABLE,
            record.id,
            incomplete_inputs=incomplete,
            correlation_id=tender.correlation_id,
            run_id=self.run_id,
        )

    def _load_kb(self) -> tuple[str, KnowledgeBaseVersion]:
        version = self.session.scalars(
            select(KnowledgeBaseVersion).order_by(
                desc(KnowledgeBaseVersion.created_at), desc(KnowledgeBaseVersion.id)
            )
        ).first()
        if version is None:
            raise ValueError("knowledge_base_unavailable")
        raw = self.storage.get(version.content_ref)
        if hashlib.sha256(raw).hexdigest() != version.content_hash:
            raise ValueError("knowledge_base_integrity_error")
        return raw.decode("utf-8"), version

    def _invoke_with_policy(
        self,
        tender: Tender,
        primary: LLMProfile,
        fallback: LLMProfile | None,
        messages: list[LLMMessage],
        max_tokens: int | None,
    ):
        candidates = []
        seen_profile_ids: set[int] = set()
        for candidate in (primary, fallback):
            if (
                candidate is not None
                and candidate.active
                and candidate.approved_for_company_docs
                and candidate.id not in seen_profile_ids
            ):
                candidates.append(candidate)
                seen_profile_ids.add(candidate.id)
        last_error = "provider_failure"
        for profile in candidates:
            attempts = 0
            while True:
                input_tokens = _estimate_tokens(
                    "\n".join(message.content for message in messages)
                ) + 1400
                available_output = profile.context_window_tokens - input_tokens
                if available_output <= 0:
                    last_error = "provider_context_window_exceeded"
                    break
                call_max_tokens = min(
                    available_output,
                    profile.max_output_tokens or available_output,
                    max_tokens or available_output,
                )
                started = time.perf_counter()
                try:
                    response = self.client_factory(profile).chat(
                        messages, max_tokens=call_max_tokens
                    )
                    self._record_call(tender, profile, "success", None, started, response)
                    return response, profile, None
                except LLMError as exc:
                    last_error = exc.error_code
                    self._record_call(tender, profile, "failed", last_error, started)
                    if _transport_error_class(last_error) in TRANSIENT_ERRORS and attempts < 2:
                        attempts += 1
                        self.sleeper(0.25 * (2 ** (attempts - 1)))
                        continue
                    if _transport_error_class(last_error) in TRANSIENT_ERRORS:
                        break
                    # Permanent rejection is not transport-retried, but the assigned
                    # approved fallback may still serve the role.
                    break
                except Exception:  # safe provider boundary
                    last_error = "provider_failure"
                    self._record_call(tender, profile, "failed", last_error, started)
                    break
        return None, primary, last_error

    def _map_documents(self, tender, profile, docs, kb, kb_version, deadline, incomplete):
        summaries = []
        # Every document is mapped; no document is silently dropped.
        for doc in docs:
            map_input = _estimate_tokens(json.dumps(doc, ensure_ascii=False)) + 1200
            map_output = profile.max_output_tokens or 1024
            if (
                map_input + map_output > profile.context_window_tokens
            ):
                return docs, "document_exceeds_context_window"
            map_messages = [
                LLMMessage(
                    role="system",
                    content='Summarize only the tender requirements and cite exact source locations. JSON: {"summary": string, "evidence": [{"document_id": integer, "location": string, "quote": string}]}',
                ),
                LLMMessage(role="user", content=json.dumps(doc, ensure_ascii=False)),
            ]
            response, _, error = self._invoke_with_policy(
                tender, profile, None, map_messages, profile.max_output_tokens
            )
            if error:
                return docs, error
            try:
                data = json.loads(response.content)
                if not isinstance(data.get("summary"), str) or not data.get("summary"):
                    raise ValueError
                evidence = data.get("evidence", [])
                if not isinstance(evidence, list):
                    raise ValueError
                for ref in evidence:
                    if (
                        not isinstance(ref, dict)
                        or ref.get("document_id") != doc["document_id"]
                        or not any(
                            ref.get("location") == chunk["location"]
                            and isinstance(ref.get("quote"), str)
                            and ref["quote"] in chunk["text"]
                            for chunk in doc["content"]
                        )
                    ):
                        raise ValueError
                summaries.append(
                    {
                        "document_id": doc["document_id"],
                        "filename": doc["filename"],
                        "summary": data["summary"],
                        "content": [
                            {"location": item["location"], "text": item["quote"]}
                            for item in evidence
                        ],
                    }
                )
            except (ValueError, TypeError, AttributeError):
                return docs, "document_map_invalid_output"
        return summaries, None

    def _within_budget(self, setting: Setting | None) -> bool:
        if setting is None or setting.monthly_ai_budget is None:
            return True  # amount remains unconfigured; no invented budget
        return self._budget_spent(setting) < float(setting.monthly_ai_budget)

    def _budget_spent(self, setting: Setting) -> float:
        month_start = self.clock().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = sum(
            self.session.scalars(
                select(LLMCall.est_cost).where(
                    LLMCall.created_at >= month_start, LLMCall.est_cost.is_not(None)
                )
            ).all()
        )
        return float(spent or 0)

    def _record_call(self, tender, profile, status, error, started, response=None):
        usage = getattr(response, "usage", None) if response else None
        self.session.add(
            LLMCall(
                correlation_id=tender.correlation_id,
                role=ROLE,
                profile_id=profile.id,
                tender_id=tender.id,
                run_id=self.run_id,
                provider=_provider(profile),
                model=getattr(response, "model", profile.model) if response else profile.model,
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                est_cost=getattr(usage, "estimated_cost_usd", None),
                latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
                status=status,
                error_code=error,
            )
        )
        self.session.flush()

    def _failure(
        self, tender, code, *, status=OutcomeStatus.FAILED, persist=True, incomplete_inputs=False
    ):
        if persist and tender is not None:
            tender.status = (
                "awaiting_approved_provider"
                if status is OutcomeStatus.APPROVAL
                else "verdict_failed"
            )
            if status is OutcomeStatus.FAILED:
                self._alert(tender, "ai_failure", "critical", "Verdict generation failed")
            self.session.flush()
        return VerdictOutcome(
            tender.id if tender else 0,
            status,
            error_code=code,
            automatic_assessment_unavailable=True,
            incomplete_inputs=incomplete_inputs,
            correlation_id=tender.correlation_id if tender else None,
            run_id=self.run_id,
        )

    def _alert(self, tender, alert_type, severity, message):
        try:
            AlertManager(self.session).trigger(
                AlertTrigger(
                    alert_type=alert_type,
                    severity=severity,
                    correlation_id=tender.correlation_id,
                    run_id=self.run_id,
                    tender_id=tender.id,
                    message=message,
                )
            )
        except Exception:  # alert delivery/persistence must not leak assessment data
            log.warning(
                "verdict alert hook failed",
                extra={
                    "tender_id": tender.id,
                    "correlation_id": tender.correlation_id,
                    "alert_type": alert_type,
                },
            )


def _bundle_documents(bundle):
    docs = []
    for extraction in bundle.documents:
        chunks = []
        for page in extraction.pages:
            chunks.append({"location": page.location, "text": page.text})
            chunks.extend(
                {"location": table.location, "text": table.to_text()} for table in page.tables
            )
        for section in extraction.sections:
            chunks.append({"location": section.location, "text": section.text})
            chunks.extend(
                {"location": table.location, "text": table.to_text()} for table in section.tables
            )
        if not chunks and extraction.text:
            chunks.append({"location": "document", "text": extraction.text})
        docs.append(
            {
                "document_id": extraction.document_id,
                "filename": extraction.filename,
                "extraction_status": extraction.extraction_status,
                "content": chunks,
            }
        )
    return docs


def _deadline_context(row):
    if row is None:
        return {
            "status": "UNRESOLVED",
            "deadline_utc": None,
            "source_timezone": None,
            "date": None,
            "time": None,
            "timezone": None,
        }
    source = row.deadline_source or "unresolved"
    status = (
        "CONFLICTING"
        if source == "conflicting"
        else "UNRESOLVED"
        if source == "unresolved" or row.deadline_resolved is None
        else "RESOLVED"
    )
    value = row.deadline_resolved
    if value and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return {
        "status": status,
        "deadline_utc": value.astimezone(UTC).isoformat() if value else None,
        "source_timezone": row.deadline_timezone,
        "date": value.astimezone(UTC).date().isoformat() if value else None,
        "time": value.astimezone(UTC).strftime("%H:%M:%S") if value else None,
        "timezone": "UTC" if value else None,
    }


def _verdict_messages(tender, docs, kb, kb_id, deadline, incomplete):
    schema = VerdictPayload.model_json_schema()
    system = (
        "Assess the public tender against the complete company KB. Output only JSON matching this schema: "
        + json.dumps(schema, separators=(",", ":"))
        + " Use only supplied content. Cite tender facts with document_id/location/exact quote. "
        "Company capability claims require exact KB heading citations and quotes; otherwise mark status unverified and say 'No evidence on file for X'. "
        "Do not turn missing attachments into failed requirements. Verdict must follow requirement assessments. "
        "Echo deadline exactly from authoritative context; never derive it from source documents."
    )
    public_tender = {
        "tender_id": tender.id,
        "title": tender.title,
        "notice": (tender.raw_metadata or {}).get("notice_text", ""),
    }
    content = {
        "tender": public_tender,
        "documents": docs,
        "knowledge_base_version_id": kb_id,
        "knowledge_base": kb,
        "deadline": deadline,
        "incomplete_inputs": incomplete,
    }
    return [
        LLMMessage("system", system),
        LLMMessage("user", json.dumps(content, ensure_ascii=False)),
    ]


def _validate_semantics(payload, docs, kb, deadline, incomplete, settings, now):
    if payload.schema_version != SCHEMA_VERSION:
        raise ValueError("schema version mismatch")
    if payload.verdict not in {"APPLY", "DO NOT APPLY", "APPLY WITH CONDITIONS"}:
        raise ValueError("invalid verdict")
    if payload.incomplete_inputs is not incomplete:
        raise ValueError("incomplete input state mismatch")
    if (
        payload.deadline_status != deadline["status"]
        or payload.deadline_utc != deadline["deadline_utc"]
    ):
        raise ValueError("authoritative deadline mismatch")
    for key in ("deadline_date", "deadline_time", "deadline_timezone", "source_timezone"):
        expected = deadline[
            "date"
            if key == "deadline_date"
            else "time"
            if key == "deadline_time"
            else "timezone"
            if key == "deadline_timezone"
            else "source_timezone"
        ]
        if getattr(payload, key) != expected:
            raise ValueError("deadline display mismatch")
    if not payload.assessments:
        raise ValueError("requirement assessments required")
    valid_refs = {
        (d["document_id"], c["location"], c["text"]) for d in docs for c in d.get("content", [])
    }
    tender_text_exists = bool(valid_refs)
    headings = {line.lstrip("# ").strip() for line in kb.splitlines() if line.startswith("#")}
    for assessment in payload.assessments:
        if assessment.status not in {"met", "not_met", "partially_met", "unverified"}:
            raise ValueError("invalid assessment status")
        for ref in assessment.tender_evidence:
            if not any(
                doc_id == ref.document_id and loc == ref.location and ref.quote in text
                for doc_id, loc, text in valid_refs
            ):
                raise ValueError("invalid tender evidence reference")
        for citation in assessment.company_evidence:
            if citation.section not in headings or citation.quote not in kb:
                raise ValueError("invalid KB citation")
        if assessment.status == "met" and not assessment.company_evidence:
            raise ValueError("company support missing citation")
        if assessment.status == "not_met" and (
            not assessment.company_evidence or not assessment.tender_evidence
        ):
            raise ValueError("negative assessment needs tender and KB evidence")
        if assessment.status != "unverified" and not assessment.company_evidence:
            raise ValueError("unsupported capability must be unverified")
        if assessment.status == "unverified" and assessment.gap is None:
            raise ValueError("unverified gap required")
        if tender_text_exists and not assessment.tender_evidence:
            raise ValueError("requirement lacks tender evidence")
        if (
            assessment.status == "unverified"
            and not assessment.company_evidence
            and (
                "no evidence on file" not in assessment.gap.casefold()
                or "no evidence on file" not in assessment.assessment.casefold()
            )
        ):
            raise ValueError("unsupported capability assessment must say no evidence on file")
    assessed = {item.requirement.strip().casefold() for item in payload.assessments}
    if any(item.strip().casefold() not in assessed for item in payload.requirements):
        raise ValueError("requirement has no assessment")
    expected_verdict = (
        "DO NOT APPLY"
        if any(item.status == "not_met" for item in payload.assessments)
        else "APPLY"
        if all(item.status == "met" for item in payload.assessments) and not payload.gaps
        else "APPLY WITH CONDITIONS"
    )
    if payload.verdict != expected_verdict:
        raise ValueError("verdict does not follow requirement assessments and gaps")
    if incomplete and not payload.limitations:
        raise ValueError("incomplete input limitation required")
    if deadline["status"] != "RESOLVED" and any(
        (payload.deadline_date, payload.deadline_time, payload.deadline_timezone)
    ):
        raise ValueError("unresolved deadline fabricated")
    window = settings.urgency_window_days if settings else None
    expected_urgent = False
    if deadline["status"] == "RESOLVED" and window is not None:
        target = datetime.fromisoformat(deadline["deadline_utc"])
        expected_urgent = 0 <= _business_days_until(now.astimezone(UTC), target) <= window
    if payload.urgency is not expected_urgent:
        raise ValueError("urgency inconsistent with configured deadline window")


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _business_days_until(now: datetime, target: datetime) -> int:
    """Count weekdays after today through the UTC deadline date; holidays are not configured."""
    if target < now:
        return -1
    days = (target.date() - now.date()).days
    return sum(
        1
        for offset in range(1, days + 1)
        if (now.date() + timedelta(days=offset)).weekday() < 5
    )


def _transport_error_class(error_code: str) -> str:
    normalized = error_code.casefold().replace("-", "_").replace(" ", "_")
    if normalized in TRANSIENT_ERRORS:
        return normalized
    if "timeout" in normalized or "timed_out" in normalized:
        return "timeout"
    if "429" in normalized or "rate_limit" in normalized or "too_many_requests" in normalized:
        return "rate_limit"
    if "5xx" in normalized or "server_error" in normalized or "http_5" in normalized:
        return "http_5xx"
    return normalized


def _provider(profile: LLMProfile) -> str:
    # Keep provider metadata safe: host only, never path/query/userinfo.
    return urlparse(profile.base_url).hostname or "configured-provider"


def format_verdict(verdict: Verdict | None, outcome: VerdictOutcome) -> VerdictContent:
    if verdict is None or outcome.status is not OutcomeStatus.AVAILABLE:
        return VerdictContent(
            outcome.status,
            None,
            [],
            [],
            {},
            None,
            None,
            None,
            False,
            "Some tender inputs were unavailable; assessment may be incomplete."
            if outcome.incomplete_inputs
            else None,
            outcome.incomplete_inputs,
            outcome.status is not OutcomeStatus.AVAILABLE,
        )
    assessments = verdict.requirements_summary or []
    applicability = "\n".join(
        "\n".join(
            part for part in (str(x.get("assessment", "")), str(x.get("gap") or "")) if part
        )
        for x in assessments
        if isinstance(x, dict)
    )
    gaps = [
        str(item.get("gap"))
        for item in assessments
        if isinstance(item, dict) and item.get("gap")
    ]
    return VerdictContent(
        OutcomeStatus.AVAILABLE,
        verdict.background_summary,
        assessments,
        gaps,
        {
            "status": verdict.deadline_status or "UNRESOLVED",
            "deadline_utc": verdict.deadline_utc,
            "date": verdict.deadline_date,
            "time": verdict.deadline_time,
            "timezone": verdict.deadline_timezone,
            "source_timezone": verdict.source_timezone,
        },
        applicability,
        verdict.recommendation,
        verdict.confidence,
        verdict.urgency_flag,
        "Some tender inputs were unavailable; assessment may be incomplete."
        if verdict.incomplete_inputs
        else None,
        verdict.incomplete_inputs,
        False,
    )

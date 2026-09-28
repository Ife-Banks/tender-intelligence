"""Evidence-grounded Stage B assessment (docs/07; Prompt 14)."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
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
from tender_intelligence.verdict.retrieval import retrieve_relevant_evidence

log = logging.getLogger("tender_intelligence.verdict")
SCHEMA_VERSION = "verdict.v1"
PROMPT_VERSION = "stage-b.v1"
ROLE = "verdict"
DOCUMENT_MAP_MAX_BYTES = 48_000
DOCUMENT_MAP_CHUNK_CHARS = 4_000
TRANSIENT_ERRORS = {
    "ai_call_timeout",
    "timeout",
    "rate_limit",
    "http_429",
    "http_5xx",
    "provider_unreachable",          # transport-layer drop; safe to retry
    "timeout_before_http_response",  # connection timeout before any response
}
# Deterministic errors that should NOT be retried with the same payload.
# These indicate the request itself is too large or malformed — retrying
# the identical request will produce the same failure.
DETERMINISTIC_ERRORS = {
    "provider_payload_too_large",
    "provider_request_rejected",
    "provider_authentication_failed",
    "invalid_provider_response",
    "provider_context_window_exceeded",
}


class OutcomeStatus(StrEnum):
    AVAILABLE = "VERDICT_AVAILABLE"
    FAILED = "VERDICT_FAILED"
    BUDGET = "AWAITING_BUDGET"
    APPROVAL = "AWAITING_APPROVED_PROVIDER"


class VerdictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: int
    location: StrictStr
    quote: StrictStr = Field(min_length=1, max_length=2000)


class KBCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: StrictStr
    quote: StrictStr = Field(min_length=1, max_length=2000)


class MaterialRequirement(BaseModel):
    """One source-grounded requirement from the independent extraction pass."""

    model_config = ConfigDict(extra="forbid", strict=True)
    requirement: StrictStr
    tender_evidence: list[VerdictDocument] = Field(min_length=1)


class RequirementExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requirements: list[MaterialRequirement] = Field(min_length=1)


class DocumentMapEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    location: StrictStr = Field(min_length=1, max_length=200)
    quote: StrictStr = Field(min_length=1, max_length=1000)


class DocumentMapResult(BaseModel):
    """Internal, source-grounded map result; never the final verdict contract."""

    model_config = ConfigDict(extra="forbid", strict=True)
    summary: StrictStr = Field(min_length=1, max_length=1200)
    evidence: list[DocumentMapEvidence] = Field(max_length=4)


class DocumentMapSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    location: StrictStr = Field(min_length=1, max_length=200)
    text: StrictStr = Field(min_length=1, max_length=1200)


class DocumentMapContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    location: StrictStr = Field(min_length=1, max_length=200)
    text: StrictStr = Field(min_length=1, max_length=500)


class DocumentMapEntry(BaseModel):
    """Locally validated, bounded document representation sent to later Stage B calls."""

    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: int
    filename: StrictStr
    document_type: StrictStr | None
    language: StrictStr | None
    extraction_status: StrictStr
    extraction_method: StrictStr
    processing_warning: StrictStr | None
    source_url: StrictStr
    checksum: StrictStr | None
    mime_type: StrictStr | None
    error_code: StrictStr | None
    summary: list[DocumentMapSummary]
    source_segment_count: int = Field(ge=0)
    mapped_segment_count: int = Field(ge=0)
    content: list[DocumentMapContent]


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
        client_factory: ClientFactory | None,
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
        if self.client_factory is None:
            return self._failure(
                tender, "verdict_client_unavailable", status=OutcomeStatus.APPROVAL
            )
        settings = self.session.get(Setting, 1)
        if settings is not None and settings.monthly_ai_budget is not None:
            warning_share = (settings.alert_thresholds or {}).get(
                "monthly_budget_warning_share", 0.8
            )
            if (
                isinstance(warning_share, (int, float))
                and not isinstance(warning_share, bool)
                and self._budget_spent(settings)
                >= float(settings.monthly_ai_budget) * float(warning_share)
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
            return self._failure(tender, "knowledge_base_unavailable", incomplete_inputs=incomplete)
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
        if not any(document.get("content") for document in source_docs):
            return self._failure(
                tender, "requirement_sources_unavailable", incomplete_inputs=incomplete
            )
        # Drop documents with no extracted content before any LLM calls.
        # For this tender, 15/16 docs had empty content — sending skeleton metadata
        # for them wastes thousands of tokens with zero informational value.
        # source_docs is kept unfiltered for the unavailable_documents counter.
        docs = [doc for doc in docs if doc.get("content")]
        # The entire KB is retained in the reduce request. If it alone cannot fit, fail safely.
        kb_tokens = max(kb_version.token_count, _estimate_tokens(kb))
        warning_share = (
            (settings.alert_thresholds or {}).get("kb_token_warning_share") if settings else None
        )
        if (
            isinstance(warning_share, (int, float))
            and not isinstance(warning_share, bool)
            and kb_tokens >= selected.context_window_tokens * float(warning_share)
        ):
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
            _estimate_tokens(json.dumps(docs)) + overhead + output_reserve
            > selected.context_window_tokens
        ):
            map_reduce = True
            docs, map_error = self._map_documents(
                tender, selected, docs, kb, kb_version, deadline, incomplete
            )
            if map_error:
                return self._failure(tender, map_error, incomplete_inputs=incomplete)
        extraction_profile = selected
        docs, extraction_messages, context_error = _fit_requirement_context(
            docs, extraction_profile, tender
        )
        if context_error:
            return self._failure(tender, context_error, incomplete_inputs=incomplete)
        extracted_response, extraction_profile, extraction_error = self._invoke_with_policy(
            tender, extraction_profile, fallback, extraction_messages, None
        )
        # Recover once from a provider-side byte/token limit even when our configured
        # context estimate fit. Deterministic payload failures are never retried unchanged.
        if extraction_error == "provider_payload_too_large" and not map_reduce:
            log.warning(
                "provider rejected requirement extraction payload; mapping documents before retry",
                extra={
                    "stage": "verdict", "role": ROLE, "status": "payload_reduction",
                    "tender_id": tender.id, "correlation_id": tender.correlation_id,
                    "retry_reason": "provider_payload_too_large",
                    "document_count": len(docs),
                },
            )
            docs, map_error = self._map_documents(
                tender, extraction_profile, docs, kb, kb_version, deadline, incomplete
            )
            if map_error:
                return self._failure(tender, map_error, incomplete_inputs=incomplete)
            map_reduce = True
            docs, extraction_messages, context_error = _fit_requirement_context(
                docs, extraction_profile, tender
            )
            if context_error:
                return self._failure(tender, context_error, incomplete_inputs=incomplete)
            extracted_response, extraction_profile, extraction_error = self._invoke_with_policy(
                tender, extraction_profile, fallback, extraction_messages, None
            )
        if extraction_error:
            return self._failure(tender, extraction_error, incomplete_inputs=incomplete)
        try:
            # Strip markdown fences if present (e.g., ```json ... ```)
            content = _strip_markdown_fences(extracted_response.content)
            extracted = RequirementExtraction.model_validate_json(content)
            _validate_requirement_sources(extracted, docs)
        except (ValidationError, ValueError, TypeError) as exc:
            log.warning(
                "Stage B final verdict validation failed",
                extra={
                    "stage": "verdict", "status": "verdict_validation_failed",
                    "tender_id": tender.id, "correlation_id": tender.correlation_id,
                    "extra": {
                        "json_parse_succeeded": isinstance(exc, ValidationError),
                        "schema_validation_succeeded": False,
                        "failure_class": (
                            "schema_validation" if isinstance(exc, ValidationError)
                            else "malformed_or_invalid_json"
                        ),
                    },
                },
            )
            retry_messages = extraction_messages + [
                LLMMessage(role="assistant", content=extracted_response.content),
                LLMMessage(
                    role="user",
                    content=(
                        "Correct the requirement extraction. Return only material tender "
                        "requirements with exact source quotes and valid document locations. "
                        "Return JSON only."
                    ),
                ),
            ]
            extracted_response, extraction_profile, extraction_error = self._invoke_with_policy(
                tender,
                extraction_profile,
                fallback,
                retry_messages,
                selected.max_output_tokens,
            )
            if extraction_error:
                return self._failure(tender, extraction_error, incomplete_inputs=incomplete)
            try:
                # Strip markdown fences if present (e.g., ```json ... ```)
                content = _strip_markdown_fences(extracted_response.content)
                extracted = RequirementExtraction.model_validate_json(content)
                _validate_requirement_sources(extracted, docs)
            except (ValidationError, ValueError, TypeError):
                return self._failure(
                    tender, "requirement_extraction_invalid", incomplete_inputs=incomplete
                )
        material_requirements = extracted.requirements
        retrieved = retrieve_relevant_evidence(
            material_requirements, kb, metadata=kb_version.metadata_json
        )
        selected_kb = "\n\n".join(f"# {item.heading}\n{item.text}" for item in retrieved)
        kb_provenance = [
            {"item_id": item.item_id, "heading": item.heading, "document_type": item.document_type,
             "signals": list(item.signals)} for item in retrieved
        ]
        messages = _verdict_messages(
            tender, docs, selected_kb, kb_version.id, deadline, incomplete, material_requirements,
            triage=triage,
        )
        selected = extraction_profile

        # Context budget management: check if the verdict request fits within the
        # configured context window. If not, reduce the document context before
        # making the LLM call. This is provider/model-agnostic — it uses only the
        # profile's configured context_window_tokens and max_output_tokens.
        budget = _context_budget(selected)
        verdict_tokens = _messages_tokens(messages)
        context_report = {
            "reduction_applied": False,
            "original_tokens": verdict_tokens,
            "reduced_tokens": verdict_tokens,
            "budget_tokens": budget["input_budget"],
            "omitted_documents": 0,
            "omitted_chunks": 0,
        }
        if verdict_tokens > budget["input_budget"]:
            log.info(
                "Stage B verdict context exceeds budget; reducing",
                extra={
                    "stage": "verdict",
                    "status": "context_budget_exceeded",
                    "tender_id": tender.id,
                    "correlation_id": tender.correlation_id,
                    "extra": {
                        "estimated_tokens": verdict_tokens,
                        "budget_tokens": budget["input_budget"],
                        "context_window_tokens": budget["context_window_tokens"],
                        "reserved_output_tokens": budget["reserved_output_tokens"],
                    },
                },
            )
            empty_messages = _verdict_messages(
                tender, [], selected_kb, kb_version.id, deadline, incomplete,
                material_requirements, triage=triage,
            )
            fixed_tokens = _messages_tokens(empty_messages)
            manifest_reserve = min(1000, max(200, budget["input_budget"] // 8))
            document_budget = max(
                0, budget["input_budget"] - fixed_tokens - manifest_reserve
            )
            docs, context_report = _reduce_documents_for_budget(
                docs, document_budget, tender.id, tender.correlation_id,
                requirements=material_requirements,
            )
            messages = _verdict_messages(
                tender, docs, selected_kb, kb_version.id, deadline,
                incomplete, material_requirements, triage=triage,
                reduction_report=context_report,
            )
            context_report["final_request_tokens"] = _messages_tokens(messages)
            for _ in range(3):
                if context_report["final_request_tokens"] <= budget["input_budget"]:
                    break
                previous_docs = docs
                current_doc_tokens = _estimate_tokens(json.dumps(docs, ensure_ascii=False))
                excess = context_report["final_request_tokens"] - budget["input_budget"]
                docs, next_report = _reduce_documents_for_budget(
                    docs,
                    max(0, current_doc_tokens - excess - 150),
                    tender.id,
                    tender.correlation_id,
                    requirements=material_requirements,
                    force_reduction=True,
                )
                if docs == previous_docs:
                    break
                prior = context_report
                context_report = {
                    **next_report,
                    "original_tokens": prior.get("original_tokens", verdict_tokens),
                    "omitted_documents": prior.get("omitted_documents", 0)
                    + next_report.get("omitted_documents", 0),
                    "omitted_chunks": prior.get("omitted_chunks", 0)
                    + next_report.get("omitted_chunks", 0),
                    "omitted_sections": (
                        prior.get("omitted_sections", [])
                        + next_report.get("omitted_sections", [])
                    )[:60],
                }
                messages = _verdict_messages(
                    tender, docs, selected_kb, kb_version.id, deadline,
                    incomplete, material_requirements, triage=triage,
                    reduction_report=context_report,
                )
                context_report["final_request_tokens"] = _messages_tokens(messages)
            if context_report["final_request_tokens"] > budget["input_budget"]:
                log.warning(
                    "Stage B reduced request remains above input budget",
                    extra={
                        "stage": "verdict", "status": "irreducible_context_exceeds_window",
                        "tender_id": tender.id, "correlation_id": tender.correlation_id,
                        "extra": {
                            **context_report,
                            "input_budget_tokens": budget["input_budget"],
                            "fixed_prompt_tokens": fixed_tokens,
                            "document_budget_tokens": document_budget,
                        },
                    },
                )
                return self._failure(
                    tender, "irreducible_context_exceeds_window", incomplete_inputs=incomplete
                )

        context_report["final_request_tokens"] = _messages_tokens(messages)
        context_report["original_request_bytes"] = sum(
            len(message.content.encode("utf-8")) for message in _verdict_messages(
                tender, source_docs, selected_kb, kb_version.id, deadline,
                incomplete, material_requirements, triage=triage,
            )
        )
        context_report["reduced_request_bytes"] = sum(
            len(message.content.encode("utf-8")) for message in messages
        )
        context_report["original_context_size"] = context_report["original_request_bytes"]
        context_report["reduced_context_size"] = context_report["reduced_request_bytes"]
        context_report["original_context_tokens"] = verdict_tokens
        context_report["context_window_tokens"] = budget["context_window_tokens"]
        context_report["reserved_output_tokens"] = budget["reserved_output_tokens"]
        context_report["input_budget_tokens"] = budget["input_budget"]
        context_report["represented_documents"] = len(docs)
        context_report["unavailable_documents"] = sum(
            1 for doc in source_docs
            if doc.get("extraction_status") != "extracted" or not doc.get("content")
        )
        log.info(
            "Stage B final request context measured",
            extra={
                "stage": "verdict", "status": "context_measured",
                "role": ROLE,
                "tender_id": tender.id, "correlation_id": tender.correlation_id,
                "extra": context_report,
            },
        )

        attempted_messages = messages
        result, profile, error = self._invoke_with_policy(
            tender, selected, fallback, messages, None
        )
        # If the provider still rejects the payload as too large, reduce further
        # and retry once. This handles the case where the token estimate fits but
        # the serialized request exceeds the provider's byte limit.
        if error == "provider_payload_too_large":
            log.warning(
                "Stage B verdict payload rejected by provider; reducing context and retrying",
                extra={
                    "stage": "verdict",
                    "status": "verdict_payload_reduction",
                    "tender_id": tender.id,
                    "correlation_id": tender.correlation_id,
                    "extra": {
                        "estimated_tokens": verdict_tokens,
                        "budget_tokens": budget["input_budget"],
                        "context_window_tokens": budget["context_window_tokens"],
                    },
                },
            )
            # Reduce the current evidence set further and retry exactly once. The
            # reducer receives a smaller allowance even if initial budgeting ran.
            fixed_tokens = _messages_tokens(_verdict_messages(
                tender, [], selected_kb, kb_version.id, deadline, incomplete,
                material_requirements, triage=triage,
            ))
            previous_doc_tokens = _estimate_tokens(json.dumps(docs, ensure_ascii=False))
            manifest_reserve = min(1000, max(200, budget["input_budget"] // 8))
            reduced_budget = max(
                0,
                min(
                    budget["input_budget"] - fixed_tokens - manifest_reserve,
                    int(previous_doc_tokens * 0.70),
                ),
            )
            previous_docs = docs
            docs, context_report = _reduce_documents_for_budget(
                docs, reduced_budget, tender.id, tender.correlation_id,
                requirements=material_requirements, force_reduction=True,
            )
            messages = _verdict_messages(
                tender, docs, selected_kb, kb_version.id, deadline,
                incomplete, material_requirements, triage=triage,
                reduction_report=context_report,
            )
            context_report["final_request_tokens"] = _messages_tokens(messages)
            if (
                context_report["final_request_tokens"] <= budget["input_budget"]
                and docs != previous_docs
                and _messages_tokens(messages) < _messages_tokens(attempted_messages)
            ):
                log.info(
                    "Stage B payload retry context prepared",
                    extra={
                        "stage": "verdict", "status": "payload_retry_context",
                        "role": ROLE,
                        "tender_id": tender.id, "correlation_id": tender.correlation_id,
                        "extra": {
                            **context_report,
                            "retry_reason": "provider_payload_too_large",
                            "context_window_tokens": budget["context_window_tokens"],
                            "reserved_output_tokens": budget["reserved_output_tokens"],
                            "input_budget_tokens": budget["input_budget"],
                        },
                    },
                )
                attempted_messages = messages
                result, profile, error = self._invoke_with_policy(
                    tender, selected, fallback, messages, None
                )
            else:
                error = "provider_payload_too_large"
        if error:
            return self._failure(tender, error, incomplete_inputs=incomplete)
        try:
            # Strip markdown fences if present (e.g., ```json ... ```)
            content = _strip_markdown_fences(result.content)
            payload = VerdictPayload.model_validate_json(content)
            payload = _apply_authoritative_overrides(payload, material_requirements, deadline)
            _validate_semantics(
                payload,
                docs,
                kb,
                deadline,
                incomplete,
                settings,
                self.clock(),
                material_requirements,
            )
        except (ValidationError, ValueError, TypeError):
            # Exactly one validation retry. The response itself is deliberately not logged.
            retry_messages = messages + [
                LLMMessage(role="assistant", content=result.content),
                LLMMessage(
                    role="user",
                    content=(
                        "Correct the output to satisfy the supplied JSON schema and evidence "
                        "rules. Return JSON only."
                    ),
                ),
            ]
            retry_response, retry_profile, retry_error = self._invoke_with_policy(
                tender, profile, fallback, retry_messages, selected.max_output_tokens
            )
            if retry_error:
                return self._failure(tender, retry_error, incomplete_inputs=incomplete)
            profile, result = retry_profile, retry_response
            try:
                # Strip markdown fences if present (e.g., ```json ... ```)
                content = _strip_markdown_fences(result.content)
                payload = VerdictPayload.model_validate_json(content)
                payload = _apply_authoritative_overrides(payload, material_requirements, deadline)
                _validate_semantics(
                    payload,
                    docs,
                    kb,
                    deadline,
                    incomplete,
                    settings,
                    self.clock(),
                    material_requirements,
                )
            except (ValidationError, ValueError, TypeError):
                log.warning(
                    "Stage B final verdict validation failed after correction attempt",
                    extra={
                        "stage": "verdict", "status": "verdict_validation_failed",
                        "tender_id": tender.id, "correlation_id": tender.correlation_id,
                        "extra": {
                            "json_parse_succeeded": False,
                            "schema_validation_succeeded": False,
                            "retry_attempted": True,
                        },
                    },
                )
                self._alert(
                    tender,
                    "invalid_ai_output",
                    "critical",
                    "Verdict validation failed after one retry",
                )
                return self._failure(tender, "verdict_invalid_output", incomplete_inputs=incomplete)
        log.info(
            "Stage B final verdict validated",
            extra={
                "stage": "verdict", "status": "verdict_validated",
                "role": ROLE,
                "tender_id": tender.id, "correlation_id": tender.correlation_id,
                "extra": {
                    "json_parse_succeeded": True,
                    "schema_validation_succeeded": True,
                    "verdict_validation_succeeded": True,
                },
            },
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
            knowledge_base_evidence=kb_provenance,
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
                input_tokens = (
                    _estimate_tokens("\n".join(message.content for message in messages)) + 1400
                )
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
                    log.warning(
                        "Stage B provider call failed",
                        extra={
                            "stage": "verdict",
                            "status": "provider_call_failed",
                            "correlation_id": tender.correlation_id,
                            "extra": {
                                "tender_id": tender.id,
                                "provider_http_status": exc.http_status,
                                "provider_error_code": exc.error_code,
                                "provider_error_type": exc.provider_error_type,
                                "provider_error_detail_code": exc.provider_error_code,
                                "provider_error_param": exc.provider_error_param,
                                "input_bytes": sum(
                                    len(message.content.encode("utf-8")) for message in messages
                                ),
                            },
                        },
                    )
                    # Deterministic errors (payload too large, auth failure, etc.)
                    # are NOT retried with the same payload — they will fail again.
                    if last_error == "provider_payload_too_large":
                        return None, profile, last_error
                    if last_error in DETERMINISTIC_ERRORS:
                        break
                    if _transport_error_class(last_error) in TRANSIENT_ERRORS and attempts < 2:
                        attempts += 1
                        self.sleeper(0.25 * (2 ** (attempts - 1)))
                        continue
                    if _transport_error_class(last_error) in TRANSIENT_ERRORS:
                        break
                    # Permanent rejection is not transport-retried, but the assigned
                    # approved fallback may still serve the role.
                    break
                except Exception as exc:  # safe provider boundary
                    last_error = "provider_failure"
                    self._record_call(tender, profile, "failed", last_error, started)
                    log.warning(
                        "Stage B provider client failed outside its typed error boundary (%s)",
                        type(exc).__name__,
                        extra={
                            "stage": "verdict", "role": ROLE,
                            "status": "provider_client_failure",
                            "tender_id": tender.id,
                            "correlation_id": tender.correlation_id,
                            "exception_class": type(exc).__name__,
                        },
                    )
                    break
        return None, primary, last_error

    def _map_documents(self, tender, profile, docs, kb, kb_version, deadline, incomplete):
        summaries = []
        input_size = len(json.dumps(docs, ensure_ascii=False).encode("utf-8"))
        unavailable_count = sum(not doc.get("content") for doc in docs)
        supplied_segments = [
            segment
            for document in docs
            for segment in _split_document_content(
                document.get("content") or [], DOCUMENT_MAP_CHUNK_CHARS
            )
        ]
        supplied_locations = {str(segment.get("location")) for segment in supplied_segments}
        supplied_characters = sum(len(segment.get("text", "")) for segment in supplied_segments)
        # Every document is mapped; no document is silently dropped.
        for doc in docs:
            if not doc.get("content"):
                summaries.append({
                    **{key: doc.get(key) for key in (
                        "document_id", "filename", "document_type", "language",
                        "extraction_status", "extraction_method", "processing_warning",
                        "source_url", "checksum", "mime_type", "error_code",
                    )},
                    "summary": [],
                    "source_segment_count": 0,
                    "mapped_segment_count": 0,
                    "content": [],
                })
                continue
            map_output = min(profile.max_output_tokens or 1024, 1200)
            source_segments = _split_document_content(doc["content"], DOCUMENT_MAP_CHUNK_CHARS)
            mapped_summaries = []
            mapped_evidence = []
            for segment_index, segment in enumerate(source_segments):
                map_doc = {
                    **{key: value for key, value in doc.items() if key != "content"},
                    "content": [segment],
                    "map_segment": segment_index + 1,
                    "map_segment_count": len(source_segments),
                }
                map_input = _estimate_tokens(json.dumps(map_doc, ensure_ascii=False)) + 1200
                if map_input + map_output > profile.context_window_tokens:
                    return docs, "document_exceeds_context_window"
                map_messages = [
                    LLMMessage(
                        role="system",
                        content=(
                            "Summarize material eligibility, qualifications, scope, deadline, "
                            "evaluation, submission, and commercial requirements only. Cite "
                            "exact source locations and verbatim quotes from the supplied "
                            "segment. Return one JSON object matching this JSON Schema exactly; "
                            "do not add fields or change object/array types: "
                            + json.dumps(
                                DocumentMapResult.model_json_schema(), separators=(",", ":")
                            )
                            + " Evidence quotes must be copied from the supplied segment. "
                            "Do not cite omitted content or invent missing requirements. "
                            "Return only the JSON object. Do not use Markdown fences or add "
                            "commentary."
                        ),
                    ),
                    LLMMessage(role="user", content=json.dumps(map_doc, ensure_ascii=False)),
                ]
                effective_reasoning_budget = (
                    min(profile.reasoning_budget, map_output)
                    if profile.enable_thinking and profile.reasoning_budget is not None
                    else None
                )
                log.info(
                    "Stage B document map request prepared",
                    extra={
                        "stage": "verdict",
                        "status": "document_map_request",
                        "correlation_id": tender.correlation_id,
                        "tender_id": tender.id,
                        "extra": {
                            "document_id": doc.get("document_id"),
                            "segment_index": segment_index,
                            "segment_count": len(source_segments),
                            "reduced_document_count": sum(
                                bool(item.get("content")) for item in docs
                            ),
                            "reduced_segment_count": len(supplied_segments),
                            "reduced_character_count": supplied_characters,
                            "reduced_token_estimate": _estimate_tokens(
                                json.dumps(docs, ensure_ascii=False)
                            ),
                            "unavailable_document_count": unavailable_count,
                            "supplied_document_ids": [
                                item.get("document_id") for item in docs
                                if item.get("content")
                            ],
                            "supplied_location_count": len(supplied_locations),
                            "prompt_char_count": sum(
                                len(message.content) for message in map_messages
                            ),
                            "prompt_token_estimate": _estimate_tokens(
                                "\n".join(message.content for message in map_messages)
                            ),
                            "schema_instructions_present": "JSON Schema" in map_messages[0].content,
                            "response_format_capability": bool(profile.supports_response_format),
                            "response_format_requested": bool(profile.supports_response_format),
                            "temperature": profile.temperature,
                            "top_p": profile.top_p,
                            "max_output_tokens": map_output,
                            "thinking_enabled": bool(profile.enable_thinking),
                            "reasoning_budget": effective_reasoning_budget,
                            "reasoning_effort_supported": bool(profile.supports_reasoning_effort),
                            "reasoning_effort": profile.reasoning_effort,
                            "stop_parameters_configured": False,
                            "reduced_segment_char_count": len(segment.get("text", "")),
                        },
                    },
                )
                response, _, error = self._invoke_with_policy(
                    tender, profile, None, map_messages, map_output
                )
                if error:
                    return docs, error
                if (response.raw or {}).get("finish_reason") == "length":
                    return docs, self._document_map_error(
                        tender, "document_map_provider_response_truncated", response
                    )
                correction_used = False
                try:
                    data = json.loads(response.content)
                except (json.JSONDecodeError, TypeError) as parse_exc:
                    parse_error = self._document_map_error(
                        tender,
                        "document_map_malformed_json",
                        response,
                        parse_error=parse_exc,
                    )
                    correction = map_messages + [
                        LLMMessage(role="assistant", content=response.content),
                        LLMMessage(
                            role="user",
                            content=(
                                "The previous response was not valid JSON. Return only one JSON "
                                "object matching the supplied JSON Schema exactly. Do not use "
                                "Markdown fences or commentary. Copy evidence quotes verbatim "
                                "from the supplied segment and do not invent content."
                            ),
                        ),
                    ]
                    response, _, retry_error = self._invoke_with_policy(
                        tender, profile, None, correction, map_output
                    )
                    correction_used = True
                    if retry_error:
                        return docs, parse_error
                    if (response.raw or {}).get("finish_reason") == "length":
                        return docs, self._document_map_error(
                            tender, "document_map_provider_response_truncated", response
                        )
                    try:
                        data = json.loads(response.content)
                    except (json.JSONDecodeError, TypeError) as retry_parse_exc:
                        return docs, self._document_map_error(
                            tender,
                            "document_map_malformed_json",
                            response,
                            parse_error=retry_parse_exc,
                        )
                try:
                    mapped = DocumentMapResult.model_validate(data)
                    # Safety net: truncate overlong but valid quotes before further validation
                    mapped = _truncate_overlong_quotes(mapped, segment["text"])
                except ValidationError as exc:
                    errors = exc.errors()
                    code = _document_map_validation_code(errors, data)
                    self._document_map_error(
                        tender, code, response, parsed_data=data, validation_errors=errors
                    )
                    if correction_used:
                        return docs, code
                    # Match the existing Stage B correction policy: one bounded
                    # validation retry, with local field/type diagnostics only.
                    correction = map_messages + [
                        LLMMessage(role="assistant", content=response.content),
                        LLMMessage(
                            role="user",
                            content=(
                                "The previous JSON failed local schema validation. Correct only "
                                "the structure to match the supplied JSON Schema. Preserve the "
                                "same source locations and copy evidence quotes verbatim from "
                                "the supplied segment. Validation issues: "
                                + json.dumps(_safe_validation_shape(errors), separators=(",", ":"))
                            ),
                        ),
                    ]
                    response, _, retry_error = self._invoke_with_policy(
                        tender, profile, None, correction, map_output
                    )
                    if retry_error:
                        return docs, code
                    if (response.raw or {}).get("finish_reason") == "length":
                        return docs, self._document_map_error(
                            tender, "document_map_provider_response_truncated", response
                        )
                    try:
                        data = json.loads(response.content)
                        mapped = DocumentMapResult.model_validate(data)
                        # Safety net: truncate overlong but valid quotes before further validation
                        mapped = _truncate_overlong_quotes(mapped, segment["text"])
                    except (json.JSONDecodeError, TypeError):
                        return docs, self._document_map_error(
                            tender, "document_map_malformed_json", response
                        )
                    except ValidationError as retry_exc:
                        retry_errors = retry_exc.errors()
                        return docs, self._document_map_error(
                            tender,
                            _document_map_validation_code(retry_errors, data),
                            response,
                            parsed_data=data,
                            validation_errors=retry_errors,
                        )
                mapped_summaries.append({"location": segment["location"], "text": mapped.summary})
                for ref_index, ref in enumerate(mapped.evidence):
                    if ref.location != segment["location"]:
                        return docs, self._document_map_error(
                            tender,
                            "document_map_invalid_reference",
                            response,
                            parsed_data=data,
                            schema_validation_succeeded=True,
                            reference_details={
                                "failure_category": "location_not_in_supplied_segment",
                                "document_id": doc.get("document_id"),
                                "reference_index": ref_index,
                                "reference_location": ref.location,
                                "supplied_evidence": [{
                                    "document_id": doc.get("document_id"),
                                    "location": segment.get("location"),
                                }],
                            },
                        )
                    quote = _normalize_text(ref.quote)
                    if not quote or quote not in _normalize_text(segment["text"]):
                        return docs, self._document_map_error(
                            tender,
                            "document_map_invalid_reference",
                            response,
                            parsed_data=data,
                            schema_validation_succeeded=True,
                            reference_details={
                                "failure_category": "quote_not_found_in_supplied_segment",
                                "document_id": doc.get("document_id"),
                                "reference_index": ref_index,
                                "reference_location": ref.location,
                                "reference_quote_length": len(ref.quote),
                                "supplied_evidence": [{
                                    "document_id": doc.get("document_id"),
                                    "location": segment.get("location"),
                                    "text_length": len(segment.get("text", "")),
                                }],
                            },
                        )
                    item = {"location": ref.location, "text": ref.quote}
                    if item not in mapped_evidence:
                        mapped_evidence.append(item)
            summaries.append(
                {
                    **{key: doc.get(key) for key in (
                        "document_id", "filename", "document_type", "language",
                        "extraction_status", "extraction_method", "processing_warning",
                        "source_url", "checksum", "mime_type", "error_code",
                    )},
                    "summary": mapped_summaries,
                    "source_segment_count": len(source_segments),
                    "mapped_segment_count": len(mapped_summaries),
                    "content": mapped_evidence,
                }
            )
        output_size = len(json.dumps(summaries, ensure_ascii=False).encode("utf-8"))
        try:
            summaries = [
                DocumentMapEntry.model_validate(item).model_dump() for item in summaries
            ]
        except ValidationError as exc:
            issue = (exc.errors() or [{}])[0]
            log.warning(
                "Stage B document map failed local validation",
                extra={
                    "stage": "verdict",
                    "status": "document_map_internal_validation_failed",
                    "error_code": "document_map_internal_validation_failed",
                    "correlation_id": tender.correlation_id,
                    "extra": {
                        "tender_id": tender.id,
                        "invalid_field": ".".join(str(part) for part in issue.get("loc", ())),
                        "invalid_type": issue.get("type"),
                        "document_count": len(summaries),
                        "map_valid": False,
                    },
                },
            )
            return docs, "document_map_internal_validation_failed"
        if output_size > DOCUMENT_MAP_MAX_BYTES:
            log.warning(
                "Stage B document map exceeded its byte bound",
                extra={
                    "stage": "verdict",
                    "status": "document_map_over_limit",
                    "correlation_id": tender.correlation_id,
                    "extra": {
                        "tender_id": tender.id,
                        "reduced_bytes": output_size,
                        "max_bytes": DOCUMENT_MAP_MAX_BYTES,
                        "document_count": len(summaries),
                        "unavailable_document_count": unavailable_count,
                        "map_valid": True,
                    },
                },
            )
            return docs, "document_map_reduction_exceeded_limit"
        log.info(
            "Stage B document map validated",
            extra={
                "stage": "verdict",
                "status": "document_map_valid",
                "correlation_id": tender.correlation_id,
                "extra": {
                    "tender_id": tender.id,
                    "input_bytes": input_size,
                    "reduced_bytes": output_size,
                    "document_count": len(summaries),
                    "unavailable_document_count": unavailable_count,
                    "source_segment_count": sum(
                        item.get("source_segment_count", 0) for item in summaries
                    ),
                    "mapped_segment_count": sum(
                        item.get("mapped_segment_count", 0) for item in summaries
                    ),
                    "map_valid": True,
                },
            },
        )
        return summaries, None

    @staticmethod
    def _document_map_error(
        tender,
        code,
        response,
        *,
        parsed_data=None,
        validation_errors=None,
        schema_validation_succeeded=False,
        reference_details=None,
        parse_error=None,
    ):
        """Log response diagnostics without retaining prompt or completion contents."""
        response_diagnostics = _response_content_diagnostics(response.content, parse_error)
        log.warning(
            "Stage B document map validation failed",
            extra={
                "stage": "verdict",
                "status": code,
                "error_code": code,
                "correlation_id": tender.correlation_id,
                "extra": {
                    "tender_id": tender.id,
                    "provider_http_status": (response.raw or {}).get("provider_http_status"),
                    "response_bytes": (response.raw or {}).get(
                        "response_bytes", len(response.content.encode("utf-8"))
                    ),
                    **response_diagnostics,
                    "request_config": _safe_request_diagnostics(
                        (response.raw or {}).get("request_config")
                    ),
                    "response_extraction": _safe_response_extraction_diagnostics(
                        response.raw
                    ),
                    "provider_finish_reason": (response.raw or {}).get("finish_reason"),
                    "provider_prompt_tokens_reported": getattr(
                        getattr(response, "usage", None), "prompt_tokens", None
                    ),
                    "provider_completion_tokens_reported": getattr(
                        getattr(response, "usage", None), "completion_tokens", None
                    ),
                    "json_parse_succeeded": response_diagnostics[
                        "response_json_parse_succeeded"
                    ],
                    "schema_validation_succeeded": schema_validation_succeeded,
                    "parsed_response_shape": (
                        _safe_json_shape(parsed_data)
                        if response_diagnostics["response_json_parse_succeeded"]
                        else response_diagnostics["response_shape_preview"]
                    ),
                    "validation_issues": _safe_validation_shape(validation_errors or []),
                    "reference_details": reference_details,
                },
            },
        )
        return code

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
                request_config=(getattr(response, "raw", {}) or {}).get("request_config")
                if response
                else _profile_request_config(profile),
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
        log.warning(
            "Stage B produced an unavailable verdict: %s", code,
            extra={
                "stage": "verdict", "role": ROLE, "status": status.value,
                "error_code": code,
                "tender_id": tender.id if tender else None,
                "correlation_id": tender.correlation_id if tender else None,
            },
        )
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


def _split_document_content(chunks: list[dict[str, str]], max_chars: int):
    """Split extracted chunks at nearby sentence/line boundaries without changing locations."""
    result = []
    for chunk in chunks:
        text = chunk.get("text", "")
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                floor = start + max_chars // 2
                boundary = max(
                    text.rfind("\n", floor, end),
                    text.rfind(". ", floor, end),
                    text.rfind("; ", floor, end),
                    text.rfind(" ", floor, end),
                )
                if boundary > start:
                    end = boundary + 1
            result.append({"location": chunk["location"], "text": text[start:end]})
            start = end
    return result


def _response_content_diagnostics(
    content: str, parse_error: json.JSONDecodeError | None = None
) -> dict[str, Any]:
    """Classify response text without retaining any response values."""
    text = content if isinstance(content, str) else ""
    stripped = text.strip()
    parse_succeeded = False
    parsed_shape = None
    local_error = parse_error
    if local_error is None:
        try:
            parsed = json.loads(text)
            parse_succeeded = True
            parsed_shape = _safe_json_shape(parsed)
        except (json.JSONDecodeError, TypeError) as exc:
            local_error = exc if isinstance(exc, json.JSONDecodeError) else None

    first = stripped[0] if stripped else ""
    last = stripped[-1] if stripped else ""

    def structural_character(character: str) -> str | None:
        if not character:
            return None
        if character in "{}[]`\\\"'":
            return character
        return "<redacted_character>"

    starts_fence = stripped.startswith("```")
    if parse_succeeded:
        structure = (parsed_shape or {}).get("kind", "unknown")
    elif not stripped:
        structure = "empty_or_whitespace"
    elif starts_fence:
        structure = "markdown_fenced"
    elif first == "{":
        near_end = local_error and local_error.pos >= len(text.rstrip()) - 1
        structure = "incomplete_json_object" if near_end else "malformed_json_object"
    elif first == "[":
        near_end = local_error and local_error.pos >= len(text.rstrip()) - 1
        structure = "incomplete_json_array" if near_end else "malformed_json_array"
    else:
        structure = "plain_text_or_unrecognized_json"

    error_category = None
    if local_error:
        message = local_error.msg.casefold()
        if "unterminated" in message or "expecting value" in message:
            error_category = "unexpected_end_or_missing_value"
        elif "property name" in message:
            error_category = "invalid_or_missing_property_name"
        elif "escape" in message:
            error_category = "invalid_escape"
        elif "extra data" in message:
            error_category = "extra_data"
        else:
            error_category = "invalid_json_syntax"

    return {
        "response_char_count": len(text),
        "response_first_non_whitespace_character": structural_character(first),
        "response_last_non_whitespace_character": structural_character(last),
        "response_starts_with_json_object": first == "{",
        "response_starts_with_json_array": first == "[",
        "response_starts_with_markdown_fence": starts_fence,
        "response_contains_markdown_fence": "```" in text,
        "response_is_empty_or_whitespace": not stripped,
        "response_json_parse_succeeded": parse_succeeded,
        "response_json_parse_error_category": error_category,
        "response_json_parse_error_position": local_error.pos if local_error else None,
        "response_json_parse_error_message_sanitized": error_category,
        "response_shape_preview": parsed_shape or {"kind": structure},
    }


def _safe_request_diagnostics(request_config: Any) -> dict[str, Any] | None:
    """Whitelist nonsensitive request settings for failure logs."""
    if not isinstance(request_config, dict):
        return None
    allowed = {
        "temperature", "top_p", "max_tokens", "enable_thinking", "reasoning_budget",
        "reasoning_effort", "supports_response_format", "response_format_requested",
        "supports_include_reasoning", "supports_chat_template_kwargs",
        "supports_reasoning_effort", "stop_parameters_configured",
    }
    return {key: request_config[key] for key in allowed if key in request_config}


def _safe_response_extraction_diagnostics(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    allowed = {
        "choice_count", "selected_choice_index", "message_content_field_present",
        "message_content_type", "reasoning_field_present", "refusal_field_present",
        "tool_calls_field_present",
    }
    return {key: raw[key] for key in allowed if key in raw}


def _safe_validation_shape(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose schema paths/types while excluding all rejected input values."""
    safe = []
    for error in errors[:8]:
        path = []
        for part in error.get("loc", ())[:8]:
            if isinstance(part, int) or (
                isinstance(part, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", part)
            ):
                path.append(part)
            else:
                path.append("<field>")
        actual = error.get("input")
        safe.append({
            "field_path": path,
            "error_type": str(error.get("type", "unknown"))[:60],
            "actual_type": type(actual).__name__ if "input" in error else None,
        })
    return safe


def _truncate_overlong_quotes(
    mapped: DocumentMapResult, segment_text: str, max_quote_length: int = 1000
) -> DocumentMapResult:
    """
    Safety net: if a quote is a valid substring but exceeds max_length, truncate it
    to max_length. A prefix of a verbatim quote is still verbatim/findable.
    
    This avoids depending on model compliance for something we can fix deterministically.
    """
    normalized_segment = _normalize_text(segment_text)
    truncated_evidence = []
    
    for ev in mapped.evidence:
        quote = ev.quote
        normalized_quote = _normalize_text(quote)
        
        # If quote is valid but overlong, truncate to max_length
        if len(quote) > max_quote_length and normalized_quote in normalized_segment:
            quote = quote[:max_quote_length]
        
        truncated_evidence.append(
            DocumentMapEvidence(location=ev.location, quote=quote)
        )
    
    return DocumentMapResult(
        summary=mapped.summary,
        evidence=truncated_evidence
    )



def _strip_markdown_fences(content: str) -> str:
    """Remove markdown code fences (```json ... ```) from LLM output if present."""
    content = content.strip()
    if content.startswith("```"):
        # Find the end of the first line (the opening fence + optional language)
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]  # Skip opening fence line
    
    if content.endswith("```"):
        # Remove closing fence
        content = content[:-3].rstrip()
    
    return content


def _safe_json_shape(value: Any, depth: int = 2) -> dict[str, Any] | None:
    """Describe parsed JSON types/keys only; never log scalar values or document text."""
    if value is None:
        return {"kind": "null"}
    if isinstance(value, dict):
        safe_fields = {}
        for key in list(value)[:16]:
            safe_key = (
                key if isinstance(key, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", key)
                else "<non_identifier_key>"
            )
            safe_fields[safe_key] = (
                _safe_json_shape(value[key], depth - 1) if depth > 0
                else {"kind": type(value[key]).__name__}
            )
        return {"kind": "object", "fields": safe_fields, "field_count": len(value)}
    if isinstance(value, list):
        item_shape = _safe_json_shape(value[0], depth - 1) if value and depth > 0 else None
        return {"kind": "array", "length": len(value), "item_shape": item_shape}
    if isinstance(value, str):
        return {"kind": "string", "length": len(value)}
    if isinstance(value, bool):
        return {"kind": "boolean"}
    if isinstance(value, int | float):
        return {"kind": "number"}
    return {"kind": "unknown"}


def _document_map_validation_code(
    errors: list[dict[str, Any]], data: Any = None
) -> str:
    if (
        isinstance(data, dict)
        and len(data) == 1
        and isinstance(next(iter(data.values())), dict)
        and {"summary", "evidence"}.issubset(next(iter(data.values())))
    ):
        return "document_map_unexpected_nesting"
    error_types = {item.get("type") for item in errors}
    if "missing" in error_types:
        return "document_map_missing_field"
    if error_types & {
        "model_type", "string_type", "list_type", "int_type", "dict_type",
        "bool_type", "float_type",
    }:
        return "document_map_invalid_type"
    if error_types & {
        "string_too_long", "string_too_short", "too_long", "too_short",
    }:
        return "document_map_constraint_violation"
    if "extra_forbidden" in error_types:
        return "document_map_unexpected_field"
    return "document_map_unexpected_schema"


def _bundle_documents(bundle):
    """Flatten the bundle into per-document content chunks for the AI stage.

    Tables are emitted as their own chunks, so a weighting scheme or a deadline matrix arrives as a
    rectangular table rather than prose. A DOCX table section's ``text`` is *already* that table's
    rendering (prompt 09 §8 keeps reading order intact), so its tables are not emitted a second
    time: repeating them would double every figure in the prompt, which is exactly the kind of
    duplication that makes a model quote a total twice or add a row to itself.
    """
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
            if section.kind == "table":
                # section.text is this table's rendering; emitting the rows again duplicates it.
                continue
            chunks.extend(
                {"location": table.location, "text": table.to_text()} for table in section.tables
            )
        if not chunks and extraction.text:
            chunks.append({"location": "document", "text": extraction.text})
        docs.append(
            {
                "document_id": extraction.document_id,
                "filename": extraction.filename,
                "document_type": extraction.mime_type,
                "language": extraction.language,
                "extraction_status": extraction.extraction_status,
                "extraction_method": extraction.extraction_method,
                "processing_warning": extraction.metadata.skip_reason,
                "source_url": extraction.source_url,
                "checksum": extraction.checksum,
                "mime_type": extraction.mime_type,
                "error_code": extraction.error_code,
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


def _requirement_messages(
    docs: list[dict[str, Any]], reduction_report: dict[str, Any] | None = None
) -> list[LLMMessage]:
    # Build a concrete example that matches the schema exactly
    example_output = {
        "requirements": [
            {
                "requirement": "Must provide 24/7 technical support",
                "tender_evidence": [
                    {
                        "document_id": 1,
                        "location": "page 5, section 3.2",
                        "quote": "The vendor must provide 24/7 technical support..."
                    }
                ]
            },
            {
                "requirement": "Must have ISO 27001 certification",
                "tender_evidence": [
                    {
                        "document_id": 2,
                        "location": "requirements section, item 4",
                        "quote": "ISO 27001 certification is mandatory"
                    }
                ]
            }
        ]
    }
    
    return [
        LLMMessage(
            role="system",
            content=(
                "Extract every material eligibility, experience, qualification, and required-"
                "submission requirement from the supplied tender documents. Do not assess the "
                "company.\n\n"
                "Output ONLY a JSON object matching this EXACT structure:\n"
                + json.dumps(example_output, indent=2, ensure_ascii=False)
                + "\n\nRules:\n"
                "- Each requirement must cite one or more exact document quotes and their "
                "existing document_id/location.\n"
                "- Do not invent requirements or locations.\n"
                "- Do not cite content listed as omitted in the supplied context manifest.\n"
                "- Output only valid JSON - no markdown fences, no explanations."
            ),
        ),
        LLMMessage(
            role="user",
            content=json.dumps(
                {
                    "documents": docs,
                    "context_manifest": {
                        "omitted_sections": (reduction_report or {}).get("omitted_sections", []),
                        "omitted_documents": (reduction_report or {}).get("omitted_documents", 0),
                        "omitted_chunks": (reduction_report or {}).get("omitted_chunks", 0),
                    },
                },
                ensure_ascii=False,
            ),
        ),
    ]


def _fit_requirement_context(docs, profile, tender):
    """Bound the complete requirement-extraction request, including its fixed prompt."""
    messages = _requirement_messages(docs)
    budget = _context_budget(profile)
    original_tokens = _messages_tokens(messages)
    if original_tokens <= budget["input_budget"]:
        return docs, messages, None
    fixed_tokens = _messages_tokens(_requirement_messages([]))
    reserve = min(1000, max(200, budget["input_budget"] // 8))
    allowance = max(0, budget["input_budget"] - fixed_tokens - reserve)
    original_docs = docs
    docs, report = _reduce_documents_for_budget(
        docs, allowance, tender.id, tender.correlation_id
    )
    messages = _requirement_messages(docs, report)
    estimated_tokens = _messages_tokens(messages)
    for _ in range(3):
        if estimated_tokens <= budget["input_budget"]:
            break
        current_tokens = _estimate_tokens(json.dumps(docs, ensure_ascii=False))
        excess = estimated_tokens - budget["input_budget"]
        reduced, next_report = _reduce_documents_for_budget(
            docs,
            max(0, current_tokens - excess - 150),
            tender.id,
            tender.correlation_id,
            force_reduction=True,
        )
        if reduced == docs:
            break
        report = {
            **next_report,
            "original_tokens": _estimate_tokens(json.dumps(original_docs, ensure_ascii=False)),
            "omitted_documents": report.get("omitted_documents", 0)
            + next_report.get("omitted_documents", 0),
            "omitted_chunks": report.get("omitted_chunks", 0)
            + next_report.get("omitted_chunks", 0),
            "omitted_sections": (
                report.get("omitted_sections", []) + next_report.get("omitted_sections", [])
            )[:60],
        }
        docs = reduced
        messages = _requirement_messages(docs, report)
        estimated_tokens = _messages_tokens(messages)
    log.info(
        "Stage B requirement extraction context measured",
        extra={
            "stage": "verdict", "role": ROLE, "status": "context_measured",
            "tender_id": tender.id, "correlation_id": tender.correlation_id,
            "extra": {
                **report,
                "estimated_input_tokens": estimated_tokens,
                "configured_context_window": budget["context_window_tokens"],
                "reserved_output_tokens": budget["reserved_output_tokens"],
                "input_budget": budget["input_budget"],
                "reduction_applied": report["reduction_applied"],
            },
        },
    )
    if estimated_tokens > budget["input_budget"]:
        log.warning(
            "Stage B requirement context could not be reduced to its budget",
            extra={
                "stage": "verdict", "role": ROLE,
                "status": "reduced_context_exceeds_window",
                "tender_id": tender.id, "correlation_id": tender.correlation_id,
                "extra": {
                    "estimated_input_tokens": estimated_tokens,
                    "input_budget": budget["input_budget"],
                    "context_window_tokens": budget["context_window_tokens"],
                    "reserved_output_tokens": budget["reserved_output_tokens"],
                    "document_count": len(docs),
                    "documents_with_content": sum(bool(doc.get("content")) for doc in docs),
                    "omitted_document_count": report.get("omitted_documents", 0),
                    "omitted_section_count": report.get("omitted_chunks", 0),
                },
            },
        )
        return docs, messages, "reduced_context_exceeds_window"
    return docs, messages, None


def _validate_requirement_sources(extraction: RequirementExtraction, source_docs) -> None:
    valid_refs = {
        (document["document_id"], chunk["location"], chunk["text"])
        for document in source_docs
        for chunk in document.get("content", [])
    }
    # Build per-document full text for cross-chunk quote matching.
    # When the LLM cites a sentence that spans a chunk boundary, the exact quote
    # won't be a substring of any single chunk but will appear in the concatenated text.
    doc_full_text: dict[int, str] = {}
    for document in source_docs:
        chunks_text = " ".join(
            chunk["text"] for chunk in document.get("content", []) if chunk.get("text")
        )
        doc_full_text[document["document_id"]] = chunks_text
    seen: set[str] = set()
    for requirement in extraction.requirements:
        normalized = requirement.requirement.strip().casefold()
        if not normalized or normalized in seen:
            raise ValueError("empty or duplicate independently extracted requirement")
        seen.add(normalized)
        for evidence in requirement.tender_evidence:
            matched = False
            # Handle comma-separated locations (e.g., "section:23, 24")
            # Split and try each location part individually
            evidence_locations = [loc.strip() for loc in evidence.location.split(',')]
            
            for document_id, location, text in valid_refs:
                if document_id != evidence.document_id:
                    continue
                # Check if this chunk location matches any of the evidence locations
                if location not in evidence_locations:
                    continue
                if not evidence.quote:
                    continue
                # Level 1: exact match within the cited chunk
                # Level 2: normalized match within the cited chunk
                # Level 3: normalized match within the full document text (handles cross-chunk quotes)
                norm_quote = _normalize_text(evidence.quote)
                norm_chunk = _normalize_text(text)
                norm_doc = _normalize_text(doc_full_text.get(document_id, ""))
                if (
                    evidence.quote in text
                    or (norm_quote and norm_quote in norm_chunk)
                    or (norm_quote and norm_quote in norm_doc)
                ):
                    matched = True
                    break
            if not matched:
                import sys as _sys
                import json as _json
                print(f"\n🔴 QUOTE MISMATCH DEBUG", file=_sys.stderr)
                print(f"  document_id: {evidence.document_id}", file=_sys.stderr)
                print(f"  location:    {evidence.location}", file=_sys.stderr)
                print(f"  split locs:  {evidence_locations}", file=_sys.stderr)
                print(f"  quote:       {repr(evidence.quote[:200])}", file=_sys.stderr)
                # Find the matching location in valid_refs to compare
                found_any_location = False
                for doc_id, loc, text in valid_refs:
                    if doc_id == evidence.document_id and loc in evidence_locations:
                        print(f"  source text: {repr(text[:200])}", file=_sys.stderr)
                        found_any_location = True
                        break
                if not found_any_location:
                    matching_locs = [loc for doc_id, loc, _ in valid_refs if doc_id == evidence.document_id]
                    print(f"  ❌ none of the split locations found in valid_refs!", file=_sys.stderr)
                    print(f"  available locations for doc {evidence.document_id}: {sorted(matching_locs)[:20]}", file=_sys.stderr)
                raise ValueError("requirement source evidence is invalid")


def _apply_authoritative_overrides(payload, material_requirements, deadline):
    """Inject authoritative values that must not deviate from independently extracted data.

    Small models paraphrase requirements, forget deadline fields, and leave placeholder
    text in gaps. This function corrects all of these after parsing, before validation.
    """
    req_strings = [r.requirement for r in material_requirements]

    # Fix assessments: align requirement strings and strip unfilled placeholders
    fixed_assessments = []
    for i, assessment in enumerate(payload.assessments):
        # Map to authoritative requirement string by position (model keeps order)
        authoritative_req = req_strings[i] if i < len(req_strings) else assessment.requirement
        # Fix gap placeholder
        gap = assessment.gap
        if gap and ("<" in gap and ">" in gap):
            gap = f"No evidence on file for this requirement."
        fixed_assessments.append(assessment.model_copy(update={
            "requirement": authoritative_req,
            "gap": gap,
        }))

    # Fix gaps list placeholder
    gaps = payload.gaps
    if gaps and all("<" in g and ">" in g for g in gaps):
        gaps = ["No evidence on file for all requirements."]

    return payload.model_copy(update={
        "requirements": req_strings,
        "deadline_status": deadline.get("status", "UNRESOLVED"),
        "deadline_utc": deadline.get("deadline_utc"),
        "deadline_date": deadline.get("date"),
        "deadline_time": deadline.get("time"),
        "deadline_timezone": deadline.get("timezone"),
        "source_timezone": deadline.get("source_timezone"),
        "assessments": fixed_assessments,
        "gaps": gaps,
    })


def _verdict_messages(
    tender, docs, kb, kb_id, deadline, incomplete, requirements, *, triage=None,
    reduction_report=None,
):
    # Build a concrete example output template with the exact literal values
    # the model must use. Small models follow examples far better than JSON schemas.
    example_output = {
        "schema_version": SCHEMA_VERSION,
        "background": "<one sentence summary of this tender>",
        "requirements": [r.requirement for r in requirements],
        "deadline_status": deadline.get("status", "UNRESOLVED"),
        "deadline_utc": deadline.get("deadline_utc"),
        "deadline_date": deadline.get("date"),
        "deadline_time": deadline.get("time"),
        "deadline_timezone": deadline.get("timezone"),
        "source_timezone": deadline.get("source_timezone"),
        "assessments": [
            {
                "requirement": r.requirement,
                "tender_evidence": [e.model_dump() for e in r.tender_evidence],
                "company_evidence": [],
                "status": "unverified",
                "assessment": f"No evidence on file for this requirement.",
                "gap": f"No evidence on file for this requirement.",
            }
            for r in requirements
        ],
        "gaps": ["<gap description if any requirement cannot be met>"],
        "verdict": "DO NOT APPLY",
        "confidence": 0.8,
        "urgency": False,
        "incomplete_inputs": incomplete,
        "limitations": ["No KB evidence available for full assessment."],
    }
    system = (
        "Assess the public tender against the complete company KB. "
        "Output ONLY a JSON object with this EXACT structure (fill in the values, "
        "keep all field names exactly as shown):\n"
        + json.dumps(example_output, indent=2, ensure_ascii=False)
        + "\n\nRules:\n"
        "- verdict must be exactly one of: \"APPLY\", \"DO NOT APPLY\", \"APPLY WITH CONDITIONS\"\n"
        "- schema_version, deadline_status, deadline_utc, deadline_date, deadline_time, "
        "deadline_timezone, source_timezone: copy EXACTLY from the example above — do not change them\n"
        "- requirements: copy the requirement strings EXACTLY as shown in the example — do NOT rephrase or translate them\n"
        "- assessments: one entry per requirement, in the same order, requirement text copied verbatim\n"
        "- status per assessment: \"verified\", \"unverified\", or \"failed\"\n"
        "- company_evidence: cite exact KB section headings and quotes; leave empty [] if no KB evidence\n"
        "- gap: if status is unverified, write 'No evidence on file for [requirement]'; if met write null\n"
        "- assessment: if status is unverified, write 'No evidence on file for [requirement]'; if met describe the evidence\n"
        "- Use only supplied content. Do not fabricate evidence.\n"
        "- An empty KB means no evidence on file; it does not mean the company fails the requirement.\n"
        "- Do not turn missing attachments into failed requirements."
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
        "material_requirements": [item.model_dump() for item in requirements],
        "stage_a_triage": ({
            "status": triage.status,
            "score": triage.score,
            "mode": triage.mode,
            "reasons": triage.reasons,
        } if triage is not None else None),
        "context_manifest": (
            {
                "omitted_sections": reduction_report.get("omitted_sections", []),
                "omitted_documents": reduction_report.get("omitted_documents", 0),
                "omitted_chunks": reduction_report.get("omitted_chunks", 0),
            }
            if reduction_report
            else {"omitted_sections": [], "omitted_documents": 0, "omitted_chunks": 0}
        ),
    }
    return [
        LLMMessage("system", system),
        LLMMessage("user", json.dumps(content, ensure_ascii=False)),
    ]


def _validate_semantics(
    payload, docs, kb, deadline, incomplete, settings, now, material_requirements
):
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
    # Independently extracted exact requirement quotes are present in the final
    # request too, so preserve their traceable references even if the same chunk
    # was omitted from the broader document excerpts.
    for requirement in material_requirements:
        for evidence in requirement.tender_evidence:
            valid_refs.add((evidence.document_id, evidence.location, evidence.quote))
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
    established = {item.requirement.strip().casefold(): item for item in material_requirements}
    listed = [item.strip().casefold() for item in payload.requirements]
    assessed = [item.requirement.strip().casefold() for item in payload.assessments]
    if len(set(listed)) != len(listed) or set(listed) != set(established):
        raise ValueError("model requirements do not match independent extraction")
    if len(set(assessed)) != len(assessed) or set(assessed) != set(established):
        raise ValueError("assessments do not exactly cover independent requirements")
    assessments_by_requirement = {
        item.requirement.strip().casefold(): item for item in payload.assessments
    }
    for key, requirement in established.items():
        assessment_refs = {
            (ref.document_id, ref.location, ref.quote)
            for ref in assessments_by_requirement[key].tender_evidence
        }
        required_refs = {
            (ref.document_id, ref.location, ref.quote) for ref in requirement.tender_evidence
        }
        if not required_refs.issubset(assessment_refs):
            raise ValueError("assessment omitted independently established source evidence")
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


def _normalize_text(text: str) -> str:
    """Deterministic normalization for evidence quote matching.

    Handles Unicode normalization, whitespace collapsing, line-break normalization,
    trimming, and safe quotation-mark normalization. Does not alter semantic words.
    """
    import unicodedata

    if not isinstance(text, str):
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    # Normalize line breaks and tabs to spaces
    normalized = (
        normalized.replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    # Collapse repeated spaces
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    # Safe quotation-mark normalization: replace fancy quotes with ASCII equivalents
    normalized = (
        normalized.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return normalized.strip()


def _estimate_tokens(text: str) -> int:
    """Conservative, tokenizer-free estimate; deliberately not an exact token count.

    Three characters per token is used for ordinary text, with a floor for non-ASCII
    characters where byte-pair tokenizers often split more aggressively.
    """
    if not text:
        return 0
    general = math.ceil(len(text) / 3)
    non_ascii = sum(1 for char in text if ord(char) > 127)
    return max(general, non_ascii + math.ceil((len(text) - non_ascii) / 3))


def _messages_tokens(messages: list[LLMMessage]) -> int:
    return _estimate_tokens("\n".join(message.content for message in messages))


def _context_budget(profile: LLMProfile) -> dict[str, int]:
    """Calculate the input budget for a Stage B LLM call.

    Returns a dict with:
    - context_window_tokens: the profile's configured context window
    - reserved_output_tokens: tokens reserved for the model's output
    - safety_margin: additional safety margin for prompt overhead
    - input_budget: the maximum estimated input tokens allowed
    """
    context_window = profile.context_window_tokens
    reserved_output = profile.max_output_tokens or 1024
    safety_margin = 1400  # overhead for system prompt, schema, JSON wrapping
    input_budget = context_window - reserved_output - safety_margin
    return {
        "context_window_tokens": context_window,
        "reserved_output_tokens": reserved_output,
        "safety_margin": safety_margin,
        "input_budget": max(input_budget, 0),
    }


def _reduce_documents_for_budget(
    docs: list[dict[str, Any]],
    budget_tokens: int,
    tender_id: int,
    correlation_id: str,
    *,
    requirements=None,
    force_reduction: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reduce document content to fit within the input budget.

    Preserves documents in priority order, keeping metadata intact.
    Content chunks are trimmed or dropped based on available budget.
    Returns the reduced documents and a reduction report.
    """
    original_size = sum(
        len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) for doc in docs
    )
    original_tokens = _estimate_tokens(json.dumps(docs, ensure_ascii=False))

    if original_tokens <= budget_tokens and not force_reduction:
        return docs, {
            "reduction_applied": False,
            "original_tokens": original_tokens,
            "reduced_tokens": original_tokens,
            "budget_tokens": budget_tokens,
            "omitted_documents": 0,
            "omitted_chunks": 0,
        }

    identity_fields = {
        "document_id", "filename", "document_type", "language", "extraction_status",
        "processing_warning", "source_url", "checksum", "error_code",
    }
    reduced = [
        {
            **{key: value for key, value in doc.items() if key in identity_fields},
            "content": [],
            **({"summary": []} if "summary" in doc else {}),
        }
        for doc in docs
    ]
    # Keep identity/status records for every discovered document even when all of its
    # content is omitted. Content selection is relevance-ranked, then emitted in source order.
    metadata_tokens = _estimate_tokens(json.dumps(reduced, ensure_ascii=False))
    allowance = max(0, budget_tokens - metadata_tokens)
    req_text = " ".join(
        str(getattr(item, "requirement", item)) for item in (requirements or [])
    ).casefold()
    required_quotes = {
        (
            evidence.document_id,
            evidence.location,
            evidence.quote,
        )
        for item in (requirements or [])
        for evidence in getattr(item, "tender_evidence", [])
    }
    priority_terms = (
        "eligibility", "eligible", "qualification", "experience", "scope", "deliverable",
        "evaluation", "submission", "contract", "value", "price", "deadline", "date",
        "mandatory", "required", "technical", "financial", "criteria",
    )
    candidates = []
    for doc_index, doc in enumerate(docs):
        for channel in ("content", "summary"):
            for chunk_index, chunk in enumerate(doc.get(channel, [])):
                text = str(chunk.get("text", ""))
                lower = text.casefold()
                score = sum(4 for term in priority_terms if term in lower)
                if (doc.get("document_id"), chunk.get("location"), text) in required_quotes:
                    score += 100000
                if req_text:
                    words = {word for word in req_text.split() if len(word) > 4}
                    score += min(12, sum(1 for word in words if word in lower))
                size = _estimate_tokens(json.dumps(chunk, ensure_ascii=False))
                # Stable source order breaks ties; shorter chunks make the budget useful.
                candidates.append((
                    -(score / max(size, 1)), doc_index, channel, chunk_index, size, chunk
                ))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    selected_keys = set()
    selected_tokens = 0
    for _rank, doc_index, channel, chunk_index, size, _chunk in candidates:
        if selected_tokens + size <= allowance:
            selected_keys.add((doc_index, channel, chunk_index))
            selected_tokens += size
    omitted_sections = []
    omitted_chunks = 0
    omitted_documents = 0
    for doc_index, doc in enumerate(docs):
        kept_any = False
        omitted_for_doc = 0
        for channel in ("content", "summary"):
            kept = []
            for chunk_index, chunk in enumerate(doc.get(channel, [])):
                if (doc_index, channel, chunk_index) in selected_keys:
                    kept.append(chunk)
                    kept_any = True
                else:
                    omitted_chunks += 1
                    omitted_for_doc += 1
                    if len(omitted_sections) < 60:
                        omitted_sections.append({
                            "doc_id": doc.get("document_id"),
                            "section": chunk.get("location", "unknown"),
                        })
            if channel == "content" or channel in doc:
                reduced[doc_index][channel] = kept
        if omitted_for_doc and not kept_any:
            omitted_documents += 1

    reduced_size = sum(
        len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) for doc in reduced
    )
    reduced_tokens = _estimate_tokens(json.dumps(reduced, ensure_ascii=False))

    report = {
        "reduction_applied": True,
        "original_tokens": original_tokens,
        "reduced_tokens": reduced_tokens,
        "budget_tokens": budget_tokens,
        "original_size_bytes": original_size,
        "reduced_size_bytes": reduced_size,
        "omitted_documents": omitted_documents,
        "omitted_chunks": omitted_chunks,
        "omitted_sections": omitted_sections,
    }

    log.info(
        "Stage B context reduction applied",
        extra={
            "stage": "verdict",
            "status": "context_reduction",
            "tender_id": tender_id,
            "correlation_id": correlation_id,
            "extra": report,
        },
    )

    return reduced, report


def _business_days_until(now: datetime, target: datetime) -> int:
    """Count weekdays after today through the UTC deadline date; holidays are not configured."""
    if target < now:
        return -1
    days = (target.date() - now.date()).days
    return sum(
        1 for offset in range(1, days + 1) if (now.date() + timedelta(days=offset)).weekday() < 5
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


def _profile_request_config(profile: LLMProfile) -> dict[str, Any]:
    return {
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "max_tokens": profile.max_output_tokens,
        "enable_thinking": profile.enable_thinking,
        "reasoning_budget": profile.reasoning_budget,
    }


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
        "\n".join(part for part in (str(x.get("assessment", "")), str(x.get("gap") or "")) if part)
        for x in assessments
        if isinstance(x, dict)
    )
    gaps = [
        str(item.get("gap")) for item in assessments if isinstance(item, dict) and item.get("gap")
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

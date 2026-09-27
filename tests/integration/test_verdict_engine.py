"""Prompt 14 Stage B integration at the persisted model/storage boundary."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.interfaces.llm import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedSection,
    TenderDocumentBundle,
)
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.storage.local import LocalFileSystemStorage
from tender_intelligence.verdict.service import (
    DOCUMENT_MAP_CHUNK_CHARS,
    DocumentMapResult,
    OutcomeStatus,
    VerdictEngine,
    _bundle_documents,
    _document_map_validation_code,
    _safe_json_shape,
    format_verdict,
)


class _AssessmentClient(LLMClient):
    profile_name = "test-assessment"

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        supplied = json.loads(messages[1].content)
        if "Extract every material" in messages[0].content:
            content = json.dumps(_extracted_requirements(supplied))
        else:
            content = json.dumps(_valid_payload(supplied))
        return LLMResponse(content, self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001))


def _extracted_requirements(supplied):
    document = supplied["documents"][0]
    chunk = document["content"][0]
    return {
        "requirements": [
            {
                "requirement": "technical proposal",
                "tender_evidence": [
                    {
                        "document_id": document["document_id"],
                        "location": chunk["location"],
                        "quote": chunk["text"][:400],
                    }
                ],
            }
        ]
    }


def test_stage_b_persists_verdict_provenance_and_formats_without_sending(
    db_session, tmp_path
) -> None:
    source = Source(
        name="verdict-test-source", source_type="test", base_url="https://example.test", active=True
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id="v-test",
        url="https://example.test/v-test",
        title="Synthetic procurement",
        correlation_id=str(uuid.uuid4()),
        raw_metadata={},
    )
    db_session.add(tender)
    db_session.flush()
    db_session.add(
        TriageResult(
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            status="passed",
            mode="rules",
            reasons=["no_rule_disqualifier"],
        )
    )
    profile = LLMProfile(
        name="verdict-mock",
        base_url="https://mock.example/api",
        model="mock-v1",
        approved_for_company_docs=True,
        context_window_tokens=8000,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
    kb_text = "# Company capabilities\nSynthetic fixture only."
    kb_bytes = kb_text.encode()
    storage = LocalFileSystemStorage(tmp_path)
    storage.put("kb/test.md", kb_bytes, "text/markdown")
    db_session.add(
        KnowledgeBaseVersion(
            content_ref="kb/test.md",
            content_hash=hashlib.sha256(kb_bytes).hexdigest(),
            token_count=20,
            created_by="test",
        )
    )
    ExtractionStore(storage).write_bundle(_document_bundle(tender.id))
    db_session.flush()

    outcome = VerdictEngine(
        db_session, storage, lambda _: _AssessmentClient(), sleeper=lambda _: None
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.schema_version == "verdict.v1"
    assert persisted.prompt_version == "stage-b.v1"
    assert persisted.knowledge_base_version_id is not None
    assert persisted.map_reduce_used is False
    assert persisted.provider == "mock.example"
    assert db_session.query(LLMCall).filter_by(role="verdict", tender_id=tender.id).count() == 2
    content = format_verdict(persisted, outcome)
    assert content.verdict == "APPLY WITH CONDITIONS"
    assert content.assessment_unavailable is False
    assert "No evidence on file" in content.applicability


def test_unapproved_profile_never_invoked_with_kb(db_session, tmp_path) -> None:
    source = Source(
        name="policy-source", source_type="test", base_url="https://example.test", active=True
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id="policy",
        url="https://example.test/policy",
        title="Policy fixture",
        correlation_id=str(uuid.uuid4()),
        raw_metadata={},
    )
    db_session.add(tender)
    db_session.flush()
    db_session.add(
        TriageResult(
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            status="passed",
            mode="rules",
            reasons=[],
        )
    )
    profile = LLMProfile(
        name="unapproved-verdict",
        base_url="https://mock.example",
        model="mock",
        approved_for_company_docs=False,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
    calls = []
    outcome = VerdictEngine(
        db_session,
        LocalFileSystemStorage(tmp_path),
        lambda p: calls.append(p) or _AssessmentClient(),
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.APPROVAL
    assert calls == []
    assert db_session.query(LLMCall).filter_by(role="verdict").count() == 0


def test_payload_rejection_retries_requirement_extraction_with_document_map_reduce(
    db_session, tmp_path
) -> None:
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    first_bundle = _document_bundle(tender.id)
    first_document = first_bundle.documents[0]
    second_document = DocumentExtraction(
        document_id=tender.id + 3000,
        tender_id=tender.id,
        filename="annex.docx",
        source_url="https://source.test/annex.docx",
        extraction_status="extracted",
        extraction_method="docx",
        sections=[
            ExtractedSection(
                index=1,
                kind="paragraph",
                text="The supplier shall provide a delivery schedule.",
            )
        ],
    )
    ExtractionStore(storage).write_bundle(
        TenderDocumentBundle(
            tender_id=tender.id,
            documents=[first_document, second_document],
            incomplete_inputs=False,
        )
    )
    original_text = first_document.sections[0].text
    annex_text = second_document.sections[0].text
    script = [
        LLMError("payload too large", "provider_payload_too_large", http_status=413),
        json.dumps(
            {
                "summary": "Tender source requirement summary.",
                "evidence": [
                    {
                        "location": "section:1",
                        "quote": original_text,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "summary": "Annex source requirement summary.",
                "evidence": [
                    {
                        "location": "section:1",
                        "quote": annex_text,
                    }
                ],
            }
        ),
        "EXTRACT",
        "VALID",
    ]
    observed = []
    client = _ScriptedClient(script, observed, profile.id)

    outcome = VerdictEngine(
        db_session, storage, lambda _profile: client, sleeper=lambda _: None
    ).generate(tender.id)

    assert outcome.status is OutcomeStatus.AVAILABLE
    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None and persisted.map_reduce_used is True
    assert len(observed) == 5
    first_request_docs = json.loads(observed[0][1][1].content)["documents"]
    assert len(first_request_docs) == 2
    assert len(observed[-1][1][1].content.encode("utf-8")) <= 48_000
    assert all(
        len(json.loads(call[1][1].content).get("documents", [])) <= 1
        for call in observed[1:3]
    )


def test_document_map_preserves_unavailable_document_status(db_session, tmp_path) -> None:
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    available = _document_bundle(tender.id).documents[0]
    unavailable = DocumentExtraction(
        document_id=tender.id + 4000,
        tender_id=tender.id,
        filename="unavailable-annex.pdf",
        source_url="https://source.test/unavailable-annex.pdf",
        extraction_status="skipped",
        extraction_method="none",
        error_code="document_download_failed",
    )
    ExtractionStore(storage).write_bundle(
        TenderDocumentBundle(
            tender_id=tender.id,
            documents=[available, unavailable],
            incomplete_inputs=True,
        )
    )
    text = available.sections[0].text
    script = [
        LLMError("payload too large", "provider_payload_too_large", http_status=413),
        json.dumps({"summary": "Source requirement summary.", "evidence": [
            {"location": "section:1", "quote": text}
        ]}),
        "EXTRACT",
        "VALID",
    ]
    observed = []
    outcome = VerdictEngine(
        db_session,
        storage,
        lambda _profile: _ScriptedClient(script, observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    reduced_docs = json.loads(observed[3][1][1].content)["documents"]
    absent = next(item for item in reduced_docs if item["document_id"] == unavailable.document_id)
    assert absent["filename"] == "unavailable-annex.pdf"
    assert absent["extraction_status"] == "skipped"
    assert absent["error_code"] == "document_download_failed"
    assert absent["content"] == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not-json", "document_map_malformed_json"),
        ('{"summary": "missing evidence"}', "document_map_missing_field"),
        ('{"summary": "ok", "evidence": "wrong type"}', "document_map_invalid_type"),
        (
            '{"summary":"ok","evidence":[{"location":"page:99","quote":"invented"}]}',
            "document_map_invalid_reference",
        ),
    ],
)
def test_document_map_validation_reports_specific_failure(
    db_session, tmp_path, content, expected
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    engine = VerdictEngine(
        db_session,
        storage,
        lambda _: _ScriptedClient([content], [], profile.id),
        sleeper=lambda _: None,
    )
    _mapped, error = engine._map_documents(tender, profile, docs, "", None, {}, False)
    assert error == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [("null", "document_map_invalid_type"),
     ('{"summary": "missing evidence"}', "document_map_missing_field")],
)
def test_invalid_document_map_never_reaches_reference_validation(
    db_session, tmp_path, caplog, content, expected
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    engine = VerdictEngine(
        db_session, storage,
        lambda _: _ScriptedClient([content], [], profile.id), sleeper=lambda _: None,
    )

    _mapped, error = engine._map_documents(tender, profile, docs, "", None, {}, False)

    assert error == expected
    failures = [record for record in caplog.records
                if getattr(record, "error_code", None) == expected]
    assert failures
    assert failures[0].extra["schema_validation_succeeded"] is False
    assert failures[0].extra["reference_details"] is None
    if content == "null":
        assert failures[0].extra["parsed_response_shape"] == {"kind": "null"}
    assert not any(
        getattr(record, "error_code", None) == "document_map_invalid_reference"
        for record in caplog.records
    )


def test_invalid_reference_diagnostic_identifies_supplied_segment_without_quote(
    db_session, tmp_path, caplog
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    supplied = docs[0]["content"][0]
    untrusted_quote = "fictional quote must not appear in logs"
    result = json.dumps({
        "summary": "Synthetic fixture summary.",
        "evidence": [{"location": supplied["location"], "quote": untrusted_quote}],
    })
    engine = VerdictEngine(
        db_session, storage,
        lambda _: _ScriptedClient([result], [], profile.id), sleeper=lambda _: None,
    )

    _mapped, error = engine._map_documents(tender, profile, docs, "", None, {}, False)

    assert error == "document_map_invalid_reference"
    record = next(r for r in caplog.records
                  if getattr(r, "error_code", None) == "document_map_invalid_reference")
    details = record.extra["reference_details"]
    assert record.extra["schema_validation_succeeded"] is True
    assert details["failure_category"] == "quote_not_found_in_supplied_segment"
    assert details["document_id"] == docs[0]["document_id"]
    assert details["supplied_evidence"] == [{
        "document_id": docs[0]["document_id"],
        "location": supplied["location"],
        "text_length": len(supplied["text"]),
    }]
    assert untrusted_quote not in caplog.text
    assert record.extra["parsed_response_shape"]["kind"] == "object"


def test_document_map_cannot_reference_original_evidence_omitted_from_reduced_input(
    db_session, tmp_path, caplog
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    # This is the reduced document representation: only A is sent to the map call.
    original_evidence_b = {"location": "page-B", "text": "Evidence B was omitted."}
    docs[0]["content"] = [{"location": "page-A", "text": "Evidence A was supplied."}]
    result = json.dumps({
        "summary": "Synthetic fixture summary.",
        "evidence": [{
            "location": original_evidence_b["location"],
            "quote": original_evidence_b["text"],
        }],
    })
    requests = []

    class CaptureClient(LLMClient):
        profile_name = "omitted-evidence"

        def chat(self, messages, *, max_tokens=None):
            requests.append(json.loads(messages[1].content))
            return LLMResponse(result, self.profile_name, "mock-v1")

    _mapped, error = VerdictEngine(
        db_session, storage, lambda _: CaptureClient(), sleeper=lambda _: None
    )._map_documents(tender, profile, docs, "", None, {}, False)

    assert error == "document_map_invalid_reference"
    assert requests[0]["content"] == [{"location": "page-A", "text": "Evidence A was supplied."}]
    record = next(r for r in caplog.records
                  if getattr(r, "error_code", None) == "document_map_invalid_reference")
    assert record.extra["reference_details"]["supplied_evidence"] == [{
        "document_id": docs[0]["document_id"], "location": "page-A",
    }]
    assert record.extra["reference_details"]["reference_location"] == "page-B"
    assert all(
        item["location"] != "page-B"
        for item in record.extra["reference_details"]["supplied_evidence"]
    )


def test_document_map_prompt_uses_canonical_schema_and_valid_response(
    db_session, tmp_path
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))

    class SchemaClient(LLMClient):
        profile_name = "schema-contract"

        def __init__(self):
            self.system_prompts = []

        def chat(self, messages, *, max_tokens=None):
            self.system_prompts.append(messages[0].content)
            supplied = json.loads(messages[1].content)
            segment = supplied["content"][0]
            return LLMResponse(
                json.dumps({
                    "summary": "Synthetic fixture summary.",
                    "evidence": [{
                        "location": segment["location"],
                        "quote": segment["text"][:80],
                    }],
                }),
                self.profile_name,
                "mock-v1",
            )

    client = SchemaClient()
    reduced, error = VerdictEngine(
        db_session, storage, lambda _: client, sleeper=lambda _: None
    )._map_documents(tender, profile, docs, "", None, {}, False)

    assert error is None
    assert reduced
    assert len(client.system_prompts) == 1
    assert json.dumps(DocumentMapResult.model_json_schema(), separators=(",", ":")) \
        in client.system_prompts[0]


def test_document_map_unexpected_nesting_has_precise_safe_diagnostic(
    db_session, tmp_path, caplog
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    # Synthetic representative shape only. The actual live completion was not retained.
    nested = json.dumps({"result": {"summary": "private text", "evidence": []}})
    engine = VerdictEngine(
        db_session, storage,
        lambda _: _ScriptedClient([nested], [], profile.id), sleeper=lambda _: None,
    )

    _mapped, error = engine._map_documents(tender, profile, docs, "", None, {}, False)

    assert error == "document_map_unexpected_nesting"
    diagnostics = [record.extra for record in caplog.records if hasattr(record, "extra")]
    assert diagnostics
    issues = diagnostics[0]["validation_issues"]
    assert {tuple(issue["field_path"]) for issue in issues} == {
        ("summary",), ("evidence",), ("result",),
    }
    assert diagnostics[0]["parsed_response_shape"]["fields"]["result"]["kind"] == "object"
    assert "private text" not in caplog.text
    assert _document_map_validation_code([
        {"type": "string_too_short", "loc": ("summary",), "input": ""}
    ]) == "document_map_constraint_violation"
    assert _document_map_validation_code([], {"result": {"summary": "x", "evidence": []}}) \
        == "document_map_unexpected_nesting"
    assert _safe_json_shape({"summary": "private text"}) == {
        "kind": "object",
        "fields": {"summary": {"kind": "string", "length": 12}},
        "field_count": 1,
    }


def test_large_document_map_requests_are_chunk_bounded_and_keep_locations(
    db_session, tmp_path
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    docs = _bundle_documents(_document_bundle(tender.id))
    full_text = ("The supplier shall submit a technical proposal. " * 1000).strip()
    docs[0]["content"] = [{"location": "page:12", "text": full_text}]

    class ChunkMapClient(LLMClient):
        profile_name = "chunk-map"

        def __init__(self):
            self.requests = []

        def chat(self, messages, *, max_tokens=None):
            supplied = json.loads(messages[1].content)
            self.requests.append(supplied)
            segment = supplied["content"][0]
            return LLMResponse(
                json.dumps({
                    "summary": "Synthetic fixture section summary.",
                    "evidence": [{
                        "location": segment["location"],
                        "quote": segment["text"][:100],
                    }],
                }),
                self.profile_name,
                "mock-v1",
            )

    client = ChunkMapClient()
    engine = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None)
    reduced, error = engine._map_documents(tender, profile, docs, "", None, {}, False)
    assert error is None
    assert len(client.requests) > 1
    assert all(
        len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        <= DOCUMENT_MAP_CHUNK_CHARS + 1500
        for request in client.requests
    )
    assert sum(len(item["content"][0]["text"]) for item in client.requests) == len(full_text)
    assert all(item["content"][0]["location"] == "page:12" for item in client.requests)
    assert reduced[0]["mapped_segment_count"] == len(client.requests)


# ── Normalization regression tests (Prompt 16C FIX #1) ─────────────────────


def test_document_map_accepts_whitespace_normalized_quote(db_session, tmp_path) -> None:
    """LLM returns quote with different whitespace than source — should pass."""
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    first_doc = _document_bundle(tender.id).documents[0]
    # Source text has single spaces; LLM returns collapsed/extra spaces
    source_text = first_doc.sections[0].text[:400]
    normalized_quote = " ".join(source_text.split())  # collapse whitespace
    assert normalized_quote != source_text or "  " not in source_text  # ensure test is meaningful

    script = [
        LLMError("payload too large", "provider_payload_too_large", http_status=413),
        json.dumps({
            "summary": "Summary.",
            "evidence": [{
                "location": "section:1",
                "quote": normalized_quote,
            }],
        }),
        "EXTRACT",
        "VALID",
    ]
    observed = []
    outcome = VerdictEngine(
        db_session, storage, lambda _profile: _ScriptedClient(script, observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE


def test_document_map_accepts_unicode_normalized_quote(db_session, tmp_path) -> None:
    """LLM returns quote with Unicode punctuation differences — should pass."""
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    first_doc = _document_bundle(tender.id).documents[0]
    source_text = first_doc.sections[0].text[:400]
    # NFKC-equivalent full-width character.
    unicode_quote = source_text.replace("a", "ａ", 1)

    script = [
        json.dumps({
            "summary": "Summary.",
            "evidence": [{
                "location": "section:1",
                "quote": unicode_quote,
            }],
        }),
    ]
    engine = VerdictEngine(
        db_session,
        storage,
        lambda _profile: _ScriptedClient(script, [], profile.id),
        sleeper=lambda _: None,
    )
    mapped, error = engine._map_documents(
        tender, profile, _bundle_documents(_document_bundle(tender.id)), "", None, {}, False
    )
    assert error is None
    assert mapped[0]["content"][0]["text"] == unicode_quote


def test_document_map_rejects_fabricated_quote(db_session, tmp_path) -> None:
    """LLM returns quote that does not exist in source — should fail."""
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    first_doc = _document_bundle(tender.id).documents[0]

    script = [
        LLMError("payload too large", "provider_payload_too_large", http_status=413),
        json.dumps({
            "summary": "Summary.",
            "evidence": [{
                "document_id": first_doc.document_id,
                "location": "section:1",
                "quote": "This text does not exist anywhere in the source document.",
            }],
        }),
        "EXTRACT",
        "VALID",
    ]
    observed = []
    outcome = VerdictEngine(
        db_session, storage, lambda _profile: _ScriptedClient(script, observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.FAILED


def test_document_map_rejects_unknown_document(db_session, tmp_path) -> None:
    """LLM returns evidence for a document not in the map — should fail."""
    tender, profile, _fallback, storage, _kb = _environment(db_session, tmp_path)
    first_doc = _document_bundle(tender.id).documents[0]

    script = [
        LLMError("payload too large", "provider_payload_too_large", http_status=413),
        json.dumps({
            "summary": "Summary.",
            "evidence": [{
                "document_id": 999999,
                "location": "section:1",
                "quote": first_doc.sections[0].text,
            }],
        }),
        "EXTRACT",
        "VALID",
    ]
    observed = []
    outcome = VerdictEngine(
        db_session, storage, lambda _profile: _ScriptedClient(script, observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.FAILED


class _ScriptedClient(LLMClient):
    profile_name = "scripted"

    def __init__(self, script, observed, profile_id):
        self.script = script
        self.observed = observed
        self.profile_id = profile_id

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.observed.append((self.profile_id, messages, max_tokens))
        next_result = self.script.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        if next_result in {"VALID", "EXTRACT"}:
            supplied = json.loads(messages[1].content)
            if "Extract every material" in messages[0].content or next_result == "EXTRACT":
                next_result = json.dumps(_extracted_requirements(supplied))
            else:
                next_result = json.dumps(_valid_payload(supplied))
        return LLMResponse(
            next_result, self.profile_name, f"actual-model-{self.profile_id}", LLMUsage(3, 4, 0.01)
        )


def _valid_payload(supplied):
    deadline = supplied["deadline"]
    requirements = supplied["material_requirements"]
    limitations = (
        ["One or more tender inputs were unavailable."] if supplied["incomplete_inputs"] else []
    )
    return {
        "schema_version": "verdict.v1",
        "background": "Synthetic assessment.",
        "requirements": [item["requirement"] for item in requirements],
        "deadline_status": deadline["status"],
        "deadline_utc": deadline["deadline_utc"],
        "deadline_date": deadline["date"],
        "deadline_time": deadline["time"],
        "deadline_timezone": deadline["timezone"],
        "source_timezone": deadline["source_timezone"],
        "assessments": [
            {
                "requirement": item["requirement"],
                "tender_evidence": item["tender_evidence"],
                "company_evidence": [],
                "status": "unverified",
                "assessment": f"No evidence on file for {item['requirement']}.",
                "gap": f"No evidence on file for {item['requirement']}.",
            }
            for item in requirements
        ],
        "gaps": ["No evidence on file for technical proposal."],
        "verdict": "APPLY WITH CONDITIONS",
        "confidence": 0.4,
        "urgency": False,
        "incomplete_inputs": supplied["incomplete_inputs"],
        "limitations": limitations,
    }


def _environment(
    db_session,
    tmp_path,
    *,
    triage_status="passed",
    approved=True,
    fallback_approved=None,
    bundle_incomplete=False,
):
    source = Source(
        name=f"source-{uuid.uuid4().hex}",
        source_type="test",
        base_url="https://source.test",
        active=True,
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id=uuid.uuid4().hex,
        url="https://source.test/tender",
        title="Synthetic tender",
        correlation_id=str(uuid.uuid4()),
        raw_metadata={},
    )
    db_session.add(tender)
    db_session.flush()
    db_session.add(
        TriageResult(
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            status=triage_status,
            mode="rules",
            reasons=[],
        )
    )
    primary = LLMProfile(
        name=f"primary-{uuid.uuid4().hex}",
        base_url="https://primary.test/api",
        model="configured-primary",
        approved_for_company_docs=approved,
        context_window_tokens=8000,
    )
    db_session.add(primary)
    db_session.flush()
    fallback = None
    if fallback_approved is not None:
        fallback = LLMProfile(
            name=f"fallback-{uuid.uuid4().hex}",
            base_url="https://fallback.test/api",
            model="configured-fallback",
            approved_for_company_docs=fallback_approved,
            context_window_tokens=8000,
        )
        db_session.add(fallback)
        db_session.flush()
    db_session.add(
        LLMRoleAssignment(
            role="verdict",
            profile_id=primary.id,
            fallback_profile_id=fallback.id if fallback else None,
        )
    )
    kb = f"# Synthetic section\nKB-{uuid.uuid4().hex} capability record."
    raw = kb.encode()
    storage = LocalFileSystemStorage(tmp_path)
    storage.put("kb/current.md", raw, "text/markdown")
    db_session.add(
        KnowledgeBaseVersion(
            content_ref="kb/current.md",
            content_hash=hashlib.sha256(raw).hexdigest(),
            token_count=20,
            created_by="test",
        )
    )
    ExtractionStore(storage).write_bundle(
        _document_bundle(tender.id, incomplete_inputs=bundle_incomplete)
    )
    db_session.flush()
    return tender, primary, fallback, storage, kb


def _document_bundle(tender_id: int, *, incomplete_inputs: bool = False):
    text = (
        "The tenderer shall submit a technical proposal. "
        "The tenderer shall provide three references. "
        "The bidder shall include audited accounts."
    )
    document = DocumentExtraction(
        document_id=tender_id + 1000,
        tender_id=tender_id,
        filename="tender.docx",
        source_url="https://source.test/tender.docx",
        extraction_status="extracted",
        extraction_method="docx",
        sections=[ExtractedSection(index=1, kind="paragraph", text=text)],
    )
    documents = [document]
    if incomplete_inputs:
        documents.append(
            DocumentExtraction(
                document_id=tender_id + 2000,
                tender_id=tender_id,
                filename="unavailable-annex.pdf",
                source_url="https://source.test/unavailable-annex.pdf",
                extraction_status="failed",
                extraction_method="none",
                error_code="document_download_failed",
            )
        )
    return TenderDocumentBundle(
        tender_id=tender_id,
        documents=documents,
        incomplete_inputs=incomplete_inputs,
    )


def _three_requirements(supplied):
    document = supplied["documents"][0]
    chunk = document["content"][0]
    phrases = (
        "submit a technical proposal",
        "provide three references",
        "include audited accounts",
    )
    return {
        "requirements": [
            {
                "requirement": phrase,
                "tender_evidence": [
                    {
                        "document_id": document["document_id"],
                        "location": chunk["location"],
                        "quote": phrase,
                    }
                ],
            }
            for phrase in phrases
        ]
    }


class _CompletenessClient(LLMClient):
    profile_name = "completeness-test"

    def __init__(self, mode="complete"):
        self.mode = mode
        self.calls = 0

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.calls += 1
        supplied = json.loads(messages[1].content)
        if "Extract every material" in messages[0].content:
            content = json.dumps(_three_requirements(supplied))
        else:
            payload = _valid_payload(supplied)
            if self.mode == "missing_assessment":
                payload["assessments"] = payload["assessments"][:-1]
            elif self.mode == "orphan_assessment":
                payload["assessments"].append(
                    {
                        **payload["assessments"][0],
                        "requirement": "R99",
                    }
                )
            elif self.mode == "model_omission":
                payload["requirements"] = payload["requirements"][:-1]
                payload["assessments"] = payload["assessments"][:-1]
            content = json.dumps(payload)
        return LLMResponse(content, self.profile_name, "mock-complete-v1", LLMUsage(4, 5, 0.01))


def test_triage_discard_and_failure_cannot_enter_stage_b(db_session, tmp_path) -> None:
    for state in ("triage_discarded", "triage_failed"):
        tender, _, _, storage, _ = _environment(db_session, tmp_path, triage_status=state)
        invoked = []
        outcome = VerdictEngine(
            db_session,
            storage,
            lambda profile, invoked=invoked: invoked.append(profile)
            or _AssessmentClient(),
        ).generate(tender.id)
        assert outcome.error_code == "triage_not_passed"
        assert invoked == []
        assert db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").count() == 0
        db_session.rollback()


def test_validation_retry_is_exactly_once_and_records_both_actual_calls(
    db_session, tmp_path
) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    observed = []
    script = ["EXTRACT", "not-json", "not-json"]
    def client():
        return _ScriptedClient(script, observed, profile.id)
    outcome = VerdictEngine(
        db_session, storage, lambda _: client(), sleeper=lambda _: None
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "verdict_invalid_output"
    assert len(observed) == 3
    rows = db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").all()
    assert len(rows) == 3 and all(row.status == "success" for row in rows)
    assert db_session.query(Verdict).filter_by(tender_id=tender.id).count() == 0


def test_transport_retries_are_bounded_then_fallback_and_record_actual_model(
    db_session, tmp_path
) -> None:
    tender, primary, fallback, storage, _ = _environment(
        db_session, tmp_path, approved=True, fallback_approved=True
    )
    observed = []
    scripts = {primary.id: [LLMError("timeout", "timeout")] * 3, fallback.id: ["EXTRACT", "VALID"]}

    def factory(profile):
        return _ScriptedClient(scripts[profile.id], observed, profile.id)

    outcome = VerdictEngine(db_session, storage, factory, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert [call[0] for call in observed] == [primary.id] * 3 + [fallback.id] * 2
    rows = (
        db_session.query(LLMCall)
        .filter_by(tender_id=tender.id, role="verdict")
        .order_by(LLMCall.id)
        .all()
    )
    assert len(rows) == 5
    assert [row.status for row in rows] == ["failed", "failed", "failed", "success", "success"]
    verdict = db_session.get(Verdict, outcome.verdict_id)
    assert verdict is not None and verdict.llm_profile_id == fallback.id
    assert verdict.model == f"actual-model-{fallback.id}"
    assert rows[-1].model == f"actual-model-{fallback.id}"


def test_unapproved_primary_routes_only_to_approved_fallback(db_session, tmp_path) -> None:
    tender, primary, fallback, storage, kb = _environment(
        db_session, tmp_path, approved=False, fallback_approved=True
    )
    observed = []
    outcome = VerdictEngine(
        db_session,
        storage,
        lambda profile: _ScriptedClient(["VALID"], observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert [call[0] for call in observed] == [fallback.id, fallback.id]
    sent = json.loads(observed[-1][1][1].content)
    assert sent["knowledge_base"] == ""
    assert sent["knowledge_base_version_id"] is not None
    assert (
        db_session.query(LLMCall).filter_by(tender_id=tender.id, profile_id=primary.id).count() == 0
    )


def test_budget_gate_prevents_model_call_and_preserves_waiting_status(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    settings = db_session.get(Setting, 1)
    if settings is None:
        settings = Setting(id=1)
        db_session.add(settings)
    settings.monthly_ai_budget = 0.01
    db_session.add(
        LLMCall(
            correlation_id=tender.correlation_id,
            role="triage",
            profile_id=profile.id,
            est_cost=0.01,
            status="success",
        )
    )
    db_session.flush()
    invoked = []
    outcome = VerdictEngine(
        db_session, storage, lambda candidate: invoked.append(candidate), sleeper=lambda _: None
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.BUDGET
    assert tender.status == "awaiting_budget"
    assert invoked == []
    assert db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").count() == 0


def test_incomplete_bundle_flag_is_forwarded_and_persisted(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path, bundle_incomplete=True)
    observed = []
    outcome = VerdictEngine(
        db_session,
        storage,
        lambda _: _ScriptedClient(["VALID"], observed, profile.id),
        sleeper=lambda _: None,
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert outcome.incomplete_inputs is True
    sent = json.loads(observed[-1][1][1].content)
    assert sent["incomplete_inputs"] is True
    assert db_session.get(Verdict, outcome.verdict_id).incomplete_inputs is True


def test_independently_extracted_requirements_all_assessed(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    client = _CompletenessClient()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.AVAILABLE
    verdict = db_session.get(Verdict, outcome.verdict_id)
    assert verdict is not None
    assert {item["requirement"] for item in verdict.requirements_summary} == {
        "submit a technical proposal",
        "provide three references",
        "include audited accounts",
    }
    assert client.calls == 2


@pytest.mark.parametrize("mode", ["missing_assessment", "orphan_assessment", "model_omission"])
def test_incomplete_or_orphaned_requirement_assessments_are_rejected(
    db_session, tmp_path, mode
) -> None:
    tender, _, _, storage, _ = _environment(db_session, tmp_path)
    client = _CompletenessClient(mode)
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "verdict_invalid_output"
    assert client.calls == 3  # extraction + assessment + exactly one validation retry
    assert db_session.query(Verdict).filter_by(tender_id=tender.id).count() == 0


# ---------------------------------------------------------------------------
# Stage B Context Budget Management Tests (Prompt 16C)
# ---------------------------------------------------------------------------


class _ContextTestClient(LLMClient):
    """A test client that tracks calls and can simulate payload-too-large."""

    profile_name = "context-test"

    def __init__(self, fail_verdict_with_413=False):
        self.calls = 0
        self.request_bodies = []
        self._fail_verdict_with_413 = fail_verdict_with_413
        self._verdict_call_count = 0

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.calls += 1
        supplied = json.loads(messages[1].content)
        self.request_bodies.append(supplied)
        if "Summarize material eligibility" in messages[0].content:
            segment = supplied["content"][0]
            content = json.dumps({
                "summary": segment["text"][:200],
                "evidence": [{
                    "location": segment["location"],
                    "quote": segment["text"][:min(200, len(segment["text"]))],
                }],
            })
        elif "Extract every material" in messages[0].content:
            content = json.dumps(_extracted_requirements(supplied))
        else:
            self._verdict_call_count += 1
            if self._fail_verdict_with_413 and self._verdict_call_count == 1:
                raise LLMError(
                    "Payload too large",
                    "provider_payload_too_large",
                    http_status=413,
                )
            content = json.dumps(_valid_payload(supplied))
        return LLMResponse(content, self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001))


def _large_document_bundle(
    tender_id: int, num_docs: int = 5, chunks_per_doc: int = 10
) -> TenderDocumentBundle:
    """Create a bundle with many documents and chunks to exceed context budget."""
    docs = []
    for i in range(num_docs):
        sections = []
        for j in range(chunks_per_doc):
            sections.append(
                ExtractedSection(
                    index=j,
                    text="A" * 2000,  # 2000 chars per chunk
                    kind="paragraph",
                )
            )
        docs.append(
            DocumentExtraction(
                document_id=tender_id * 100 + i,
                tender_id=tender_id,
                filename=f"doc{i}.pdf",
                source_url=f"https://example.com/doc{i}.pdf",
                mime_type="application/pdf",
                language="en",
                extraction_status="ok",
                extraction_method="test",
                pages=[],
                sections=sections,
            )
        )
    return TenderDocumentBundle(tender_id=tender_id, documents=docs)


def test_small_bundle_no_reduction(db_session, tmp_path) -> None:
    """Test A: Small bundle within budget — no reduction, one normal LLM request."""
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    client = _ContextTestClient()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.AVAILABLE
    # Normal flow: extraction + verdict = 2 calls
    assert client.calls == 2


def test_oversized_bundle_reduction(db_session, tmp_path, caplog) -> None:
    """Test B: Oversized bundle — context reduction occurs, reduced request is sent."""
    caplog.set_level("INFO", logger="tender_intelligence.verdict")
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    # Replace the bundle with a large one (but not so large that reduction leaves nothing)
    large_bundle = _large_document_bundle(tender.id, num_docs=3, chunks_per_doc=10)
    storage.put(
        f"tenders/{tender.id}/extracted/bundle.json",
        json.dumps(large_bundle.to_dict(), default=str).encode("utf-8"),
    )
    client = _ContextTestClient()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert client.calls > 2  # map calls + extraction + final verdict
    assert db_session.query(Verdict).filter_by(tender_id=tender.id).count() == 1
    final_request = client.request_bodies[-1]
    assert final_request["context_manifest"]["omitted_sections"]
    measured = [
        record.extra for record in caplog.records if record.status == "context_measured"
    ][-1]
    assert measured["reduction_applied"] is True
    assert measured["final_request_tokens"] <= measured["input_budget_tokens"]
    assert measured["original_context_size"] > measured["reduced_context_size"]


def test_irreducible_final_prompt_fails_without_verdict_call(db_session, tmp_path, monkeypatch):
    import tender_intelligence.verdict.service as verdict_service

    tender, _profile, _, storage, _ = _environment(db_session, tmp_path)
    actual_budget = verdict_service._context_budget
    monkeypatch.setattr(
        verdict_service,
        "_context_budget",
        lambda profile: {**actual_budget(profile), "input_budget": 0},
    )
    client = _ContextTestClient()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "reduced_context_exceeds_window"
    assert client.calls == 0  # irreducible requirements prompt is rejected before the provider
    assert db_session.query(Verdict).filter_by(tender_id=tender.id).count() == 0


def test_payload_too_large_recovery(db_session, tmp_path) -> None:
    """Test C: Provider returns provider_payload_too_large — context is reduced and retried."""
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    client = _ContextTestClient(fail_verdict_with_413=True)
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    # Should succeed after reduction and retry
    assert outcome.status is OutcomeStatus.AVAILABLE
    # extraction + verdict(413) + verdict(reduced) = 3 calls
    assert client.calls == 3
    assert client.request_bodies[-1] != client.request_bodies[-2]
    assert len(json.dumps(client.request_bodies[-1], ensure_ascii=False)) < len(
        json.dumps(client.request_bodies[-2], ensure_ascii=False)
    )


def test_second_payload_rejection_fails_after_one_smaller_retry(db_session, tmp_path) -> None:
    tender, _, _, storage, _ = _environment(db_session, tmp_path)

    class _TwoVerdictRejections(_ContextTestClient):
        verdict_bodies = []

        def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
            supplied = json.loads(messages[1].content)
            if "Extract every material" not in messages[0].content:
                self._verdict_call_count += 1
                self.calls += 1
                self.request_bodies.append(supplied)
                self.verdict_bodies.append(supplied)
                raise LLMError("Too large", "provider_payload_too_large", http_status=413)
            return super().chat(messages, max_tokens=max_tokens)

    client = _TwoVerdictRejections()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "provider_payload_too_large"
    assert client._verdict_call_count == 2
    assert len(client.verdict_bodies) == 2
    assert client.verdict_bodies[0] != client.verdict_bodies[1]
    assert len(json.dumps(client.verdict_bodies[1], ensure_ascii=False)) < len(
        json.dumps(client.verdict_bodies[0], ensure_ascii=False)
    )


def test_deterministic_error_no_retry(db_session, tmp_path) -> None:
    """Test D: Deterministic errors are not retried with the same payload."""
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)

    class _Always413Client(LLMClient):
        profile_name = "always-413"
        calls = 0

        def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
            self.calls += 1
            raise LLMError("Too large", "provider_payload_too_large", http_status=413)

    client = _Always413Client()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "provider_payload_too_large"
    # Should NOT retry the same payload multiple times
    # extraction(413) + verdict(413) = 2 calls, no retry loop
    assert client.calls == 2


def test_transient_error_still_retries(db_session, tmp_path) -> None:
    """Test E: Transient timeout continues to use existing retry behavior."""
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)

    class _TimeoutThenSuccessClient(LLMClient):
        profile_name = "timeout-client"
        calls = 0

        def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
            self.calls += 1
            if "Extract every material" in messages[0].content:
                if self.calls == 1:
                    raise LLMError("Timeout", "timeout_before_http_response")
                content = json.dumps(_extracted_requirements(json.loads(messages[1].content)))
            else:
                content = json.dumps(_valid_payload(json.loads(messages[1].content)))
            return LLMResponse(content, self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001))

    client = _TimeoutThenSuccessClient()
    outcome = VerdictEngine(db_session, storage, lambda _: client, sleeper=lambda _: None).generate(
        tender.id
    )
    assert outcome.status is OutcomeStatus.AVAILABLE
    # extraction(timeout) + extraction(retry) + verdict = 3 calls
    assert client.calls == 3


def test_context_budget_calculation() -> None:
    """Test F: Context budget is calculated from profile configuration."""
    from tender_intelligence.verdict.service import _context_budget

    profile = LLMProfile(
        name="Test",
        base_url="https://example.com/v1",
        model="test-model",
        context_window_tokens=8000,
        max_output_tokens=1024,
    )
    budget = _context_budget(profile)
    assert budget["context_window_tokens"] == 8000
    assert budget["reserved_output_tokens"] == 1024
    assert budget["safety_margin"] == 1400
    assert budget["input_budget"] == 8000 - 1024 - 1400


def test_reduce_documents_preserves_metadata() -> None:
    """Test G: Document reduction preserves metadata and high-priority content."""
    from tender_intelligence.verdict.service import _reduce_documents_for_budget

    docs = [
        {
            "document_id": 1,
            "filename": "test.pdf",
            "document_type": "application/pdf",
            "language": "en",
            "extraction_status": "ok",
            "extraction_method": "test",
            "processing_warning": None,
            "source_url": "https://example.com",
            "checksum": "abc123",
            "mime_type": "application/pdf",
            "error_code": None,
            "content": [
                {"location": "page-1", "text": "A" * 1000},
                {"location": "page-2", "text": "B" * 1000},
                {"location": "page-3", "text": "C" * 1000},
            ],
        }
    ]
    # Budget that fits only 1-2 chunks (metadata also consumes tokens)
    reduced, report = _reduce_documents_for_budget(docs, 500, 1, "test-correlation")
    assert report["reduction_applied"] is True
    assert report["omitted_chunks"] >= 1
    # Metadata is preserved
    assert reduced[0]["document_id"] == 1
    assert reduced[0]["filename"] == "test.pdf"
    assert reduced[0]["checksum"] == "abc123"
    assert report["omitted_sections"]


def test_reducer_prioritizes_independently_extracted_requirement_quote() -> None:
    from types import SimpleNamespace

    from tender_intelligence.verdict.service import _reduce_documents_for_budget

    docs = [{
        "document_id": 17, "filename": "fictional-tender.pdf", "extraction_status": "extracted",
        "content": [
            {"location": "page-1", "text": "background " + "x" * 1800},
            {"location": "page-8", "text": "Eligibility: supplier must provide three references."},
        ],
    }]
    requirement = SimpleNamespace(
        requirement="supplier references",
        tender_evidence=[SimpleNamespace(
            document_id=17, location="page-8",
            quote="Eligibility: supplier must provide three references.",
        )],
    )
    reduced, report = _reduce_documents_for_budget(
        docs, 100, 17, "fictional-correlation", requirements=[requirement]
    )
    assert reduced[0]["content"] == [docs[0]["content"][1]]
    assert report["omitted_sections"][0]["section"] == "page-1"


def test_token_estimator_is_unicode_conservative() -> None:
    from tender_intelligence.verdict.service import _estimate_tokens

    assert _estimate_tokens("é" * 100) >= 100
    assert _estimate_tokens("a" * 100) >= 34


def test_no_provider_name_branching() -> None:
    """Test H: Context management does not branch on provider name or model name."""
    from tender_intelligence.verdict.service import _context_budget, _reduce_documents_for_budget

    # Same budget calculation regardless of provider
    for provider_name, model in [
        ("Groq", "openai/gpt-oss-20b"),
        ("OpenAI", "gpt-4o"),
        ("DeepSeek", "deepseek-chat"),
        ("SomeOtherProvider", "some-model"),
    ]:
        profile = LLMProfile(
            name=provider_name,
            base_url="https://example.com/v1",
            model=model,
            context_window_tokens=8000,
            max_output_tokens=1024,
        )
        budget = _context_budget(profile)
        assert budget["input_budget"] == 8000 - 1024 - 1400

    # Same reduction logic regardless of provider
    docs = [
        {
            "document_id": 1,
            "filename": "test.pdf",
            "document_type": "application/pdf",
            "language": "en",
            "extraction_status": "ok",
            "extraction_method": "test",
            "processing_warning": None,
            "source_url": "https://example.com",
            "checksum": "abc123",
            "mime_type": "application/pdf",
            "error_code": None,
            "content": [
                {"location": "page-1", "text": "A" * 1000},
                {"location": "page-2", "text": "B" * 1000},
                {"location": "page-3", "text": "C" * 1000},
            ],
        }
    ]
    reduced, report = _reduce_documents_for_budget(docs, 500, 1, "test-correlation")
    assert report["reduction_applied"] is True

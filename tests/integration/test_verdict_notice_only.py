"""Stage B's behaviour when the tender document was never downloadable.

The tender is assessed anyway, on the notice the source did publish — and the verdict says so:
reduced confidence, ``incomplete_inputs`` set, and a limitation the reader cannot miss. The
case that motivates this is TenderDetail, whose detail pages carry a real description while
keeping the tender document behind a subscription; those tenders were being reported as
``requirement_sources_unavailable`` when the system had enough to say something.

The LLM is a deterministic stub in every test: the point is what the engine *does* with the
assessment — the penalty, the limitation, the completeness flag — not what a model would say.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.llm import LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.interfaces.llm import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
from tender_intelligence.processing.notice import (
    NOTICE_DOCUMENT_ID,
    NOTICE_FILENAME,
    NOTICE_METHOD,
    NOTICE_SOURCE_KIND,
    NoticeSource,
    notice_extraction,
)
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedSection,
    TenderDocumentBundle,
)
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.storage.local import LocalFileSystemStorage
from tender_intelligence.verdict.service import (
    NOTICE_ONLY_CONFIDENCE_PENALTY,
    NOTICE_ONLY_LIMITATION,
    OutcomeStatus,
    VerdictEngine,
    VerdictPayload,
    _assessed_from_notice_only,
    _bundle_documents,
    _notice_only_overrides,
    format_verdict,
)

NOTICE_TEXT = (
    "Scope: Supply of laboratory consumables to twelve federal hospitals.\n\n"
    "Requirements stated in the notice: Bidder must hold a current NAFDAC registration."
)

#: What the stubbed model reports. Chosen deliberately high so a penalty is unmistakable.
MODEL_CONFIDENCE = 0.9


class _NoticeClient(LLMClient):
    profile_name = "notice-only-test"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.prompts.append(messages[0].content)
        supplied = json.loads(messages[1].content)
        if "Extract every material" in messages[0].content:
            content = json.dumps(_extracted(supplied))
        else:
            content = json.dumps(_payload(supplied))
        return LLMResponse(content, self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001))


def _extracted(supplied):
    # The first *citable* document: a notice-only tender may still carry a failed document
    # alongside it, and an unreadable one contributes no content to quote from.
    document = next(
        document for document in supplied["documents"] if document.get("content")
    )
    chunk = document["content"][0]
    return {
        "requirements": [
            {
                "requirement": "current NAFDAC registration",
                "tender_evidence": [
                    {
                        "document_id": document["document_id"],
                        "location": chunk["location"],
                        "quote": "Bidder must hold a current NAFDAC registration",
                    }
                ],
            }
        ]
    }


def _payload(supplied):
    deadline = supplied["deadline"]
    requirements = supplied["material_requirements"]
    return {
        "schema_version": "verdict.v1",
        "background": "Assessment from the published notice.",
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
                "assessment": "No evidence on file for this requirement.",
                "gap": "No evidence on file for this requirement.",
            }
            for item in requirements
        ],
        "gaps": ["Scoring criteria are not published in the notice."],
        "verdict": "APPLY WITH CONDITIONS",
        "confidence": MODEL_CONFIDENCE,
        "urgency": False,
        "incomplete_inputs": supplied["incomplete_inputs"],
        "limitations": ["Notice text only."],
    }


def _environment(db_session, tmp_path, bundle: TenderDocumentBundle | None = None):
    """Seed a tender with its KB, LLM profile and (optionally) an already-persisted bundle.

    Most tests write the bundle afterwards because it needs the tender id, which only exists
    once the tender has been flushed.
    """
    source = Source(
        name=f"notice-source-{uuid.uuid4().hex}",
        source_type="test",
        base_url="https://tenderdetail.test",
        active=True,
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id=uuid.uuid4().hex,
        url="https://tenderdetail.test/Global-Tenders/TenderNotice/1",
        title="Provision of medical laboratory consumables",
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
        name=f"notice-profile-{uuid.uuid4().hex}",
        base_url="https://mock.test/api",
        model="mock-v1",
        approved_for_company_docs=True,
        context_window_tokens=8000,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
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
    if bundle is not None:
        ExtractionStore(storage).write_bundle(bundle)
    db_session.flush()
    return tender, storage


def _notice_bundle(tender_id: int, *, with_failed_file: bool = False):
    """The real TenderDetail shape: a description on the page, no downloadable document."""
    extraction = notice_extraction(
        NoticeSource(
            title="Provision of medical laboratory consumables",
            source_url="https://tenderdetail.test/Global-Tenders/TenderNotice/1",
            procuring_body="Federal Ministry of Health",
            scope="Supply of laboratory consumables to twelve federal hospitals.",
            requirements_hints=("Bidder must hold a current NAFDAC registration",),
            body_text="Prospective suppliers are invited to submit sealed bids.",
        ),
        tender_id=tender_id,
    )
    assert extraction is not None
    documents: list[DocumentExtraction] = []
    if with_failed_file:
        # The portal linked a file, the download failed. Kept, because it is *why* the
        # assessment is partial.
        documents.append(
            DocumentExtraction(
                document_id=tender_id + 900,
                tender_id=tender_id,
                filename="unavailable-annex.pdf",
                source_url="https://tenderdetail.test/annex.pdf",
                extraction_status="failed",
                extraction_method="none",
                error_code="document_download_failed",
            )
        )
    documents.append(extraction)
    return TenderDocumentBundle(
        tender_id=tender_id, documents=documents, incomplete_inputs=True
    )


def _document_bundle(tender_id: int):
    text = "Bidder must hold a current NAFDAC registration."
    document = DocumentExtraction(
        document_id=tender_id + 1000,
        tender_id=tender_id,
        filename="tender.docx",
        source_url="https://source.test/tender.docx",
        extraction_status="extracted",
        extraction_method="docx",
        sections=[ExtractedSection(index=1, kind="paragraph", text=text)],
    )
    return TenderDocumentBundle(tender_id=tender_id, documents=[document])


def _empty_bundle(tender_id: int):
    return TenderDocumentBundle(
        tender_id=tender_id,
        documents=[
            DocumentExtraction(
                document_id=tender_id + 800,
                tender_id=tender_id,
                filename="unavailable-annex.pdf",
                source_url="https://tenderdetail.test/annex.pdf",
                extraction_status="failed",
                extraction_method="none",
                error_code="document_download_failed",
            )
        ],
        incomplete_inputs=True,
    )


def _run(db_session, storage, tender, client):
    return VerdictEngine(
        db_session, storage, lambda _: client, sleeper=lambda _: None
    ).generate(tender.id)


# ------------------------------------------------------------------------ the headline


def test_notice_only_tender_is_assessed_rather_than_reported_unavailable(
    db_session, tmp_path
) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    # The regression this exists for: previously `requirement_sources_unavailable`.
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert outcome.error_code is None
    assert outcome.verdict_id is not None


def test_confidence_is_reduced_by_the_documented_penalty(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.confidence == pytest.approx(
        MODEL_CONFIDENCE - NOTICE_ONLY_CONFIDENCE_PENALTY, abs=1e-6
    )


def test_penalty_cannot_push_confidence_below_zero(db_session, tmp_path) -> None:
    # The payload is validated in [0, 1] before the penalty; the result must stay a probability.
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    class _ZeroConfidence(_NoticeClient):
        def chat(self, messages, *, max_tokens=None):
            response = super().chat(messages, max_tokens=max_tokens)
            if "Extract every material" not in messages[0].content:
                payload = json.loads(response.content)
                payload["confidence"] = 0.1
                response = LLMResponse(
                    json.dumps(payload), self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001)
                )
            return response

    outcome = _run(db_session, storage, tender, _ZeroConfidence())

    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.confidence == 0.0


def test_inputs_are_reported_as_incomplete(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.incomplete_inputs is True
    assert outcome.incomplete_inputs is True


def _payload_with_confidence(confidence: float, limitations: list[str]):
    return VerdictPayload(
        schema_version="verdict.v1",
        background="Synthetic.",
        requirements=[],
        deadline_status="UNRESOLVED",
        deadline_utc=None,
        deadline_date=None,
        deadline_time=None,
        deadline_timezone=None,
        source_timezone=None,
        assessments=[],
        gaps=[],
        verdict="APPLY WITH CONDITIONS",
        confidence=confidence,
        urgency=False,
        incomplete_inputs=True,
        limitations=limitations,
    )


class TestNoticeOnlyOverrides:
    """The corrections applied in code rather than requested of the model.

    Limitations are not a persisted column on the verdict row, so this is tested where the
    correction actually happens — the payload handed back by the authoritative-override pass.
    """

    def test_the_missing_document_is_stated_in_the_limitations(self) -> None:
        payload = _payload_with_confidence(0.9, ["Notice text only."])
        result = _notice_only_overrides(payload, notice_only=True)
        # Fixed wording, not whatever the model happened to write: "Notice text only." alone
        # tells a reader nothing about which document is missing.
        assert result["limitations"][0] == NOTICE_ONLY_LIMITATION

    def test_the_limitation_leads_rather_than_trails(self) -> None:
        payload = _payload_with_confidence(0.9, ["Model said this."])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["limitations"][0] == NOTICE_ONLY_LIMITATION
        assert "Model said this." in result["limitations"]

    def test_the_model_reasoning_is_kept(self) -> None:
        # A model that says *why* a requirement is unverified has said something useful;
        # the injected caveat is added alongside it, never in place of it.
        payload = _payload_with_confidence(0.9, ["Scoring weights are not published."])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["limitations"][-1] == "Scoring weights are not published."

    def test_the_limitation_is_not_added_twice(self) -> None:
        payload = _payload_with_confidence(0.9, [NOTICE_ONLY_LIMITATION])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["limitations"].count(NOTICE_ONLY_LIMITATION) == 1

    def test_an_equivalent_restatement_is_not_duplicated(self) -> None:
        payload = _payload_with_confidence(0.9, ["No Downloadable source files were offered."])
        result = _notice_only_overrides(payload, notice_only=True)
        assert len(result["limitations"]) == 1

    def test_blank_model_limitations_are_dropped(self) -> None:
        payload = _payload_with_confidence(0.9, ["", "   "])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["limitations"] == [NOTICE_ONLY_LIMITATION]

    def test_confidence_is_reduced(self) -> None:
        payload = _payload_with_confidence(0.9, [])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["confidence"] == pytest.approx(
            0.9 - NOTICE_ONLY_CONFIDENCE_PENALTY, abs=1e-6
        )

    def test_confidence_never_goes_negative(self) -> None:
        payload = _payload_with_confidence(0.1, [])
        result = _notice_only_overrides(payload, notice_only=True)
        assert result["confidence"] == 0.0

    def test_nothing_changes_when_a_document_was_read(self) -> None:
        payload = _payload_with_confidence(0.9, ["Notice text only."])
        assert _notice_only_overrides(payload, notice_only=False) == {}


def test_outcome_carries_the_notice_only_fact(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    assert outcome.notice_only is True


# ------------------------------------------------------------------ a real document is untouched


def test_a_tender_with_a_real_document_is_not_penalised(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_document_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.confidence == pytest.approx(MODEL_CONFIDENCE, abs=1e-6)
    assert outcome.notice_only is False
    assert outcome.incomplete_inputs is False


def test_a_real_document_bundle_is_not_marked_incomplete(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_document_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.incomplete_inputs is False


def test_an_unreadable_file_alone_still_fails_as_before(db_session, tmp_path) -> None:
    """No notice means no fallback: the tender genuinely has no assessable material."""
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_empty_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "requirement_sources_unavailable"


# ----------------------------------------------------------------------- failed file + notice


def test_a_failed_file_does_not_stop_the_notice_being_used(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id, with_failed_file=True))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())

    assert outcome.status is OutcomeStatus.AVAILABLE
    assert outcome.notice_only is True


# ------------------------------------------------------------------------------ the prompts


def test_both_prompts_state_that_no_document_was_available(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    client = _NoticeClient()
    _run(db_session, storage, tender, client)

    assert client.prompts
    assert all("NO DOWNLOADABLE DOCUMENTS" in prompt for prompt in client.prompts)


def test_the_prompt_forbids_inventing_a_document_citation(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    client = _NoticeClient()
    _run(db_session, storage, tender, client)

    joined = "\n".join(client.prompts)
    assert "Never cite a document, annex or page you were not given" in joined


def test_prompts_are_unchanged_when_a_real_document_exists(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_document_bundle(tender.id))
    db_session.flush()

    client = _NoticeClient()
    _run(db_session, storage, tender, client)

    joined = "\n".join(client.prompts)
    assert "NO DOWNLOADABLE DOCUMENTS" not in joined


# ------------------------------------------------------------------- the detection rule itself


class TestNoticeOnlyDetection:
    def test_notice_alone_is_notice_only(self) -> None:
        docs = [{"notice_only": True, "content": [{"text": "x"}]}]
        assert _assessed_from_notice_only(docs) is True

    def test_a_real_document_alongside_is_not_notice_only(self) -> None:
        docs = [
            {"notice_only": True, "content": [{"text": "x"}]},
            {"notice_only": False, "content": [{"text": "y"}]},
        ]
        assert _assessed_from_notice_only(docs) is False

    def test_a_uncitable_document_cannot_make_it_not_notice_only(self) -> None:
        # Stage B's own gate is "any document has content", so a document with none cannot.
        docs = [
            {"notice_only": True, "content": [{"text": "x"}]},
            {"notice_only": False, "content": []},
        ]
        assert _assessed_from_notice_only(docs) is True

    def test_no_content_at_all_is_not_notice_only(self) -> None:
        # That case is `requirement_sources_unavailable`, decided earlier and never reached here.
        assert _assessed_from_notice_only([{"notice_only": True, "content": []}]) is False
        assert _assessed_from_notice_only([]) is False

    def test_bundle_documents_mark_the_notice(self) -> None:
        docs = _bundle_documents(_notice_bundle(1))
        notice_docs = [document for document in docs if document.get("notice_only")]
        assert len(notice_docs) == 1
        assert notice_docs[0]["filename"] == NOTICE_FILENAME

    def test_bundle_documents_do_not_mark_real_files(self) -> None:
        docs = _bundle_documents(_document_bundle(1))
        assert [document["notice_only"] for document in docs] == [False]


# ---------------------------------------------------------------------------- the marker keys


def test_the_notice_marking_survives_a_storage_round_trip(db_session, tmp_path) -> None:
    # The flag is read back from persisted JSON by the verdict stage; if it did not survive,
    # every notice-only tender would silently be unpenalised in production.
    storage = LocalFileSystemStorage(tmp_path)
    bundle = _notice_bundle(7)
    ExtractionStore(storage).write_bundle(bundle)

    reloaded = ExtractionStore(storage).read_bundle(7)

    assert reloaded is not None
    assert _assessed_from_notice_only(_bundle_documents(reloaded)) is True
    assert reloaded.documents[-1].document_metadata[NOTICE_SOURCE_KIND] == "1"


def test_the_synthetic_document_id_is_reserved() -> None:
    # Real Document rows are serial-numbered from 1, so 0 can never name a real document —
    # a collision would let the model cite a file it never read.
    assert NOTICE_DOCUMENT_ID == 0


def test_the_notice_is_labelled_as_text_it_built() -> None:
    extraction = notice_extraction(
        NoticeSource(title="T", scope="Supply 300 kits."), tender_id=1
    )
    assert extraction is not None
    assert extraction.extraction_method == NOTICE_METHOD


# -------------------------------------------------------------------------------- rendering


def test_the_notification_says_no_downloadable_sources(db_session, tmp_path) -> None:
    tender, storage = _environment(db_session, tmp_path)
    ExtractionStore(storage).write_bundle(_notice_bundle(tender.id))
    db_session.flush()

    outcome = _run(db_session, storage, tender, _NoticeClient())
    persisted = db_session.get(Verdict, outcome.verdict_id)

    assert persisted is not None
    content = format_verdict(persisted, outcome)
    assert content.assessment_unavailable is False
    assert content.incomplete_warning is not None
    assert "No Downloadable source files" in content.incomplete_warning

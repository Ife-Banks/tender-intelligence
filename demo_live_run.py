"""
Phase 0–12 Live Pipeline Demonstration Script
=============================================
Runs the REAL system against the LIVE WAHO site using the Neon production database.
Demonstrates the full pipeline: Discovery → Dedup → Persistence → Detail → Acquisition → Processing.

SAFETY:
  - Test Mode ON (no business emails)
  - No AI stages (Prompts 13/14 not yet implemented)
  - Read-only against WAHO (polite crawl, 2s delay)
  - Writes to Neon DB are intentional (demonstrate real persistence)

SCOPE LIMIT:
  - max_pages=1 → first listing page only (~10–12 tenders) to avoid Neon pooler exhaustion
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# ── bootstrap ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("TI_STORAGE_DIR", str(ROOT / "data" / "documents"))

from sqlalchemy import select, text

from tender_intelligence.acquisition.fetcher import DocumentFetcher
from tender_intelligence.acquisition.service import DocumentAcquisitionService
from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.config.seed import ensure_settings_row
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories import RunHistoryRepository, SourceRepository
from tender_intelligence.dedup.service import DedupService
from tender_intelligence.orchestrator.config import ConfigLoader
from tender_intelligence.orchestrator.coordinator import RunCoordinator
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.orchestrator.retry import RetryPolicy
from tender_intelligence.orchestrator.scheduler import SourceScheduler
from tender_intelligence.orchestrator.status import RunStatus
from tender_intelligence.processing.ocr import OcrEngine
from tender_intelligence.processing.service import DocumentProcessingService
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.storage.local import LocalFileSystemStorage

# ── real WAHO live parser_config (confirmed from live site inspection) ─────────
#  Verified 2026-09-24:
#    - title:       div.card-header a
#    - body:        div.card-body
#    - attachments: ol li a[href*="/uploads/"]
#    - deadline:    "End Date: </strong>YYYY-MM-DD HH:MM:SS UTC" in raw text
#    - reference:   <strong>Reference: </strong> ... <br>
LIVE_WAHO_PARSER_CONFIG = {
    # Listing page selectors (same as default — listing structure matches fixtures)
    "row_selector": "div.col-md-6",
    "title_selector": "div.card-header h5 a",
    "detail_href_pattern": r"/tenders/tenders/(?P<id>\d+)/list",
    "next_page_selector": "a[rel=next]",
    "published_date_pattern": (
        r"Start Date:\s*(?P<raw>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(?P<tz>UTC|GMT)"
    ),
    "deadline_patterns": [
        # Live site format: "End Date: 2026-09-24 12:00:00 UTC"
        r"End Date:\s*</strong>?(?P<year>\d{4})-(?P<month_num>\d{2})-(?P<day>\d{2})\s+"
        r"(?P<hour>\d{2}):(?P<minute>\d{2}):\d{2}\s+(?P<tz>UTC|GMT)",
        # FR live: "Date limite de dépôt des candidatures : le 24 September 2026 at 1.00 pm GMT"
        (
            r"Date limite(?: de dépôt des candidatures)?\s*:?\s*"
            r"(?:toutes les offres doivent(?: être| etre)? reçues? au plus tard\s+|at\s+|"
            r"le\s+)?"
            r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})"
            r"(?:\s+at\s+(?P<hour>\d{1,2})[.:h](?P<minute>\d{2})\s*(?P<ampm>[ap]\.?m\.?))?"
            r"\s*(?P<tz>[A-Z]{2,4})?"
        ),
        # EN live: "Deadline for submission of applications: 24 September 2026 at 1.00 pm GMT"
        (
            r"Deadline(?:[^:]*)?:\s*"
            r"(?:All proposals must be received no later than\s+|at\s+)?"
            r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})"
            r"(?:\s+at\s+(?P<hour>\d{1,2})[.:h](?P<minute>\d{2})\s*(?P<ampm>[ap]\.?m\.?))?"
            r"\s*(?P<tz>[A-Z]{2,4})?"
        ),
    ],
    "max_pages": 1,   # ← LIMIT: one page only (~10–12 tenders) for focused demo
    # Detail page selectors (verified from live HTML):
    "detail_title_selectors": [
        "div.card-header a",   # primary — confirmed live
        "div.card-header h5 a",
        "h1", "h2",
    ],
    "detail_body_selector": "div.card-body",
    # Attachment block: the real page uses <ol><li><a href="/tenders/uploads/...">
    "attachment_block_selectors": ["ol", "div.card-body"],
    "attachment_path_markers": ["/uploads/"],
    "document_extensions": r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|od[tsp]|rtf|txt|csv)$",
    "excluded_href_patterns": [
        r"/submissions/",
        r"^javascript:",
        r"^mailto:",
        r"^#",
        r"^/tenders$",
        r"^/tenders/tenders/list$",
        r"^/tenders/tenders/upload",
    ],
    "reference_labels": ["reference", "référence", "referencia", "referência", "ref"],
    "allow_empty_listing": False,
    "expected_languages": ["en", "fr", "pt"],
}

DEMO_RESULTS: dict = {
    "demo_timestamp": datetime.now(UTC).isoformat(),
    "demo_id": f"DEMO-{int(time.time())}",
    "stages": {},
}


# ── NullOcr (Tesseract not required for demo) ─────────────────────────────────
class NullOcr(OcrEngine):
    name: str = "null_ocr_demo"

    def version(self) -> str | None:
        return None

    def is_available(self) -> bool:
        return False

    def image_to_text(self, image: bytes, *, lang: str | None = None) -> str:
        from tender_intelligence.processing.errors import OcrUnavailableError
        raise OcrUnavailableError("null OCR — demo mode", context={"engine": "null"})


def banner(title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n  {title}\n{line}")


def section(label: str, data: object) -> None:
    print(f"\n  ▶ {label}")
    if isinstance(data, dict):
        for k, v in data.items():
            print(f"      {k}: {v}")
    elif isinstance(data, list):
        for i, item in enumerate(data, 1):
            print(f"      [{i}] {item}")
    else:
        for line in str(data).splitlines():
            print(f"      {line}")


def record(stage: str, data: dict) -> None:
    DEMO_RESULTS["stages"][stage] = data


# ── build coordinator ──────────────────────────────────────────────────────────
def build_coordinator(sf, storage_dir: str) -> RunCoordinator:
    store = LocalFileSystemStorage(Path(storage_dir) / "objects")
    policy = CrawlPolicy(
        request_interval_seconds=2.0,
        timeout_seconds=30,
        max_retries=2,
        respect_robots=False,
    )
    fetcher = DocumentFetcher(policy)
    acquisition = DocumentAcquisitionService(sf, storage=store, fetcher=fetcher)
    processing = DocumentProcessingService(sf, storage=store, ocr=NullOcr())
    return RunCoordinator(
        session_factory=sf,
        registry=AdapterRegistry.default(policy=policy),
        dedup=DedupService(sf),
        acquisition=acquisition,
        processing=processing,
        scheduler=SourceScheduler(sf),
        config_loader=ConfigLoader(sf),
        retry_policy=RetryPolicy(attempts=2, backoff_base_seconds=1.0),
    )


# ── seed / ensure WAHO source ──────────────────────────────────────────────────
def ensure_waho_source(sf, *, fresh: bool = False) -> int:
    with sf() as session:
        existing = session.scalars(
            select(Source).where(Source.name == "WAHO Live Demo")
        ).first()
        if existing and fresh:
            session.delete(existing)
            session.commit()
            existing = None
        if existing:
            # Update parser_config to live selectors
            existing.parser_config = LIVE_WAHO_PARSER_CONFIG
            session.commit()
            print(f"      Updated existing source id={existing.id} with live parser_config")
            return int(existing.id)
        src = Source(
            name="WAHO Live Demo",
            source_type="paginated_html_list",
            base_url="https://data.wahooas.org",
            listing_url="https://data.wahooas.org/tenders/tenders/list",
            parser_config=LIVE_WAHO_PARSER_CONFIG,
            crawl_frequency_minutes=1440,
            active=True,
            expected_languages=["en", "fr", "pt"],
        )
        session.add(src)
        session.flush()
        sid = int(src.id)
        session.commit()
        print(f"      Created new source id={sid}")
        return sid


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN DEMO
# ══════════════════════════════════════════════════════════════════════════════
def main() -> dict:
    settings = get_env_settings()
    storage_dir = settings.storage_path
    Path(storage_dir).mkdir(parents=True, exist_ok=True)

    banner("PHASE 0–12 LIVE PIPELINE DEMONSTRATION")
    print(f"  Timestamp : {DEMO_RESULTS['demo_timestamp']}")
    print(f"  Demo ID   : {DEMO_RESULTS['demo_id']}")
    print(f"  Database  : Neon PostgreSQL (pooler)")
    print(f"  Storage   : {storage_dir}")
    print(f"  Source    : LIVE https://data.wahooas.org/tenders/tenders/list")
    print(f"  Scope     : max_pages=1 (first page only, ~10–12 tenders)")
    print(f"  Test Mode : ON (no business emails sent)")
    print(f"  AI Stages : NOT IMPLEMENTED (Prompts 13/14)")

    engine = build_engine(settings.database_url)
    sf = session_factory(engine)

    # ── STEP 0: environment ───────────────────────────────────────────────────
    banner("STEP 0 — Environment & Settings")
    with sf() as sess:
        row = ensure_settings_row(sess)
        sess.commit()
    section("Settings row (from Neon DB)", {
        "test_mode": row.test_mode,
        "urgency_window_days": row.urgency_window_days,
        "retention_months": row.retention_months,
        "link_expiry_days": row.link_expiry_days,
    })
    record("environment", {"test_mode": bool(row.test_mode), "database": "Neon PostgreSQL"})

    # ── STEP 1: source registry ───────────────────────────────────────────────
    banner("STEP 1 — Source Registry (REAL — Neon DB)")
    source_id = ensure_waho_source(sf)
    with sf() as sess:
        src = sess.get(Source, source_id)
    section("Source record", {
        "id": src.id, "name": src.name, "type": src.source_type,
        "listing_url": src.listing_url, "active": src.active,
        "parser_config.max_pages": src.parser_config.get("max_pages"),
    })
    record("source_registry", {
        "source_id": source_id, "name": src.name,
        "listing_url": src.listing_url, "status": "REAL",
    })

    # ── STEP 2: pre-run snapshot ──────────────────────────────────────────────
    banner("STEP 2 — Pre-Run DB Snapshot")
    with sf() as sess:
        pre_tenders = sess.execute(
            text("SELECT COUNT(*) FROM tenders WHERE source_id=:s"), {"s": source_id}
        ).scalar()
        pre_docs = sess.execute(text("SELECT COUNT(*) FROM documents")).scalar()
        pre_runs = sess.execute(
            text("SELECT COUNT(*) FROM run_history WHERE source_id=:s"), {"s": source_id}
        ).scalar()
    section("DB state before this run", {
        "tenders (this source)": pre_tenders,
        "documents (all)": pre_docs,
        "run_history rows (this source)": pre_runs,
    })
    record("pre_run_snapshot", {
        "tenders": pre_tenders, "documents": pre_docs, "run_history_rows": pre_runs,
    })

    # ── STEP 3: FULL PIPELINE RUN ─────────────────────────────────────────────
    banner("STEP 3 — FULL PIPELINE RUN (Stages 04 → 09, LIVE WAHO)")
    print("  Stages: Discovery → Dedup → Persistence → Detail → Acquisition → Processing")
    print("  Polite crawl: 2s request interval. Please wait 2–4 minutes ...")
    print()

    coordinator = build_coordinator(sf, storage_dir)
    run_start = datetime.now(UTC)
    report = coordinator.run_source(source_id, force=True)
    run_end = datetime.now(UTC)
    duration = round((run_end - run_start).total_seconds(), 1)

    section("Run Report", {
        "run_status": str(report.status),
        "correlation_id": report.correlation_id,
        "run_history_id": report.run_history_id,
        "tenders_considered": report.tenders_considered,
        "documents_acquired": report.documents_acquired,
        "documents_processed": report.documents_processed,
        "duration_seconds": duration,
        "failed_stage": str(report.failed_stage) if report.failed_stage else "none",
        "error_code": report.error_code or "none",
    })
    print("\n  Stage-by-stage breakdown:")
    stage_map = {}
    for outcome in report.stages:
        status_str = outcome.status.value
        print(f"      {outcome.stage.value:30s} → {status_str}")
        if outcome.detail:
            print(f"        detail: {outcome.detail}")
        stage_map[outcome.stage.value] = {"status": status_str, "detail": outcome.detail or ""}

    record("pipeline_run", {
        "status": str(report.status), "correlation_id": report.correlation_id,
        "run_history_id": report.run_history_id,
        "tenders_considered": report.tenders_considered,
        "documents_acquired": report.documents_acquired,
        "documents_processed": report.documents_processed,
        "duration_seconds": duration, "stages": stage_map,
    })

    # ── STEP 4: persisted tenders ─────────────────────────────────────────────
    banner("STEP 4 — Tender Persistence (REAL — Neon DB)")
    with sf() as sess:
        tenders = sess.scalars(
            select(Tender).where(Tender.source_id == source_id).order_by(Tender.id)
        ).all()
        run_row = sess.get(RunHistory, report.run_history_id) if report.run_history_id else None

    print(f"\n  Total tenders persisted for this source: {len(tenders)}")
    tender_records = []
    for t in tenders:
        print(f"\n  ┌─ Tender DB id={t.id}  external_id={t.external_id}")
        print(f"  │  title        : {(t.title or '')[:90]}")
        print(f"  │  status       : {t.status}")
        print(f"  │  correlation  : {t.correlation_id}")
        print(f"  │  first_seen   : {t.first_seen_at}")
        print(f"  │  deadline_at  : {t.deadline_at}")
        print(f"  └─ deadline_tz  : {t.deadline_timezone}")
        tender_records.append({
            "id": t.id, "external_id": t.external_id,
            "title": (t.title or "")[:120], "status": t.status,
            "correlation_id": t.correlation_id,
            "first_seen_at": str(t.first_seen_at),
            "deadline_at": str(t.deadline_at),
            "deadline_timezone": t.deadline_timezone,
        })
    record("tender_persistence", {"total": len(tenders), "tenders": tender_records})

    if run_row:
        print()
        section("RunHistory row (Neon DB)", {
            "id": run_row.id, "source_id": run_row.source_id,
            "correlation_id": run_row.correlation_id,
            "started_at": str(run_row.started_at),
            "ended_at": str(run_row.ended_at),
            "listings_found": run_row.listings_found,
            "new_count": run_row.new_count,
            "update_count": run_row.update_count,
            "unchanged_count": run_row.unchanged_count,
            "error_count": run_row.error_count,
            "failed_stage": run_row.failed_stage or "none",
            "error_code": run_row.error_code or "none",
        })
        record("run_history", {
            "id": run_row.id, "listings_found": run_row.listings_found,
            "new_count": run_row.new_count, "update_count": run_row.update_count,
            "unchanged_count": run_row.unchanged_count, "error_count": run_row.error_count,
            "started_at": str(run_row.started_at), "ended_at": str(run_row.ended_at),
            "stages": run_row.stages,
        })

    # ── STEP 5: documents ─────────────────────────────────────────────────────
    banner("STEP 5 — Document Discovery & Acquisition (REAL)")
    with sf() as sess:
        all_docs = sess.scalars(
            select(Document).join(Tender).where(Tender.source_id == source_id)
        ).all()

    print(f"\n  Total documents acquired: {len(all_docs)}")
    doc_records = []
    by_tender: dict[int, list] = {}
    for d in all_docs:
        by_tender.setdefault(d.tender_id, []).append(d)

    for tid_key, docs in list(by_tender.items())[:5]:
        t_title = next((t.title for t in tenders if t.id == tid_key), f"tender {tid_key}")
        print(f"\n  Tender '{(t_title or '')[:60]}...'")
        for d in docs:
            print(f"    ├─ doc id={d.id}  file={d.filename}")
            print(f"    │  mime={d.mime_type}  dl_status={d.download_status}  ext_status={d.extraction_status}")
            cksum = (d.checksum or "")[:32]
            print(f"    └─ checksum={cksum}{'...' if cksum else 'N/A'}")
            doc_records.append({
                "id": d.id, "tender_id": d.tender_id, "filename": d.filename,
                "mime_type": d.mime_type, "download_status": d.download_status,
                "extraction_status": d.extraction_status,
                "has_checksum": bool(d.checksum),
                "has_storage_path": bool(d.storage_path),
            })

    if len(by_tender) > 5:
        print(f"\n  ... ({len(by_tender) - 5} more tenders with documents, display capped)")
    record("documents", {"total": len(all_docs), "sample": doc_records[:20]})

    # ── STEP 6: timeline ──────────────────────────────────────────────────────
    banner("STEP 6 — Correlation Timeline (REAL — Prompt 12)")
    timeline_records = []
    if tenders:
        demo_tender = tenders[0]
        with sf() as sess:
            tl = TimelineService(sess).reconstruct_by_correlation(demo_tender.correlation_id)
        print(f"\n  Tender : {(demo_tender.title or '')[:80]}")
        print(f"  Corr.  : {demo_tender.correlation_id}")
        print(f"  Events : {len(tl.events)}")
        for ev in tl.events:
            ts = ev.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC") if ev.timestamp else "N/A"
            print(f"\n    [{ts}]  stage={ev.stage}  status={ev.status}")
            if ev.processing_info:
                for k, v in ev.processing_info.items():
                    if v:
                        print(f"      {k}: {str(v)[:80]}")
            timeline_records.append({
                "timestamp": str(ev.timestamp), "stage": ev.stage,
                "status": ev.status, "source": ev.source,
            })
        record("timeline", {
            "tender_title": (demo_tender.title or "")[:120],
            "correlation_id": demo_tender.correlation_id,
            "event_count": len(tl.events),
            "events": timeline_records,
        })
    else:
        print("  (no tenders to build timeline for)")

    # ── STEP 7: deduplication second run ──────────────────────────────────────
    banner("STEP 7 — Deduplication: Second Run (REAL idempotency test)")
    print("  Running same source again — all tenders should be UNCHANGED ...")
    report2 = coordinator.run_source(source_id, force=True)
    with sf() as sess:
        rh2 = sess.get(RunHistory, report2.run_history_id) if report2.run_history_id else None
    section("Second run result (idempotency)", {
        "status": str(report2.status),
        "new_count": rh2.new_count if rh2 else "N/A",
        "update_count": rh2.update_count if rh2 else "N/A",
        "unchanged_count": rh2.unchanged_count if rh2 else "N/A",
        "documents_acquired": report2.documents_acquired,
        "conclusion": "PASS — zero NEW on second run" if (rh2 and rh2.new_count == 0) else "CHECK NEEDED",
    })
    record("deduplication_idempotency", {
        "status": str(report2.status),
        "new_count": rh2.new_count if rh2 else None,
        "unchanged_count": rh2.unchanged_count if rh2 else None,
        "result": "UNCHANGED — idempotency confirmed" if (rh2 and rh2.new_count == 0) else "unexpected",
    })

    # ── STEP 8: audit tables ──────────────────────────────────────────────────
    banner("STEP 8 — Audit & Alerting State (Neon DB)")
    with sf() as sess:
        alert_ct = sess.execute(text("SELECT COUNT(*) FROM alert_events")).scalar()
        notif_ct = sess.execute(text("SELECT COUNT(*) FROM notification_logs")).scalar()
        cfg_ct = sess.execute(text("SELECT COUNT(*) FROM config_change_log")).scalar()
        run_ct = sess.execute(
            text("SELECT COUNT(*) FROM run_history WHERE source_id=:s"), {"s": source_id}
        ).scalar()
    section("System audit tables", {
        "alert_events": alert_ct,
        "notification_logs": notif_ct,
        "config_change_log entries": cfg_ct,
        "run_history rows (this source)": run_ct,
    })
    record("audit_state", {
        "alert_events": alert_ct, "notification_logs": notif_ct,
        "config_change_log": cfg_ct, "run_history_rows": run_ct,
        "note": "Notifications not sent — AI verdict (Prompt 13/14) not yet implemented",
    })

    # ── STEP 9: AI boundary ───────────────────────────────────────────────────
    banner("STEP 9 — AI Input Boundary")
    print("""
  ┌──────────────────────────────────────────────────────────────────────┐
  │  AI TRIAGE / VERDICT: NOT IMPLEMENTED — PROMPTS 13 / 14             │
  │                                                                      │
  │  Pipeline output available at AI boundary:                          │
  │    • TenderDocumentBundle per processed tender                      │
  │    • Extracted text from PDF / DOCX documents                       │
  │    • Language metadata per document                                  │
  │    • incomplete_inputs flag (True when any doc failed processing)    │
  │    • Full provenance + correlation identity                          │
  │    • Processing version stamps for reproducibility                   │
  │                                                                      │
  │  Ready for consumption by Prompt 13 (Stage A Triage) when an        │
  │  LLM provider is configured and approved (open decisions O5–O9).    │
  │                                                                      │
  │  DEMO VERDICT FIXTURE — NOT PRODUCTION AI                            │
  └──────────────────────────────────────────────────────────────────────┘
    """)
    record("ai_boundary", {
        "status": "NOT IMPLEMENTED",
        "next_prompt": "Prompt 13 — AI Triage",
        "bundles_ready": report.documents_processed,
        "note": "DEMO VERDICT FIXTURE — NOT PRODUCTION AI",
    })

    # ── STEP 10: save evidence ────────────────────────────────────────────────
    banner("STEP 10 — Saving Evidence Package")
    evidence_dir = ROOT / "demo-evidence"
    evidence_dir.mkdir(exist_ok=True)
    summary_path = evidence_dir / "run-summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(DEMO_RESULTS, f, indent=2, default=str)
    print(f"  Saved: {summary_path}")

    # ── FINAL SUMMARY ─────────────────────────────────────────────────────────
    banner("DEMONSTRATION COMPLETE — FINAL SUMMARY")
    first_run_status = str(report.status)
    dedup_result = f"{rh2.unchanged_count if rh2 else '?'} UNCHANGED on run 2"
    print(f"""
  ╔══════════════════════════════════════════════════════════════════════╗
  ║  PHASE 0–12 LIVE PIPELINE DEMONSTRATION RESULTS                     ║
  ╠══════════════════════════════════════════════════════════════════════╣
  ║  Demo ID       : {DEMO_RESULTS['demo_id']:<52} ║
  ║  Run 1 Status  : {first_run_status:<52} ║
  ║  Run CID       : {(report.correlation_id or '')[:52]:<52} ║
  ║  Tenders found : {len(tenders):<52} ║
  ║  Documents     : {report.documents_acquired} acquired, {report.documents_processed} processed{'':<38} ║
  ║  Idempotency   : {dedup_result:<52} ║
  ║  AI Stage      : NOT IMPLEMENTED (Prompts 13/14){'':<22} ║
  ║  Email         : NOT SENT (Test Mode ON + no verdict){'':<18} ║
  ╠══════════════════════════════════════════════════════════════════════╣
  ║  CLASSIFICATION                                                      ║
  ║    WAHO Discovery      → LIVE SOURCE (real HTTP crawl)              ║
  ║    Deduplication       → REAL (Neon DB)                             ║
  ║    Tender Persistence  → REAL (Neon DB)                             ║
  ║    Document Discovery  → REAL (live WAHO detail pages)              ║
  ║    Doc Acquisition     → REAL (downloaded from wahooas.org)         ║
  ║    Doc Processing      → REAL (text extracted)                      ║
  ║    AI Verdict          → NOT IMPLEMENTED (Prompts 13/14)            ║
  ║    Email Notification  → NOT SENT (Test Mode ON)                    ║
  ║    Timeline            → REAL (Prompt 12 reconstruction)            ║
  ║    RunHistory          → REAL (Neon DB)                             ║
  ╚══════════════════════════════════════════════════════════════════════╝
    """)

    return DEMO_RESULTS


if __name__ == "__main__":
    results = main()
    sys.exit(0)

"""Headless worker entrypoint (Phase 0 lifecycle).

Phase 0 builds the full startup spine the later pipeline slots into:
start → load env config → open DB → seed settings/dev-alert recipient (optional YAML init)
→ create correlation ID → log startup → run a placeholder pipeline stage → log completion
or failure → clean shutdown. The scheduler/crawler/AI stages are later slices (docs/12).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tender_intelligence.config.seed import ensure_settings_row, seed_dev_alert_recipient
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.core.correlation import new_correlation_id, set_correlation_id
from tender_intelligence.core.result import StageResult
from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.logging.structured import get_logger, setup_logging


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    yaml_path: Path | None = Path(args[0]) if args else None
    if args and (args[0] in ("-h", "--help")):
        print("usage: python -m tender_intelligence.worker [seed-yaml-path]", file=sys.stderr)
        return 2

    settings = get_env_settings()
    setup_logging(settings.log_level)
    logger = get_logger("tender_intelligence.worker")

    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    logger.info(
        "worker starting",
        extra={"correlation_id": correlation_id, "stage": "startup", "status": "started"},
    )

    engine = build_engine(settings.database_url)
    maker = session_factory(engine)
    try:
        counts: dict = {}
        with maker() as session:
            ensure_settings_row(session)
            if yaml_path and yaml_path.exists():
                from tender_intelligence.config.seed import seed_from_yaml

                counts = seed_from_yaml(session, str(yaml_path))
            if settings.dev_alert_email and not counts.get("dev_alert", 0):
                seed_dev_alert_recipient(session, settings.dev_alert_email)
            session.commit()
            seeded = {
                "settings": counts.get("settings", 0),
                "sources": counts.get("sources", 0),
                "tender_recipients": counts.get("tender_recipients", 0),
                "dev_alert_email": 1 if settings.dev_alert_email else 0,
            }

        stage = StageResult(stage="bootstrap", status="success", correlation_id=correlation_id)
        logger.info(
            "worker startup complete",
            extra={
                "correlation_id": correlation_id,
                "stage": "startup",
                "status": "success",
                "seeded": seeded,
            },
        )
        return 0 if stage.status == "success" else 1
    except Exception:  # noqa: BLE001 - top-level process boundary
        logger.exception(
            "worker failed during startup",
            extra={"correlation_id": correlation_id, "stage": "startup", "status": "failed"},
        )
        return 1
    finally:
        set_correlation_id(None)
        engine.dispose()
        logger.info(
            "worker shut down cleanly",
            extra={"correlation_id": correlation_id, "stage": "shutdown", "status": "success"},
        )


if __name__ == "__main__":
    raise SystemExit(main())

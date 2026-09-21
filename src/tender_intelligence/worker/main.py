"""Headless worker entrypoint (Phase 0 seam).

Phase 0 builds only the startup spine: load operator config, open the database, ensure the
settings singleton + dev-alert recipient from env, and emit a structured startup record.
The scheduler/pipeline engine is a later slice (docs/12 Phase 1+).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tender_intelligence.config.seed import ensure_settings_row, seed_dev_alert_recipient
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.logging.structured import get_logger, setup_logging


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m tender_intelligence.worker [seed-yaml-path]", file=sys.stderr)
        return 2
    yaml_path = Path(args[0])

    settings = get_env_settings()
    setup_logging(settings.log_level)
    logger = get_logger("tender_intelligence.worker")

    engine = build_engine(settings.database_url)
    maker = session_factory(engine)
    from tender_intelligence.config.seed import seed_from_yaml

    counts = {}
    with maker() as session:
        ensure_settings_row(session)
        counts = seed_from_yaml(session, str(yaml_path))
        if settings.dev_alert_email and not counts.get("dev_alert_email", 0):
            seed_dev_alert_recipient(session, settings.dev_alert_email)
        session.commit()

    logger.info(
        "worker startup: config seeded",
        extra={"stage": "startup", "status": "success", "extra": {"seeded": counts}},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
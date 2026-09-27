"""Runtime composition for the existing Stage B verdict service."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.storage.interface import ObjectStorage
from tender_intelligence.triage.service import TriageDecision
from tender_intelligence.verdict.service import ClientFactory, OutcomeStatus, VerdictEngine

log = logging.getLogger("tender_intelligence.verdict.runtime")


def build_verdict_handoff(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    client_factory: ClientFactory | None,
) -> Callable[[TriageDecision], object]:
    """Build a per-tender handoff that persists the result through ``VerdictEngine``."""

    def handoff(decision: TriageDecision) -> object:
        if not decision.proceeds_to_verdict:
            return None
        with sessions() as session:
            outcome = VerdictEngine(
                session,
                storage,
                client_factory,
                run_id=decision.run_id,
            ).generate(decision.tender_id)
            session.commit()
        if outcome.status is OutcomeStatus.FAILED:
            log.warning(
                "Stage B produced an explicit failure outcome",
                extra={
                    "stage": "14-verdict",
                    "status": "failed",
                    "tender_id": decision.tender_id,
                    "correlation_id": decision.correlation_id,
                    "run_id": decision.run_id,
                    "error_code": outcome.error_code,
                },
            )
        return outcome

    return handoff

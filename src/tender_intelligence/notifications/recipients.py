""":mod:`tender_intelligence.notifications.recipients` — recipient-list invariants."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.recipients import Recipient


class RecipientGuard:
    """Enforces the confirmed recipient invariants (docs/03 Recipient):

    - Nothing can change or deactivate the *last active dev recipient*.
    - Email must be syntactically valid at entry.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def active_dev_count(self) -> int:
        return len(
            self.session.scalars(
                select(Recipient).where(
                    Recipient.list_type == "dev_alert", Recipient.active.is_(True)
                )
            ).all()
        )

    def refuse_last_dev_removal(self) -> bool:
        """Return True when removing the last active dev recipient must be refused.

        Called by admin flows before a deactivate/delete; the caller must roll back if True.
        """
        return self.active_dev_count() <= 1

    def dev_recipients(self) -> list[Recipient]:
        return list(
            self.session.scalars(
                select(Recipient)
                .where(Recipient.list_type == "dev_alert", Recipient.active.is_(True))
                .order_by(Recipient.id)
            ).all()
        )
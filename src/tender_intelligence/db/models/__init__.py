"""SQLAlchemy ORM models implementing the confirmed entities of ``docs/03`` / v1.1 §7."""

from tender_intelligence.db.models.alerts import AlertEvent
from tender_intelligence.db.models.config import ConfigChangeLog, Setting
from tender_intelligence.db.models.deadline import TenderDeadlineResolution
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.mail import (
    MailProvider,
    MailProviderUsage,
    NotificationAttempt,
    NotificationLog,
)
from tender_intelligence.db.models.recipients import Recipient
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.users import AdminUser
from tender_intelligence.db.models.verdicts import Verdict

__all__ = [
    "AdminUser",
    "AlertEvent",
    "ConfigChangeLog",
    "Document",
    "KnowledgeBaseVersion",
    "LLMCall",
    "LLMProfile",
    "LLMRoleAssignment",
    "MailProvider",
    "MailProviderUsage",
    "NotificationAttempt",
    "NotificationLog",
    "Recipient",
    "RunHistory",
    "Setting",
    "Source",
    "Tender",
    "TenderDeadlineResolution",
    "TriageResult",
    "Verdict",
]

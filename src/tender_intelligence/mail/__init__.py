"""Provider-neutral mail subsystem.

The notification layer depends on the abstractions exported here; provider-specific wire
code remains inside an adapter module.  Sendlib is the only concrete adapter in Prompt 11;
providers 2/3 remain open decisions (O4/O13).
"""

from tender_intelligence.mail.chain import (
    ChainAttempt,
    ChainResult,
    MailRetryPolicy,
    ProviderChain,
    ProviderEntry,
)
from tender_intelligence.mail.errors import MailError
from tender_intelligence.mail.message import EmailMessage, MailAttachment
from tender_intelligence.mail.planner import AttachmentPlanner, PlanResult
from tender_intelligence.mail.provider import Capabilities, MailProvider, SendResult

__all__ = [
    "AttachmentPlanner",
    "Capabilities",
    "ChainAttempt",
    "ChainResult",
    "EmailMessage",
    "MailAttachment",
    "MailError",
    "MailProvider",
    "MailRetryPolicy",
    "PlanResult",
    "ProviderChain",
    "ProviderEntry",
    "SendResult",
]

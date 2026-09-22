""":mod:`tender_intelligence.interfaces.llm` — LLMClient seam (docs/07, docs/02 §2.8).

Standardised on the OpenAI-compatible chat-completions shape *[PROPOSED, decision D4]*.
Profiles are resolved per role (``triage``, ``verdict``) with retry-then-fallback, and the
fallback must obey the same ``approved_for_company_docs`` data policy. Per-call usage is
logged to ``LLMCall``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from tender_intelligence.core.errors import AI_CALL_TIMEOUT


@dataclass(frozen=True)
class LLMMessage:
    """One OpenAI-compatible chat message."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


@dataclass(frozen=True)
class LLMUsage:
    """Token/cost usage mirroring DM ``LLMCall`` fields."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    """A complete model reply plus usage and the profile that produced it."""

    content: str
    profile_name: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: dict = field(default_factory=dict)


class LLMError(Exception):
    """A model call failed or returned invalid output; carries a structured code."""

    def __init__(self, message: str, error_code: str = AI_CALL_TIMEOUT) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class LLMClient(ABC):
    """Abstract model-call seam.

    Implementations wrap the OpenAI-compatible chat endpoint for exactly one provider
    profile and are interchangeable via configuration (PROJECT_RULES #9).
    """

    profile_name: str

    @abstractmethod
    def chat(self, messages: list[LLMMessage], *, max_tokens: int | None = None) -> LLMResponse:
        """Send *messages* and return the model's completion.

        Raise :class:`LLMError` (structured code) on timeout or invalid output so the
        caller can retry then fall back to the role's fallback profile.
        """

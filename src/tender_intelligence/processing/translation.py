"""The translation boundary (prompt 12 §6).

The problem
-----------
Tender documents are routinely French or Portuguese. The AI provider that will read them may
require a single working language. Two failure modes follow, and both are worse than a missing
feature:

* **Silent translation inside extraction.** A translator wired into the PDF reader or the DOCX
  reader means the stored text is no longer the document's text. Evidence quoted back in a verdict
  would no longer match the source, ``Document.extracted_text`` would stop being reproducible, and
  a mistranslated requirement would be indistinguishable from a correct one. It also makes
  extraction non-deterministic, which is the property the reuse and fingerprint machinery is built
  on.
* **Silent discard.** Dropping non-English content because the model prefers English is worse
  still: a French eligibility annex simply disappears and a verdict is reached without it, looking
  complete.

What this module does instead
-----------------------------
It names the boundary and makes the unconfigured state explicit.

* :class:`TranslationBoundary` is the seam a provider would implement. It is injected, never
  constructed here — **no provider is invented or bundled**, because choosing a translation vendor
  is a business decision, not an extraction detail.
* :class:`NullTranslationBoundary` is the shipped implementation. It is permanently unavailable
  and its ``translate`` is never reached, so original text cannot be altered by accident.
* :class:`TranslationRecord` is what the bundle carries: the languages actually found, whether
  originals are preserved verbatim, and — when no provider is configured — an explicit statement
  of the limitation and its consequence for the AI stage.

So when translation is unconfigured the outcome is not silence. The bundle says
``"status": "not_configured"``, names the languages involved, and states that the AI stage will
read the documents in their original language. Any consumer can assert on it.

Where the record stops
----------------------
The record is persisted on the bundle, which is the handoff artifact. It is *not* injected into the
Stage B model payload: ``verdict.service._bundle_documents`` forwards document text only. That is a
deliberate boundary, not an oversight. A model told "the limitation is unresolved" with no
instruction on how to respond has nothing to do with the information except treat foreign-language
evidence as suspect or omit it, which is the discard failure mode this module exists to prevent.
Surfacing it usefully means a prompt instruction to treat non-English text as quotable evidence
verbatim, and that is a Stage B prompt change to be made on its own terms, not smuggled in here.
``tests/unit/test_verdict_bundle_documents.py`` pins the current boundary so it cannot move by
accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: No provider is configured, originals are preserved, and the AI stage reads them as-is.
NOT_CONFIGURED = "not_configured"

#: Every document's language already matches what the consumer asked for, so no boundary is needed.
NOT_REQUIRED = "not_required"

#: A configured provider was used. Not produced by any implementation in this module.
TRANSLATED = "translated"

#: Translation states. ``translated`` exists so a future provider is representable without a schema
#: change, not because anything sets it today.
TRANSLATION_STATUSES: tuple[str, ...] = (NOT_CONFIGURED, NOT_REQUIRED, TRANSLATED)

#: The limitation recorded when no provider is configured *and* no language was detected. It is the
#: default of :attr:`TranslationRecord.limitation` so that a default-constructed record is still
#: explicit: a record that merely *could* carry a limitation is a record that is silent about it,
#: which is the outcome prompt 12 §6 rules out.
UNRESOLVED_LANGUAGE_LIMITATION = (
    "No translation provider is configured. Document text is preserved verbatim in its original "
    "language, which could not be determined for this tender. The AI stage reads these documents "
    "as published, so quotes and requirements must be returned in the source language rather than "
    "assumed to be English."
)


@runtime_checkable
class TranslationBoundary(Protocol):
    """The seam a translation provider would implement (prompt 12 §6).

    Deliberately narrow. It is asked whether it can work and for a working language; it is never
    called on the extraction path, so a configured provider cannot make extraction
    non-deterministic.
    """

    name: str

    def is_available(self) -> bool:
        """Whether translation is configured and usable at all."""

    def working_language(self) -> str | None:
        """The language this boundary would translate into, or ``None`` when unavailable."""

    def translate(self, text: str, *, target_language: str) -> str:
        """Translate *text*. Not part of the extraction path; see the module docstring."""


class NullTranslationBoundary:
    """The shipped boundary: translation is not configured (prompt 12 §6).

    ``translate`` raises rather than returning the input, so a caller that ignores
    :meth:`is_available` fails loudly instead of believing it received a translation.
    """

    name = "none"

    def is_available(self) -> bool:
        return False

    def working_language(self) -> str | None:
        return None

    def translate(self, text: str, *, target_language: str) -> str:
        raise NotImplementedError(
            "no translation provider is configured; original document text is preserved verbatim"
        )


@dataclass(frozen=True)
class TranslationRecord:
    """What the AI stage needs to know about language handling (prompt 12 §6)."""

    status: str = NOT_CONFIGURED
    provider: str | None = None
    working_language: str | None = None
    source_languages: list[str] = field(default_factory=list)
    originals_preserved: bool = True
    limitation: str | None = UNRESOLVED_LANGUAGE_LIMITATION

    @property
    def is_limitation_recorded(self) -> bool:
        """Whether a limitation is explicitly stated rather than left implicit."""
        return self.status == NOT_CONFIGURED and self.limitation is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "working_language": self.working_language,
            "source_languages": list(self.source_languages),
            "originals_preserved": self.originals_preserved,
            "limitation": self.limitation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranslationRecord:
        return cls(
            status=str(data.get("status", NOT_CONFIGURED)),
            provider=data.get("provider"),
            working_language=data.get("working_language"),
            source_languages=[str(item) for item in data.get("source_languages", [])],
            originals_preserved=bool(data.get("originals_preserved", True)),
            limitation=data.get("limitation"),
        )


def describe_unconfigured(languages: list[str]) -> str:
    """The limitation sentence recorded when no provider is configured.

    Named languages are listed so a consumer reading only this field still knows what it is
    dealing with. With no detectable language the sentence says so rather than implying English.
    """
    if not languages:
        return UNRESOLVED_LANGUAGE_LIMITATION
    found = ", ".join(languages)
    return (
        f"No translation provider is configured. Document text is preserved verbatim in its "
        f"original language ({found}). The AI stage reads these documents in their original "
        f"language, so quotes and requirements must be returned in that language rather than "
        f"assumed to be English."
    )


def build_translation_record(
    languages: list[str], boundary: TranslationBoundary | None = None
) -> TranslationRecord:
    """Describe the language situation of a bundle against the configured boundary.

    With no boundary, or an unavailable one, this records the limitation explicitly and asserts
    that originals are preserved. With an available boundary, the record names the provider and
    its working language — the state a future provider would produce.
    """
    if boundary is not None and boundary.is_available():
        return TranslationRecord(
            status=TRANSLATED,
            provider=boundary.name,
            working_language=boundary.working_language(),
            source_languages=list(languages),
            originals_preserved=False,
            limitation=None,
        )
    return TranslationRecord(
        status=NOT_CONFIGURED,
        provider=None,
        working_language=None,
        source_languages=list(languages),
        originals_preserved=True,
        limitation=describe_unconfigured(languages),
    )

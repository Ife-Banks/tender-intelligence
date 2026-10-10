""":mod:`tender_intelligence.processing.notice` — the tender's own notice as a document.

Why this exists
===============
Stage B refuses to assess a tender it has no requirement sources for. That rule is right, but it
produced a wrong outcome for a whole class of real sources: portals that *list* tenders and
publish a usable description, while keeping the tender document itself behind a login or a
subscription. TenderDetail is the clearest case — the detail page carries the title, the procuring
entity, the scope, the reference numbers and often the requirement bullets, but exposes no
downloadable file. Those tenders were reported as ``requirement_sources_unavailable`` and went out
as raw notices: the system said nothing when it in fact had enough to say something.

This module turns the notice a source *did* publish into a first-class, citable document, so the
tender can be assessed on the record that is publicly available rather than silently skipped.

What it is honest about
-----------------------
A notice is not the tender document. It is a summary written by the procuring entity, and it is
routinely missing the annexes where eligibility, qualification and scoring criteria actually live.
So the notice document is marked (:data:`NOTICE_SOURCE_KIND`) and every downstream consumer is
expected to treat it as a weaker input:

* the bundle records ``incomplete_inputs`` — an assessment reached this way *is* partial
  (docs/06 §6.5);
* Stage B applies a confidence penalty (:mod:`tender_intelligence.verdict.service`) and states
  the limitation in its own words;
* the notification renders "No Downloadable source files" so the reader knows the assessment rests
  on a summary rather than on the tender document.

The notice text is never padded to look like a document. If a source published nothing usable,
:func:`notice_extraction` returns ``None`` and the tender keeps its existing behaviour.

Citable structure
-----------------
Stage B requires every tender quote to exist verbatim at a cited location, so the notice is
emitted as :class:`~tender_intelligence.processing.representation.ExtractedSection` blocks rather
than one opaque blob. Each labelled field becomes its own section with a stable
``section:N`` location, which is what lets a model cite "the scope" specifically instead of
quoting the whole page.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from tender_intelligence.processing.languages import detect_language
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedSection,
    ProcessingMetadata,
)
from tender_intelligence.processing.versions import (
    PROCESSOR_NAME,
    PROCESSOR_VERSION,
    content_fingerprint,
)

#: ``document_metadata`` key marking a document as the notice rather than a downloaded file.
#: Consumers branch on this rather than on the filename, which is a display label only.
NOTICE_SOURCE_KIND = "notice_text"

#: ``extraction_method`` recorded for notice text. Kept distinct from ``none`` (nothing was
#: extracted) and from the real extractors: this text was *built* from the source's own words, not
#: decoded from a container format.
NOTICE_METHOD = "notice_text"

#: Reserved synthetic ``document_id``. Real ``Document`` rows are serial-numbered from 1, so 0 can
#: never collide with one, and Stage B matches citations on ``document_id`` — a collision here
#: would let a model cite a real document it never read. Asserted in tests.
NOTICE_DOCUMENT_ID = 0

#: Display name for the synthetic document. It appears in the email's document list, so it says
#: what it is rather than pretending to be the tender document.
NOTICE_FILENAME = "tender-notice.txt"

#: Labels used for each notice field, in the order they are emitted. The label is part of the
#: section text, so a quote that includes "Scope:" is verifiable against the same chunk.
_LABELS = {
    "title": "Title",
    "entity": "Procuring entity",
    "references": "Reference numbers",
    "published": "Published",
    "deadline": "Submission deadline (as published)",
    "scope": "Scope",
    "requirements": "Requirements stated in the notice",
    "body": "Notice text",
}


@dataclass(frozen=True)
class NoticeSource:
    """What a source published about a tender in place of a downloadable file.

    Every field is optional except :attr:`title`: a portal that publishes a bare title is still
    better than nothing, and :func:`notice_extraction` decides whether the total is usable.

    ``body_text`` is the detail page's visible text. HTML is the adapter's business and not this
    module's, so the adapter passes plain text already.
    """

    title: str
    source_url: str = ""
    procuring_body: str | None = None
    reference_numbers: tuple[str, ...] = ()
    requirements_hints: tuple[str, ...] = ()
    scope: str | None = None
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    deadline_timezone: str | None = None
    language: str | None = None
    body_text: str | None = None


def _clean(value: str | None) -> str:
    """Collapse whitespace and drop empties, so a blank field contributes no section."""
    if not value:
        return ""
    return " ".join(value.split())


def _format_instant(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    stamp = aware.strftime("%Y-%m-%d %H:%M %Z").strip()
    return stamp or ""


def notice_sections(source: NoticeSource) -> list[tuple[str, str]]:
    """The notice as ordered ``(label, text)`` pairs, dropping anything empty.

    Ordering is fixed rather than derived from what happened to be present, so the same tender
    produces the same sections on every run and a previously-cited location still means the same
    thing (prompt 09 §14 determinism).
    """
    blocks: list[tuple[str, str]] = [
        ("title", _clean(source.title)),
        ("entity", _clean(source.procuring_body)),
        ("references", _clean(", ".join(source.reference_numbers))),
        ("published", _format_instant(source.published_at)),
        (
            "deadline",
            _clean(
                " ".join(
                    part
                    for part in (
                        _format_instant(source.deadline_at),
                        f"(stated as {source.deadline_timezone})"
                        if _clean(source.deadline_timezone)
                        else "",
                    )
                    if part
                )
            ),
        ),
        ("scope", _clean(source.scope)),
        (
            "requirements",
            _clean("; ".join(source.requirements_hints)),
        ),
        ("body", _clean(source.body_text)),
    ]
    return [
        (f"{_LABELS[key]}: {text}", text)
        for key, text in blocks
        if text
    ]


def notice_is_usable(sections: list[tuple[str, str]]) -> bool:
    """Whether these sections carry enough substance to assess from.

    A bare title is not an assessment input: it cannot support a requirement, and pretending
    otherwise would produce a verdict with no citable evidence and a confidence that looks
    ordinary. Anything beyond the title counts.
    """
    return sum(1 for label, _text in sections if not label.startswith(f"{_LABELS['title']}:")) >= 1


def build_notice_text(sections: list[tuple[str, str]]) -> str:
    """Render the labelled sections as one plain-text document body.

    Kept in step with :attr:`NOTICE_METHOD`: the full text is what a reader would open the file
    to see, and the sections are what Stage B cites.
    """
    return "\n\n".join(label for label, _text in sections)


def notice_extraction(
    source: NoticeSource,
    *,
    tender_id: int,
    correlation_id: str | None = None,
) -> DocumentExtraction | None:
    """Build the synthetic notice document, or ``None`` when the source published too little.

    Returns ``None`` rather than a thin document on purpose: an empty-bodied "notice" would
    satisfy the "there is a document" check in Stage B while providing nothing to cite, which is
    the exact failure this module exists to prevent. In that case the tender keeps its existing
    behaviour.
    """
    blocks = notice_sections(source)
    if not notice_is_usable(blocks):
        return None

    text = build_notice_text(blocks)
    language = source.language
    if not language:
        detected, _confidence = detect_language(text)
        language = detected

    sections = [
        ExtractedSection(
            index=index,
            kind="paragraph",
            text=label,
            style=None,
            heading_level=None,
            language=language,
        )
        for index, (label, _text) in enumerate(blocks, start=1)
    ]
    return DocumentExtraction(
        document_id=NOTICE_DOCUMENT_ID,
        tender_id=tender_id,
        filename=NOTICE_FILENAME,
        source_url=source.source_url,
        extraction_status="extracted",
        extraction_method=NOTICE_METHOD,
        text=text,
        mime_type="text/plain",
        checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        storage_path=None,
        language=language,
        pages=[],
        sections=sections,
        # The declared metadata of a downloaded file's container. A notice has none: it is text
        # the source wrote, not a document it served.
        document_metadata={NOTICE_SOURCE_KIND: "1"},
        metadata=_notice_metadata(correlation_id),
    )


def _notice_metadata(correlation_id: str | None) -> ProcessingMetadata:
    return ProcessingMetadata(
        processor=PROCESSOR_NAME,
        processor_version=PROCESSOR_VERSION,
        processed_at=datetime.now(UTC).isoformat(),
        correlation_id=correlation_id,
    )


def is_notice(document: DocumentExtraction) -> bool:
    """Whether *document* is the synthetic notice rather than a downloaded file."""
    return document.document_metadata.get(NOTICE_SOURCE_KIND) == "1"


def notice_fingerprint(document: DocumentExtraction) -> str:
    """Content fingerprint for the notice, matching how real extractions are keyed."""
    return content_fingerprint(
        {
            "filename": document.filename,
            "extraction_method": document.extraction_method,
            "language": document.language,
            "text": document.text,
            "sections": [section.to_dict() for section in document.sections],
        }
    )

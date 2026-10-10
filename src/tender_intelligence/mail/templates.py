"""Deterministic tender and update notification templates (docs/08 §8.14–§8.16).

The renderer is pure: it never loads a verdict, calls a model, or chooses a recipient.  It
copies the supplied recommendation and evidence into the fixed section structure so the email
cannot silently reinterpret an AI result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tender_intelligence.notifications.contracts import NotificationKind
from tender_intelligence.notifications.test_mode import TEST_PREFIX, has_test_prefix

WAT = ZoneInfo("Africa/Lagos")
_DEADLINE_FMT = "%Y-%m-%d %H:%M %Z"


@dataclass(frozen=True)
class LinkedDocument:
    """One secure expiring link plus display metadata."""

    filename: str
    url: str
    expires_at: datetime
    expiry_days: int


@dataclass(frozen=True)
class NotificationContent:
    """Provider-neutral template input.

    ``requirements_summary`` and ``gap_analysis`` accept the string lists used by the current
    ORM contract and the structured dictionaries an eventual Prompt 14 formatter may emit.
    Values are rendered as evidence-preserving text; the notification layer never derives a
    verdict from them.
    """

    kind: NotificationKind = NotificationKind.NEW
    test_mode: bool = False
    source_name: str = ""
    title: str = ""
    urgent: bool = False
    verdict: str = "APPLY"
    confidence: float | None = None
    background_summary: str = ""
    requirements_summary: tuple[Any, ...] | list[Any] = ()
    gap_analysis: tuple[Any, ...] | list[Any] = ()
    deadline: datetime | None = None
    deadline_timezone: str | None = None
    incomplete_inputs: bool = False
    attached_filenames: tuple[str, ...] = ()
    linked_documents: tuple[LinkedDocument, ...] = ()
    failed_filenames: tuple[str, ...] = ()
    skipped_filenames: tuple[str, ...] = ()
    update_summary: str = ""
    previous_assessment_date: str = ""
    verdict_changed: bool | None = None
    requirements_label: str = "Tender"
    #: True when Stage B produced no verdict (v1.1 §5.6). Drives the "automatic assessment
    #: unavailable" banner and suppresses the verdict-specific sections, so a raw notice can
    #: never be mistaken for an assessment.
    assessment_unavailable: bool = False
    #: Machine-readable reason, shown verbatim so the failure is diagnosable from the email.
    unavailability_reason: str | None = None
    #: True when an assessment *was* produced but from the tender's published notice alone,
    #: because no downloadable document was offered. Distinct from
    #: ``assessment_unavailable``: there is a verdict here, and saying otherwise would be as
    #: misleading as claiming a document was read. Renders the "No Downloadable source files"
    #: caveat so the reader knows what the assessment rests on.
    notice_only_assessment: bool = False


def base_subject(content: NotificationContent) -> str:
    """Return the subject without Test Mode/urgency markers."""

    source = content.source_name.strip() or "SOURCE"
    # Truncate title to keep subject under ~100 characters total
    # Format: "SOURCE Expression of Interest – Assessment Report: TITLE"
    # Allow ~60 chars for title to keep total reasonable
    title = content.title.strip()
    if len(title) > 60:
        title = title[:57] + "..."

    if content.kind == NotificationKind.RAW_NOTICE:
        return f"{source} RAW NOTICE (assessment unavailable): {title}"
    if content.kind == NotificationKind.UPDATE:
        changed = content.update_summary.strip()
        tail = f": {changed}" if changed else ""
        return f"{source} UPDATE – {title}{tail}"
    return f"{source} Expression of Interest – Assessment Report: {title}"


def subject(content: NotificationContent) -> str:
    """Compose ``[TEST]`` and ``URGENT`` exactly once, in that order."""

    base = base_subject(content)
    markers: list[str] = []
    if content.test_mode and not has_test_prefix(base):
        markers.append(TEST_PREFIX)
    if content.urgent:
        markers.append("URGENT")
    return f"{' '.join(markers)} {base}" if markers else base


def _zone(value: str | None) -> ZoneInfo | None:
    if not value:
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def format_deadline(deadline: datetime, source_timezone: str | None) -> str:
    """Render the exact instant in WAT and, when supplied, the notice's named timezone.

    The database stores the instant in UTC and the source timezone separately.  A naive value
    is treated as UTC, matching the pipeline's persisted deadline contract.  We never label a
    UTC clock value with an unrelated source zone.
    """

    aware = deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=UTC)
    wat = aware.astimezone(WAT)
    parts = [f"Deadline: {wat.strftime(_DEADLINE_FMT)} (WAT)"]
    zone = _zone(source_timezone)
    if zone is not None:
        local = aware.astimezone(zone)
        parts.append(f"stated in notice: {local.strftime(_DEADLINE_FMT)} ({source_timezone})")
    elif source_timezone:
        # Preserve an unknown source label rather than silently dropping it; do not claim the
        # UTC value is in that zone.
        parts.append(f"source timezone label: {source_timezone}")
    return " · ".join(parts)


def _text_value(value: Any) -> str:
    """Render a verdict artefact without discarding evidence fields."""

    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, dict):
        # Prefer human-readable fields, but retain every other key/value as labelled evidence.
        # A structured verdict item must not lose a citation, status, or qualification merely
        # because it also contains a field named ``summary``.
        preferred = (
            "text",
            "summary",
            "description",
            "analysis",
            "status",
            "result",
            "conclusion",
        )
        ordered_keys = [key for key in preferred if key in value]
        ordered_keys.extend(
            sorted((key for key in value if key not in preferred), key=lambda key: str(key))
        )
        chunks: list[str] = []
        for key in ordered_keys:
            item = value[key]
            rendered = _text_value(item)
            if not rendered:
                continue
            if key in preferred and isinstance(item, str):
                chunks.append(rendered)
            else:
                chunks.append(f"{key}: {rendered}")
        return "; ".join(chunks)
    if isinstance(value, (list, tuple)):
        return "; ".join(part for part in (_text_value(item) for item in value) if part)
    return str(value)


def _items(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(text for text in (_text_value(value) for value in values) if text)


def _confidence(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{max(0.0, min(1.0, float(value))):.0%}"


#: Requirement statuses as a human reads them. docs/07 §7.3: an uncited claim is
#: downgraded to "unverified", which is a statement about our records, not a claim
#: that the company lacks the capability.
_STATUS_LABELS = {
    "met": "Met",
    "partially_met": "Partially met",
    "not_met": "Not met",
    "unverified": "Unverified - no company evidence on file",
}


def _status_label(value: Any) -> str:
    text = _text_value(value).strip()
    return _STATUS_LABELS.get(text.casefold(), text) or _STATUS_LABELS["unverified"]


def _is_assessment(item: Mapping[str, Any]) -> bool:
    """True when a mapping is a verdict assessment rather than free-form evidence.

    Section 3 reports applicability per requirement. A mapping that carries no
    requirement and no status is not an assessment, so it belongs to Section 2 only -
    repeating it in Section 3 would duplicate the text without adding evidence.
    """

    return "requirement" in item or "status" in item


def _requirement_blocks(items: Iterable[Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Section 2 blocks: what the tender requires, quoted from the tender itself."""

    blocks: list[tuple[str, tuple[str, ...]]] = []
    for item in items:
        if not isinstance(item, Mapping) or not _is_assessment(item):
            # Not a verdict assessment. Render it whole rather than dropping any of
            # its fields - a structured requirement must never lose a citation.
            rendered = _text_value(item).strip()
            if rendered:
                blocks.append((rendered, ()))
            continue
        requirement = _text_value(item.get("requirement")).strip()
        if not requirement:
            continue
        details: list[str] = []
        for citation in item.get("tender_evidence") or ():
            if not isinstance(citation, Mapping):
                rendered = _text_value(citation).strip()
                if rendered:
                    details.append(rendered)
                continue
            quote = _text_value(citation.get("quote")).strip()
            location = _text_value(citation.get("location")).strip()
            document_id = citation.get("document_id")
            where = " ".join(
                part
                for part in (
                    f"document {document_id}" if document_id not in (None, "") else "",
                    location,
                )
                if part
            )
            if quote and where:
                details.append(f'Stated in the tender: "{quote}" ({where})')
            elif quote:
                details.append(f'Stated in the tender: "{quote}"')
            elif where:
                details.append(f"Stated in the tender at {where}")
        blocks.append((requirement, tuple(details)))
    return tuple(blocks)


def _company_evidence_lines(citations: Any) -> tuple[str, ...]:
    """Knowledge-base citations, each labelled with the section it came from."""

    if not isinstance(citations, (list, tuple)):
        return ()
    lines: list[str] = []
    for citation in citations:
        if not isinstance(citation, Mapping):
            rendered = _text_value(citation).strip()
            if rendered:
                lines.append(rendered)
            continue
        quote = _text_value(citation.get("quote")).strip()
        section = _text_value(citation.get("section")).strip()
        if quote and section:
            lines.append(f'Company evidence (KB "{section}"): "{quote}"')
        elif quote:
            lines.append(f'Company evidence: "{quote}"')
        elif section:
            lines.append(f"Company evidence (KB): {section}")
    return tuple(lines)


def _applicability_blocks(
    items: Iterable[Any], fallback: Iterable[Any]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Section 3 blocks: requirement-by-requirement evidence (docs/07 §7.2, §7.3).

    ``fallback`` carries the model's standalone gap strings. They are appended only when
    a gap is not already attributed to a requirement, so nothing is shown twice.
    """

    blocks: list[tuple[str, tuple[str, ...]]] = []
    claimed: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or not _is_assessment(item):
            continue
        requirement = _text_value(item.get("requirement")).strip() or "(unnamed requirement)"
        details: list[str] = []
        assessment = _text_value(item.get("assessment")).strip()
        if assessment:
            details.append(f"Assessment: {assessment}")
        details.extend(_company_evidence_lines(item.get("company_evidence")))
        gap = _text_value(item.get("gap")).strip()
        if gap:
            details.append(f"Gap: {gap}")
            claimed.add(gap)
        blocks.append((f"{requirement} - {_status_label(item.get('status'))}", tuple(details)))

    if not blocks:
        return tuple(("", (rendered,)) for rendered in _items(fallback) if rendered)

    extra: list[str] = []
    for item in fallback:
        raw = item.get("gap") if isinstance(item, Mapping) else item
        value = _text_value(raw).strip()
        if value and value not in claimed:
            extra.append(value)
    if extra:
        blocks.append(("Other gaps reported", tuple(extra)))
    return tuple(blocks)


def _render_blocks_text(
    blocks: tuple[tuple[str, tuple[str, ...]], ...], empty: str
) -> list[str]:
    if not blocks:
        return [f"- {empty}"]
    lines: list[str] = []
    for title, details in blocks:
        lines.append(f"- {title}" if title else "-")
        lines.extend(f"    {detail}" for detail in details)
    return lines


def _render_blocks_html(
    blocks: tuple[tuple[str, tuple[str, ...]], ...], empty: str
) -> str:
    if not blocks:
        return f"<ul><li>{escape(empty)}</li></ul>"
    items: list[str] = []
    for title, details in blocks:
        parts = [escape(title)] if title else []
        parts.extend(escape(detail) for detail in details)
        items.append(f"<li>{'<br>'.join(parts)}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _update_lines(content: NotificationContent) -> list[str]:
    if content.kind != NotificationKind.UPDATE:
        return []
    lines = [f"What changed: {content.update_summary.strip() or 'Tender listing changed.'}"]
    if content.deadline is not None:
        lines.append(
            f"Current deadline: {format_deadline(content.deadline, content.deadline_timezone)}"
        )
    if content.verdict_changed is True:
        lines.append("Does this change our verdict? Yes; a material re-evaluation was required.")
    elif content.verdict_changed is False:
        lines.append("Does this change our verdict? No; the existing verdict remains applicable.")
    else:
        lines.append(
            "Does this change our verdict? The change classification did not require re-evaluation."
        )
    if content.previous_assessment_date:
        lines.append(
            f"The earlier assessment (of {content.previous_assessment_date}) remains valid."
        )
    return lines


def _notes_footer(content: NotificationContent) -> list[str]:
    notes: list[str] = []
    if content.attached_filenames:
        notes.append(f"Documents attached: {', '.join(content.attached_filenames)}")
    if content.linked_documents:
        linked: list[str] = []
        for link in content.linked_documents:
            expires = link.expires_at.astimezone(WAT).strftime("%Y-%m-%d")
            linked.append(f"{link.filename} (expires {expires}): {link.url}")
        notes.append("Documents as links: " + ", ".join(linked))
    if content.failed_filenames:
        notes.append(
            "Documents that failed to download/extract: " + ", ".join(content.failed_filenames)
        )
    if content.skipped_filenames:
        notes.append("Documents skipped by processing: " + ", ".join(content.skipped_filenames))
    if content.incomplete_inputs:
        notes.append("Note: the verdict was reached on incomplete inputs. incomplete_inputs=true")
    if content.notice_only_assessment and not content.assessment_unavailable:
        notes.append(
            "Note: no downloadable source files were available; this assessment used the "
            "tender's published notice only"
        )
    return notes


#: Banner shown when Stage B produced no verdict (v1.1 §5.6 requires the raw notice to carry a
#: clear "automatic assessment unavailable" statement so the mail is not read as an assessment).
ASSESSMENT_UNAVAILABLE_BANNER = "AUTOMATIC ASSESSMENT UNAVAILABLE"

#: Shown when a verdict exists but rests on the tender's published notice because the source
#: offered no downloadable document. Stated up front, before any requirement, because a reader
#: who reaches Section 3 has already begun trusting the analysis — the caveat has to arrive
#: before the reasoning it qualifies, not after it.
NO_DOWNLOADABLE_SOURCES_BANNER = "NO DOWNLOADABLE SOURCE FILES"


def _notice_only_notice(content: NotificationContent) -> list[str]:
    """The lines describing a notice-only assessment, or ``[]`` when it does not apply."""
    if not content.notice_only_assessment or content.assessment_unavailable:
        return []
    return [
        NO_DOWNLOADABLE_SOURCES_BANNER,
        "This assessment is based only on the tender's published notice — the tender document "
        "was not available for download. Eligibility, qualification and scoring criteria stated "
        "only in that document have not been assessed, and the confidence below is reduced to "
        "reflect this.",
    ]


def _unavailability_reason(content: NotificationContent) -> str:
    """One sentence naming why the assessment is missing.

    The machine-readable reason from the pipeline is shown verbatim when present, because that
    is what makes the failure diagnosable from the inbox; otherwise a neutral fallback keeps the
    banner from being empty.
    """
    reason = (content.unavailability_reason or "").strip()
    if not reason:
        return (
            "The automated assessment could not be completed for this tender. "
            "The source notice is provided below for manual review."
        )
    return (
        f"The automated assessment could not be completed ({reason}). "
        "The source notice is provided below for manual review."
    )


def render_text(content: NotificationContent) -> str:
    """Render the substantive notification in deterministic plain text."""

    lines: list[str] = [f"Subject: {subject(content)}", ""]
    if content.assessment_unavailable:
        lines.append(ASSESSMENT_UNAVAILABLE_BANNER)
        lines.append(_unavailability_reason(content))
        lines.append("")
    notice_only_lines = _notice_only_notice(content)
    if notice_only_lines:
        lines.extend(notice_only_lines)
        lines.append("")
    lines.append("1. Background")
    lines.append(content.background_summary.strip() or "(no background summary available)")
    lines.append("")

    lines.append(f"2. Requirements for {content.requirements_label}")
    lines.extend(
        _render_blocks_text(
            _requirement_blocks(content.requirements_summary),
            "(no requirements summary available)",
        )
    )
    if content.deadline is not None:
        lines.append(format_deadline(content.deadline, content.deadline_timezone))
    lines.append("")

    lines.append("3. Why We Can / Cannot Apply")
    lines.extend(
        _render_blocks_text(
            _applicability_blocks(content.requirements_summary, content.gap_analysis),
            "(no comparison analysis available)",
        )
    )
    if content.assessment_unavailable:
        # Say plainly that there is no verdict. Printing an empty recommendation here would
        # read as a formatting glitch rather than a failure the reader must act on.
        lines.append("Final verdict: NOT ASSESSED (automatic assessment unavailable)")
    else:
        lines.append(
            f"Final verdict: {content.verdict} (confidence {_confidence(content.confidence)})"
        )

    update_lines = _update_lines(content)
    if update_lines:
        lines.append("")
        lines.extend(update_lines)
    notes = _notes_footer(content)
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in notes)
    return "\n".join(lines).rstrip() + "\n"


def _safe_link(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _notes_footer_html(content: NotificationContent) -> str:
    items: list[str] = []
    if content.attached_filenames:
        items.append(
            f"<li>Documents attached: {escape(', '.join(content.attached_filenames))}</li>"
        )
    for link in content.linked_documents:
        safe_url = _safe_link(link.url)
        expires = link.expires_at.astimezone(WAT).strftime("%Y-%m-%d")
        label = escape(f"{link.filename} (expires {expires})")
        if safe_url:
            items.append(
                f"<li>Documents as links: {label}: "
                f'<a href="{escape(safe_url, quote=True)}">secure download</a></li>'
            )
        else:
            items.append(f"<li>Documents as links: {label}</li>")
    if content.failed_filenames:
        items.append(
            "<li>Documents that failed to download/extract: "
            f"{escape(', '.join(content.failed_filenames))}</li>"
        )
    if content.skipped_filenames:
        items.append(
            "<li>Documents skipped by processing: "
            f"{escape(', '.join(content.skipped_filenames))}</li>"
        )
    if content.incomplete_inputs:
        items.append(
            "<li>Note: the verdict was reached on incomplete inputs. incomplete_inputs=true</li>"
        )
    if content.notice_only_assessment and not content.assessment_unavailable:
        items.append(
            "<li>Note: no downloadable source files were available; this assessment used the "
            "tender's published notice only</li>"
        )
    return "".join(items)


def render_html(content: NotificationContent) -> str:
    """Render an escaped HTML equivalent; the plain-text body remains authoritative."""

    background = content.background_summary.strip() or "(no background summary available)"
    body: list[str] = [f"<h1>{escape(subject(content))}</h1>"]
    if content.assessment_unavailable:
        body.append(
            '<p style="background:#fff3cd;border:1px solid #ffe08a;padding:12px;'
            'font-weight:bold;">'
            f"{escape(ASSESSMENT_UNAVAILABLE_BANNER)}</p>"
        )
        body.append(f"<p>{escape(_unavailability_reason(content))}</p>")
    notice_only_lines = _notice_only_notice(content)
    if notice_only_lines:
        body.append(
            '<p style="background:#e8f4fd;border:1px solid #9ec9e8;padding:12px;'
            'font-weight:bold;">'
            f"{escape(notice_only_lines[0])}</p>"
        )
        body.append(f"<p>{escape(' '.join(notice_only_lines[1:]))}</p>")
    body.extend(
        [
            "<h2>1. Background</h2>",
            f"<p>{escape(background)}</p>",
            f"<h2>2. Requirements for {escape(content.requirements_label)}</h2>",
            _render_blocks_html(
                _requirement_blocks(content.requirements_summary),
                "(no requirements summary available)",
            ),
        ]
    )
    if content.deadline is not None:
        deadline = format_deadline(content.deadline, content.deadline_timezone)
        body.append(f"<p><strong>{escape(deadline)}</strong></p>")
    confidence = _confidence(content.confidence)
    if content.assessment_unavailable:
        verdict_html = (
            "<p><strong>Final verdict: NOT ASSESSED "
            "(automatic assessment unavailable)</strong></p>"
        )
    else:
        verdict_html = (
            f"<p><strong>Final verdict: {escape(content.verdict)}</strong> "
            f"(confidence {confidence})</p>"
        )
    body.extend(
        [
            "<h2>3. Why We Can / Cannot Apply</h2>",
            _render_blocks_html(
                _applicability_blocks(content.requirements_summary, content.gap_analysis),
                "(no comparison analysis available)",
            ),
            verdict_html,
        ]
    )
    update_lines = _update_lines(content)
    if update_lines:
        body.append("<h2>Tender update</h2>")
        body.append("<ul>" + "".join(f"<li>{escape(line)}</li>" for line in update_lines) + "</ul>")
    notes_html = _notes_footer_html(content)
    if notes_html:
        body.append(f"<h2>Notes</h2><ul>{notes_html}</ul>")
    return "<html><body>" + "".join(body) + "</body></html>"


__all__ = [
    "LinkedDocument",
    "NotificationContent",
    "NotificationKind",
    "WAT",
    "base_subject",
    "format_deadline",
    "render_html",
    "render_text",
    "subject",
]

"""Deterministic tender and update notification templates (docs/08 §8.14–§8.16).

The renderer is pure: it never loads a verdict, calls a model, or chooses a recipient.  It
copies the supplied recommendation and evidence into the fixed section structure so the email
cannot silently reinterpret an AI result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html import escape
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tender_intelligence.notifications.test_mode import TEST_PREFIX, has_test_prefix

WAT = ZoneInfo("Africa/Lagos")
_DEADLINE_FMT = "%Y-%m-%d %H:%M %Z"


class NotificationKind(StrEnum):
    """Whether this notification reports a new tender or an update."""

    NEW = "new"
    UPDATE = "update"


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


def base_subject(content: NotificationContent) -> str:
    """Return the subject without Test Mode/urgency markers."""

    source = content.source_name.strip() or "SOURCE"
    if content.kind == NotificationKind.UPDATE:
        changed = content.update_summary.strip()
        tail = f": {changed}" if changed else ""
        return f"{source} UPDATE – {content.title}{tail}"
    return f"{source} Expression of Interest – Assessment Report: {content.title}"


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
    return notes


def render_text(content: NotificationContent) -> str:
    """Render the substantive notification in deterministic plain text."""

    lines: list[str] = [f"Subject: {subject(content)}", ""]
    lines.append("1. Background")
    lines.append(content.background_summary.strip() or "(no background summary available)")
    lines.append("")

    lines.append(f"2. Requirements for {content.requirements_label}")
    requirements = _items(content.requirements_summary)
    lines.extend(f"- {item}" for item in requirements or ("(no requirements summary available)",))
    if content.deadline is not None:
        lines.append(format_deadline(content.deadline, content.deadline_timezone))
    lines.append("")

    lines.append("3. Why We Can / Cannot Apply")
    gaps = _items(content.gap_analysis)
    lines.extend(f"- {item}" for item in gaps or ("(no comparison analysis available)",))
    lines.append(f"Final verdict: {content.verdict} (confidence {_confidence(content.confidence)})")

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
    return "".join(items)


def _ul(items: Iterable[Any]) -> str:
    values = _items(items)
    if not values:
        values = ("(no items available)",)
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in values) + "</ul>"


def render_html(content: NotificationContent) -> str:
    """Render an escaped HTML equivalent; the plain-text body remains authoritative."""

    background = content.background_summary.strip() or "(no background summary available)"
    body: list[str] = [
        f"<h1>{escape(subject(content))}</h1>",
        "<h2>1. Background</h2>",
        f"<p>{escape(background)}</p>",
        f"<h2>2. Requirements for {escape(content.requirements_label)}</h2>",
        _ul(content.requirements_summary),
    ]
    if content.deadline is not None:
        deadline = format_deadline(content.deadline, content.deadline_timezone)
        body.append(f"<p><strong>{escape(deadline)}</strong></p>")
    confidence = _confidence(content.confidence)
    body.extend(
        [
            "<h2>3. Why We Can / Cannot Apply</h2>",
            _ul(content.gap_analysis),
            f"<p><strong>Final verdict: {escape(content.verdict)}</strong> "
            f"(confidence {confidence})</p>",
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

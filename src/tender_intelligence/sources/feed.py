""":mod:`tender_intelligence.sources.feed` — RSS/Atom syndication-feed source strategy.

docs/05 §5.1 names "RSS/Atom" as a source type. A feed is the simplest acquisition shape a
procurement portal can offer and the easiest to get wrong: the format is *syndication*, not
*tender data*. The elements that reliably exist are a title, a link and a date; a reference
number, a procuring entity, a scope, requirements and a closing date usually do not, and a
deadline guessed from a feed summary is worse than a deadline that is honestly absent
(docs/05 §5.2, prompt 07 timezone rule). This adapter therefore reports what the feed
actually carries and leaves the rest to the detail page.

Three formats are read, because portals publish all three and none of them needs a different
dependency: RSS 2.0 (``<rss><channel><item>``), RSS 1.0 / RDF (``<rdf:RDF><item>``) and
Atom (``<feed><entry>``). Parsing uses :mod:`xml.etree.ElementTree` from the standard library
and is namespace-insensitive, so a feed that declares a default namespace, a prefixed one, or
none at all is read the same way. No feed-specific library is added for that.

**Entry names are configuration, not code.** Which element carries the reference, and which
carries the deadline, are ``parser_config`` facts. Nothing in this file knows a portal.

**Detail pages are HTML.** A feed entry links to a page; that page is parsed by exactly the
same code a paginated source uses, which is why this adapter shares the HTML detail machinery
rather than reimplementing it. It remembers the URL it discovered for each
``external_id`` and prefers it over ``detail_url_template`` — feed identities are often GUIDs
or URNs that no numeric URL template can accept.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.core.errors import PARSER_MISMATCH
from tender_intelligence.interfaces.source import SourceError, SourceType, TenderListing
from tender_intelligence.sources.normalize import (
    RESPONSE_KIND_FEED,
    absolute_url,
    clean_text,
    dedupe_listings,
    derive_external_id,
    parse_timestamp,
    with_metadata,
)
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import Fetcher
from tender_intelligence.sources.waho import PaginatedHtmlAdapter

log = logging.getLogger("tender_intelligence.sources.feed")

#: Element local names denoting one syndication entry, across RSS 2.0, RSS 1.0 and Atom.
_ENTRY_TAGS = frozenset({"item", "entry"})

#: Element local names holding the entry's own identifier, most specific first.
_ID_TAGS = ("id", "guid")

#: Element local names holding a publication date, in the order they are trusted.
_DATE_TAGS = ("pubdate", "published", "updated", "date", "modified")

#: Element local names whose text is worth keeping as the entry's body text.
_BODY_TAGS = ("description", "summary", "content", "encoded")

#: Entries kept per feed when the source sets no explicit ``max_items``. A feed is one
#: response, so there is no page cap to stop it; this bound exists so a malformed feed
#: advertising a hundred thousand entries cannot exhaust memory. Set ``max_items`` to 0 for
#: no limit.
_DEFAULT_MAX_ITEMS = 200

#: Longest entry body text kept in ``raw_metadata``.
_SUMMARY_MAX_CHARS = 2000

#: Tags stripped from feed text that carries escaped markup (``type="html"`` titles).
_TAG_RE = re.compile(r"<[^>]+>")


class FeedAdapter(PaginatedHtmlAdapter):
    """Adapter for a source whose tenders arrive as an RSS, RDF or Atom feed.

    Listing discovery is feed-native XML; detail and attachment discovery is inherited HTML
    parsing, pointed at the URL each entry linked to. ``list_new_tenders`` is replaced
    wholesale, so none of the listing-page behaviour of the parent applies.
    """

    source_type: str = SourceType.RSS_ATOM.value

    def __init__(
        self,
        feed_url: str,
        *,
        base_url: str | None = None,
        parser_config: dict[str, Any] | None = None,
        policy: CrawlPolicy | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        super().__init__(
            feed_url,
            base_url=base_url,
            parser_config=parser_config,
            policy=policy,
            fetcher=fetcher,
        )
        # Deadline extraction from a feed's prose is opt-in: the inherited HTML patterns
        # default to one portal's wording and must not be applied to unrelated summaries.
        self._raw_config: dict[str, Any] = dict(parser_config or {})
        self._url_by_id: dict[str, str] = {}

    # -- Discovery ------------------------------------------------------------

    def list_new_tenders(self) -> list[TenderListing]:
        """Fetch the feed once and return one normalised listing per unique entry."""
        correlation_id = get_correlation_id()
        url = self._listing_url
        log.info(
            "fetching syndication feed %s",
            url,
            extra={"stage": "discovery", "status": "fetch", "correlation_id": correlation_id},
        )
        response = self._fetch(url)
        entries = _parse_feed(response.text, url)
        if not entries and not self._config.get("allow_empty_listing"):
            raise SourceError(
                "feed contains no entries; the URL may not be a feed",
                error_code=PARSER_MISMATCH,
                context={"url": url, "correlation_id": correlation_id},
            )

        limit = self._max_items()
        selected = entries[:limit] if limit else entries
        if limit and len(entries) > limit:
            log.info(
                "feed carried %d entries; keeping the first %d per max_items",
                len(entries),
                limit,
                extra={"stage": "discovery", "status": "warning"},
            )

        listings: list[TenderListing] = []
        failures = 0
        for entry in selected:
            try:
                listing = self._entry_to_listing(entry, response.final_url)
            except ValueError as exc:
                failures += 1
                log.warning(
                    "skipping malformed feed entry: %s",
                    exc,
                    extra={"stage": "discovery", "status": "warning"},
                )
                continue
            if listing is not None:
                listings.append(listing)
        if not listings and failures:
            raise SourceError(
                "feed entries found but none parsed; the feed structure may have changed",
                error_code=PARSER_MISMATCH,
                context={"entries_seen": len(selected), "entries_parsed": 0},
            )

        unique, duplicates = dedupe_listings(listings)
        log.info(
            "feed discovery complete: %d entr(y/ies) -> %d candidate(s), %d duplicate(s)",
            len(selected),
            len(unique),
            duplicates,
            extra={"stage": "discovery", "status": "done", "correlation_id": correlation_id},
        )
        return unique

    def _entry_to_listing(self, entry: ET.Element, feed_url: str) -> TenderListing | None:
        """Map one feed entry onto a :class:`TenderListing`, or ``None`` if it is unusable."""
        title = _text_of(entry, "title")
        link = self._entry_link(entry, feed_url)
        raw_id = _first_text(entry, _ID_TAGS)
        external_id, identity_source = self._identity(raw_id, link)
        if not title:
            raise ValueError("entry has no title")
        if not external_id:
            raise ValueError("entry has neither a usable identifier nor a link")
        if not link:
            raise ValueError("entry has no alternate link to a detail page")

        body = _body_text(entry)
        published_at, published_raw = _entry_timestamp(entry)
        deadline_at, deadline_tz, deadline_raw = self._entry_deadline(title, body)

        raw_metadata: dict[str, Any] = {"feed_url": feed_url}
        if raw_id:
            raw_metadata["feed_id"] = raw_id
        if published_raw:
            raw_metadata["published_raw"] = published_raw
        if deadline_raw:
            raw_metadata["deadline_raw"] = deadline_raw
        if self._include_summary() and body:
            raw_metadata["summary"] = body[:_SUMMARY_MAX_CHARS]

        self._url_by_id[external_id] = link
        return with_metadata(
            TenderListing(
                external_id=external_id,
                title=title,
                url=link,
                published_at=published_at,
                deadline_at=deadline_at,
                deadline_timezone=deadline_tz,
                raw_metadata=raw_metadata,
            ),
            response_kind=RESPONSE_KIND_FEED,
            discovery_url=feed_url,
            identity_source=identity_source,
        )

    def _entry_link(self, entry: ET.Element, feed_url: str) -> str:
        """The entry's alternate link, resolved against the feed URL.

        RSS carries the URL as element text; Atom carries it in a ``href`` attribute and marks
        self/enclosure links with ``rel``. A link whose ``rel`` is not the configured one is
        not the detail page, so it is skipped rather than guessed at.
        """
        wanted_rel = str(self._config.get("link_rel") or "alternate").lower()
        fallback = ""
        for element in entry.iter():
            if _local_name(element.tag) != "link":
                continue
            href = clean_text(element.get("href")) or clean_text(element.text)
            if not href:
                continue
            rel = clean_text(element.get("rel")).lower()
            if rel and rel != wanted_rel:
                continue
            resolved = absolute_url(feed_url, href)
            if not resolved:
                continue
            if rel == wanted_rel:
                return resolved
            fallback = fallback or resolved
        return fallback

    def _identity(self, raw_id: str, link: str) -> tuple[str, str]:
        """Resolve the entry's dedupe identity and record which rule produced it.

        A feed that publishes its own identifier is trusted with it: an Atom ``<id>`` is by
        specification permanent, and an RSS ``<guid>`` is the publisher's chosen key. When
        ``id_pattern`` is configured it is applied first, which is how a feed whose
        identifier embeds the tender's own reference number is mapped onto the identity the
        rest of the pipeline already stores.
        """
        pattern = clean_text(self._config.get("id_pattern"))
        if pattern:
            for candidate in (raw_id, link):
                match = re.search(pattern, candidate or "")
                if match:
                    value = match.groupdict().get("id") or match.group(0)
                    if value:
                        return value, f"id_pattern:{pattern}"
        if raw_id:
            return raw_id, "feed_entry_id"
        derived = derive_external_id(link)
        return derived, "derived_from_link" if derived else ""

    def _entry_deadline(
        self, title: str, body: str
    ) -> tuple[datetime | None, str | None, str | None]:
        """A deadline from the entry text, but only where the source configured patterns for it."""
        if "deadline_patterns" not in self._raw_config:
            return None, None, None
        return self._parse_deadline(f"{title} {body}")

    def _max_items(self) -> int:
        raw = self._config.get("max_items", _DEFAULT_MAX_ITEMS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_ITEMS
        return max(value, 0)

    def _include_summary(self) -> bool:
        return bool(self._config.get("include_summary", True))

    # -- Detail discovery (inherited HTML parsing) ----------------------------

    def _detail_url(self, tender_id: str) -> str:
        """Prefer the URL the feed linked to, falling back to the configured template."""
        discovered = self._url_by_id.get(tender_id)
        if discovered:
            return discovered
        return super()._detail_url(tender_id)


# -- feed parsing helpers ---------------------------------------------------


def _local_name(tag: str) -> str:
    """Strip any XML namespace from *tag*, lowercased: ``{ns}pubDate`` -> ``pubdate``."""
    return tag.rsplit("}", 1)[-1].strip().lower() if isinstance(tag, str) else ""


def _parse_feed(xml_text: str, url: str) -> list[ET.Element]:
    """Every syndication entry in *xml_text*, whatever the format or namespace."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(
            "response is not well-formed XML; the URL may not be a feed",
            error_code=PARSER_MISMATCH,
            context={"url": url, "last_error": str(exc)[:200]},
        ) from exc
    entries = [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and _local_name(element.tag) in _ENTRY_TAGS
    ]
    log.debug(
        "feed %s parsed: root=<%s>, %d entry/entries",
        url,
        _local_name(root.tag),
        len(entries),
    )
    return entries


def _text_of(element: ET.Element, local_name: str) -> str:
    """Text of the first direct or nested child with *local_name*, markup removed.

    A feed carries the same content in several shapes: plain text, HTML escaped into a
    ``type="html"`` title, or real XHTML children under ``type="xhtml"``. All three reach the
    pipeline as the same readable string, so titles and summaries are unescaped and stripped
    of tags here instead of leaving every downstream consumer to do it.
    """
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child.tag) == local_name:
            return _strip_markup(_element_text(child))
    return ""


def _first_text(element: ET.Element, local_names: tuple[str, ...]) -> str:
    """Text of the first child matching any of *local_names*, in order."""
    for name in local_names:
        text = _text_of(element, name)
        if text:
            return text
    return ""


def _element_text(element: ET.Element) -> str:
    """Text content of *element*, including any XHTML children, as one normalised string."""
    parts: list[str] = [clean_text(element.text)]
    for child in element:
        if isinstance(child.tag, str):
            parts.append(_element_text(child))
        parts.append(clean_text(child.tail))
    return clean_text(" ".join(part for part in parts if part))


def _body_text(element: ET.Element) -> str:
    """The entry's prose — description, summary or content — whichever it carries."""
    for name in _BODY_TAGS:
        text = _text_of(element, name)
        if text:
            return text
    return ""


def _strip_markup(text: str) -> str:
    """Unescape entities and drop tags, collapsing the leftover whitespace."""
    return clean_text(_TAG_RE.sub(" ", html.unescape(text or "")))


def _entry_timestamp(element: ET.Element) -> tuple[datetime | None, str | None]:
    """The entry's publication date as an aware UTC timestamp, and the raw value behind it."""
    for name in _DATE_TAGS:
        raw = _text_of(element, name)
        if not raw:
            continue
        parsed = parse_timestamp(raw)
        if parsed is not None:
            return parsed.astimezone(UTC), raw
    return None, None

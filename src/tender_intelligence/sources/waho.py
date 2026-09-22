""":mod:`tender_intelligence.sources.waho` — WAHO Paginated HTML list adapter (docs/05 §5.3).

Phase 1 implements only the **discovery** half (``prompts/04-waho-discovery.md``): crawl the
configured listing pages, read the card rows, normalise them into the neutral
:class:`~tender_intelligence.interfaces.source.TenderListing` representation, follow
pagination, and return candidates.

The adapter is **stateless** with respect to seen state: it never queries the tender
database, deduplicates, or persists anything (prompt 04 §3, §18). ``get_detail`` and
``get_attachments`` are out of scope for this prompt and raise explicitly.

Site specifics live in ``parser_config`` (prompt 04 §7, §9), not in business logic. The
defaults below mirror the structure **verified against the live source** (data.wahooas.org,
"WAHO Tenders Platform", loader-independent plain-HTML listing); anything not verified is
reported separately and fails safely via :class:`SourceError` (``parser_mismatch``).
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final

from bs4 import BeautifulSoup, Tag

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_UNREACHABLE
from tender_intelligence.interfaces.source import (
    SourceAdapter,
    SourceError,
    TenderAttachment,
    TenderDetail,
    TenderListing,
)
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import Fetcher, HttpResponse, PoliteHttpClient

log = logging.getLogger("tender_intelligence.sources.waho")

SOURCE_TYPE: Final[str] = "paginated_html_list"

#: Verified: card wrapper per listing row (``div.col-md-6`` containing ``div.card``).
DEFAULT_PARSER_CONFIG: Final[dict[str, Any]] = {
    "row_selector": "div.col-md-6",
    "title_selector": "div.card-header h5 a",
    #: Verified: detail link local path ``/tenders/tenders/{id}/list``.
    "detail_href_pattern": r"/tenders/tenders/(?P<id>\d+)/list",
    #: Verified: next-page marker ``<a rel="next">`` on the listing page.
    "next_page_selector": "a[rel=next]",
    "published_date_pattern": (
        r"Start Date:\s*(?P<raw>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(?P<tz>UTC|GMT)"
    ),
    "deadline_patterns": [
        (
            r"Deadline(?: for submission of applications)?:\s*(?:All proposals must be "
            r"received no later than\s+|at\s+|le\s+)?"
            r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÄÜÖäüöéèàçûê]+)\s+(?P<year>\d{4})"
            r"(?:\s+at\s+(?P<hour>\d{1,2})[.:](?P<minute>\d{2})\s*(?P<ampm>[ap]\.?m\.?))?"
            r"\s*(?P<tz>[A-Z]{2,4})?"
        ),
        (
            r"Date limite(?: de dépôt des candidatures)?\s*:?\s*"
            r"(?:toutes les offres doivent(?: être| etre)? reçues? au plus tard\s+|at\s+|"
            r"le\s+)?"
            r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÄÜÖäüöéèàçûê]+)\s+(?P<year>\d{4})"
            r"(?:\s+at\s+(?P<hour>\d{1,2})[.:](?P<minute>\d{2})\s*(?P<ampm>[ap]\.?m\.?))?"
            r"\s*(?P<tz>[A-Z]{2,4})?"
        ),
    ],
    #: Verified at collection time; operator may override.
    "max_pages": 15,
    "allow_empty_listing": False,
    "expected_languages": ["en", "fr", "pt"],
}

#: Timezone abbreviations that the source has been observed to state, mapped to UTC offsets.
#: ``GMT`` (observed) and ``UTC`` (observed on published dates) are safe; anything else is
#: preserved verbatim and treated as unknown rather than invented (prompt 04 §10).
_TZ_OFFSETS: Final[dict[str, timezone]] = {
    "GMT": UTC,
    "UTC": UTC,
    "WAT": timezone(timedelta(hours=1)),
}

_MONTHS: Final[dict[str, int]] = {
    "january": 1, "janvier": 1, "janeiro": 1, "janv": 1,
    "february": 2, "février": 2, "fevereiro": 2, "feb": 2,
    "march": 3, "mars": 3, "março": 3, "marco": 3,
    "april": 4, "avril": 4, "abril": 4,
    "may": 5, "mai": 5, "maio": 5,
    "june": 6, "juin": 6, "junho": 6,
    "july": 7, "juillet": 7, "julho": 7,
    "august": 8, "août": 8, "aout": 8, "agosto": 8,
    "september": 9, "septembre": 9, "setembro": 9, "sept": 9,
    "october": 10, "octobre": 10, "outubro": 10,
    "november": 11, "novembre": 11, "novembro": 11,
    "december": 12, "décembre": 12, "dezembro": 12,
}

_FRENCH_HINTS: Final[tuple[str, ...]] = (
    "dépôt", "candidatures", "soumission", "offres", "doivent", "plan de passation",
    "organisation", "sera", "seront", "règlement", "évaluation", "récapitulatif",
)
_PORTUGUESE_HINTS: Final[tuple[str, ...]] = (
    "para", "aquisições", "publicação", "órgão", "candidatura", "admissão",
    "proposta", "plano", "compras",
)
_ACCENT_RE: Final[re.Pattern[str]] = re.compile(r"[à-ÿÀ-ßÇç]")


class WahoPaginatedAdapter(SourceAdapter):
    """WAHO tenders platform — Paginated HTML list source (docs/05 §5.3)."""

    source_type: str = SOURCE_TYPE

    def __init__(
        self,
        listing_url: str,
        *,
        base_url: str | None = None,
        parser_config: dict[str, Any] | None = None,
        policy: CrawlPolicy | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self._listing_url = listing_url
        self._base_url = base_url or listing_url
        self._config: dict[str, Any] = {
            **DEFAULT_PARSER_CONFIG,
            **(parser_config or {}),
        }
        self._policy = policy or CrawlPolicy()
        self._fetcher = fetcher or self._default_fetcher()

    def _default_fetcher(self) -> Fetcher:
        client = PoliteHttpClient.build(self._policy)
        return client.get

    # -- Discovery (in scope) -------------------------------------------------

    def list_new_tenders(self) -> list[TenderListing]:
        """Crawl the configured listing pages and return normalised candidates.

        Never consults the database, never deduplicates, never writes, never emails
        (prompt 04 §3, §18).
        """
        correlation_id = get_correlation_id()
        max_pages = int(self._config.get("max_pages") or self._policy.max_pages)
        url: str | None = self._listing_url
        visited: set[str] = set()
        discovered: list[TenderListing] = []
        page_number = 0

        while url and url not in visited and page_number < max_pages:
            visited.add(url)
            page_number += 1
            log.info(
                "fetching listing page %s",
                url,
                extra={
                    "stage": "discovery",
                    "status": "fetch",
                    "correlation_id": correlation_id,
                },
            )
            response = self._fetch(url)
            page_listings, next_url = self._parse_page(response)
            if not page_listings and page_number == 1 and not self._config.get(
                "allow_empty_listing"
            ):
                raise SourceError(
                    "listing page returned no rows; structure may have changed",
                    error_code=PARSER_MISMATCH,
                    context={"url": url, "correlation_id": correlation_id},
                )
            discovered.extend(page_listings)
            url = next_url

        log.info(
            "discovery complete: %d candidates from %d page(s)",
            len(discovered),
            page_number,
            extra={"stage": "discovery", "status": "done", "correlation_id": correlation_id},
        )
        return discovered

    def _fetch(self, url: str) -> HttpResponse:
        try:
            response = self._fetcher(url)
        except SourceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            raise SourceError(
                f"source unreachable: {url}",
                error_code=SOURCE_UNREACHABLE,
                context={"url": url, "last_error": type(exc).__name__},
            ) from exc
        if response.status_code >= 400:
            raise SourceError(
                f"source unreachable: {url} (http {response.status_code})",
                error_code=SOURCE_UNREACHABLE,
                context={"url": url, "status_code": response.status_code},
            )
        return response

    def _parse_page(
        self,
        response: HttpResponse,
    ) -> tuple[list[TenderListing], str | None]:
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select(self._config["row_selector"])
        if not rows:
            return [], self._next_page_url(soup, response.final_url)

        listings: list[TenderListing] = []
        failed = 0
        for row in rows:
            try:
                listing = self._extract_row(row, response.final_url)
                if listing is not None:
                    listings.append(listing)
                else:
                    failed += 1
            except ValueError as exc:
                failed += 1
                log.warning(
                    "skipping malformed listing row: %s",
                    exc,
                    extra={"stage": "discovery", "status": "warning"},
                )
        if listings:
            return listings, self._next_page_url(soup, response.final_url)
        if failed and len(rows) > len(listings):
            raise SourceError(
                "listing rows found but none parsed; parser may be out of date",
                error_code=PARSER_MISMATCH,
                context={"rows_seen": len(rows), "rows_parsed": len(listings)},
            )
        return [], self._next_page_url(soup, response.final_url)

    def _extract_row(self, row: Tag, page_url: str) -> TenderListing | None:
        title_el = row.select_one(self._config["title_selector"])
        if title_el is None:
            return None
        href = str(title_el.get("href", ""))
        match = re.search(self._config["detail_href_pattern"], href)
        if not match:
            log.warning(
                "listing row without a recognised detail link",
                extra={"stage": "discovery", "status": "warning", "href": href},
            )
            return None
        external_id = match.group("id")
        detail_url = urllib.parse.urljoin(page_url, href)
        title = title_el.get_text(" ", strip=True)

        card_text = row.get_text(" ", strip=True)
        published_at, published_raw = self._parse_published(card_text)
        deadline_at, deadline_tz, deadline_raw = self._parse_deadline(card_text)

        raw_metadata: dict[str, Any] = {
            "language": self._guess_language(card_text),
            "reference": self._reference_number(card_text),
            "listing_page_url": page_url,
        }
        if published_raw:
            raw_metadata["published_raw"] = published_raw
        if deadline_raw:
            raw_metadata["deadline_raw"] = deadline_raw

        return TenderListing(
            external_id=external_id,
            title=title,
            url=detail_url,
            published_at=published_at,
            deadline_at=deadline_at,
            deadline_timezone=deadline_tz,
            raw_metadata=raw_metadata,
        )

    def _parse_published(self, card_text: str) -> tuple[datetime | None, str | None]:
        pattern = self._config["published_date_pattern"]
        match = re.search(pattern, card_text)
        if not match:
            return None, None
        raw = match.group("raw")
        tz_name = match.group("tz")
        try:
            naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None, raw
        tz = _TZ_OFFSETS.get(tz_name)
        published = naive.replace(tzinfo=tz or UTC)
        return published, raw

    def _parse_deadline(
        self,
        card_text: str,
    ) -> tuple[datetime | None, str | None, str | None]:
        for pattern in self._config["deadline_patterns"]:
            match = re.search(pattern, card_text)
            if not match:
                continue
            day, month_name, year = (match.group("day"), match.group("month"), match.group("year"))
            month = _MONTHS.get(month_name.lower())
            if month is None:
                log.warning(
                    "deadline uses unrecognised month %r",
                    month_name,
                    extra={"stage": "discovery", "status": "warning"},
                )
                return None, None, match.group(0)
            if match.groupdict().get("hour") is not None:
                hour = int(match.group("hour"))
                minute = int(match.group("minute"))
                if match.groupdict().get("ampm") and match.group("ampm").lstrip().startswith("p"):
                    if hour != 12:
                        hour += 12
                elif match.groupdict().get("ampm") and hour == 12:
                    hour = 0
            else:
                hour = minute = 0
            tz_name = match.groupdict().get("tz")
            tz = _TZ_OFFSETS.get(tz_name) if tz_name else None
            deadline_raw = match.group(0)
            try:
                deadline = datetime(
                    year=int(year), month=month, day=int(day), hour=hour, minute=minute
                )
            except ValueError:
                return None, tz_name or None, deadline_raw
            if tz is not None:
                deadline = deadline.replace(tzinfo=tz).astimezone(UTC)
            return deadline, tz_name or None, deadline_raw
        return None, None, None

    def _next_page_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        selector = self._config["next_page_selector"]
        link = soup.select_one(selector)
        if link is None:
            return None
        href = str(link.get("href") or "")
        if not href:
            return None
        return urllib.parse.urljoin(page_url, href)

    @staticmethod
    def _reference_number(card_text: str) -> str | None:
        match = re.search(r"Reference:\s*([^\s]+)", card_text)
        return match.group(1) if match else None

    def _guess_language(self, card_text: str) -> str:
        lowered = card_text.lower()
        fr_hits = sum(1 for hint in _FRENCH_HINTS if hint in lowered)
        pt_hits = sum(1 for hint in _PORTUGUESE_HINTS if hint in lowered)
        if fr_hits > 0 and fr_hits >= pt_hits:
            return "fr"
        if pt_hits > 0:
            return "pt"
        if _ACCENT_RE.search(card_text):
            return "fr"
        return "en"

    # -- Out of scope for prompt 04 (explicit stubs) ---------------------------

    def get_detail(self, tender_id: str) -> TenderDetail:
        raise NotImplementedError("get_detail is out of scope for prompt 04 (discovery only)")

    def get_attachments(self, tender_id: str) -> list[TenderAttachment]:
        raise NotImplementedError(
            "get_attachments is out of scope for prompt 04 (discovery only)"
        )

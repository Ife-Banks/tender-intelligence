""":mod:`tender_intelligence.sources.waho` — WAHO Paginated HTML list adapter (docs/05 §5.3).

Phase 1 implements the **discovery** half for the WAHO Tenders platform: crawl the
configured listing pages (``prompts/04-waho-discovery.md``) and, per tender, parse the
detail page (``prompts/07-document-discovery.md``) into the neutral
:class:`~tender_intelligence.interfaces.source.TenderDetail` / ``TenderAttachment``
representations.

The adapter is **stateless** with respect to seen state: it never queries the tender
database, deduplicates, or persists anything (prompt 04 §3, §18; prompt 07 §6, §7).
``get_detail``/``get_attachments`` are pure discovery — attachment bytes, checksums and
``Document`` rows belong to prompts 08/09 via the Prompt 06 repository seam.

Site specifics live in ``parser_config`` (prompt 04 §7, §9; prompt 07 §6), not in business
logic. The defaults below encode the structure **established from the project's offline
fixtures** (``tests/fixtures/sources/waho/``). They are **assumptions, not live-verified
facts**: confirming them against the live WAHO site (or overriding them via
``parser_config``) is an open task — see ``docs/04a-waho-discovery-notes.md`` and
``docs/07a-waho-detail-discovery-notes.md`` (prompt 04 §8, prompt 07 §8). Any mismatch fails
safely via :class:`SourceError` (``parser_mismatch``) rather than returning corrupt data.
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

#: ASSUMED from project fixtures, not live-verified (docs/04a-waho-discovery-notes.md):
#: card wrapper per listing row (``div.col-md-6`` containing ``div.card``).
DEFAULT_PARSER_CONFIG: Final[dict[str, Any]] = {
    "row_selector": "div.col-md-6",
    "title_selector": "div.card-header h5 a",
    #: ASSUMED from fixtures: detail link local path ``/tenders/tenders/{id}/list``.
    "detail_href_pattern": r"/tenders/tenders/(?P<id>\d+)/list",
    #: ASSUMED from fixtures: next-page marker ``<a rel="next">`` on the listing page.
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
    #: Technical default; operator may override.
    "max_pages": 15,
    "allow_empty_listing": False,
    "expected_languages": ["en", "fr", "pt"],
    #: ASSUMED from project fixtures (docs/07a-waho-detail-discovery-notes.md): a detail page
    #: reuses the listing card vocabulary — a ``div.card-header`` heading and a
    #: ``div.card-body`` rich-text area (``.trix-content``) of ``<strong>LABEL: value</strong>``
    #: blocks, with document links as Bootstrap buttons/anchors in an attachments area.
    "detail_title_selectors": [
        "div.card-header h1",
        "h1",
        "div.card-header h2",
        "h2",
        "div.card-header h3",
    ],
    "detail_body_selector": "div.card-body div.trix-content",
    "attachment_block_selectors": ["div.attachments", "#attachments"],
    "attachment_path_markers": ["/uploads/", "/documents/", "/files/"],
    "document_extensions": (r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|od[tsp]|rtf|txt|csv)$"),
    "excluded_href_patterns": [
        r"/submissions/",
        r"^javascript:",
        r"^mailto:",
        r"^#",
    ],
    "reference_labels": ["reference", "référence", "referencia", "referência", "ref"],
    "procuring_body_labels": [
        "client",
        "organisation",
        "organization",
        "employer",
        "procuring entity",
        "autorité contractante",
        "autorite contractante",
        "entidade",
        "entidade contratante",
        "órgão",
        "orgao",
    ],
    "scope_labels": [
        "description",
        "object",
        "objet",
        "objetivo",
        "descrição",
        "descricao",
        "background",
        "contexte",
        "contexte et objectifs",
        "purpose",
    ],
    "requirements_labels": [
        "requirements",
        "exigences",
        "critères",
        "critères d'éligibilité",
        "criteres",
        "critérios",
        "criterios",
        "qualifications",
        "eligibility",
        "éligibilité",
        "eligibilidade",
        "requisitos",
        "profile",
    ],
    "advertised_size_pattern": r"(?P<size>[0-9]+(?:[.,][0-9]+)?)\s*(?P<unit>GB|MB|KB|Go|Mo|Ko)",
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
    "january": 1,
    "janvier": 1,
    "janeiro": 1,
    "janv": 1,
    "february": 2,
    "février": 2,
    "fevereiro": 2,
    "feb": 2,
    "march": 3,
    "mars": 3,
    "março": 3,
    "marco": 3,
    "april": 4,
    "avril": 4,
    "abril": 4,
    "may": 5,
    "mai": 5,
    "maio": 5,
    "june": 6,
    "juin": 6,
    "junho": 6,
    "july": 7,
    "juillet": 7,
    "julho": 7,
    "august": 8,
    "août": 8,
    "aout": 8,
    "agosto": 8,
    "september": 9,
    "septembre": 9,
    "setembro": 9,
    "sept": 9,
    "october": 10,
    "octobre": 10,
    "outubro": 10,
    "november": 11,
    "novembre": 11,
    "novembro": 11,
    "december": 12,
    "décembre": 12,
    "dezembro": 12,
}

_FRENCH_HINTS: Final[tuple[str, ...]] = (
    "dépôt",
    "candidatures",
    "soumission",
    "offres",
    "doivent",
    "plan de passation",
    "sera",
    "seront",
    "règlement",
    "évaluation",
    "récapitulatif",
)
_PORTUGUESE_HINTS: Final[tuple[str, ...]] = (
    "para",
    "aquisições",
    "publicação",
    "órgão",
    "candidatura",
    "admissão",
    "proposta",
    "plano",
    "compras",
)
_ACCENT_RE: Final[re.Pattern[str]] = re.compile(r"[à-ÿÀ-ßÇç]")

#: Neutral extension → MIME guess for document links (a *guess* only; the fetcher records
#: the real type after bytes are acquired — docs/06 §6.1).
_DOCUMENT_MIME: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}
_ZIP_MIMES: Final[frozenset[str]] = frozenset(
    {"application/zip", "application/x-zip-compressed", "application/x-zip"}
)

#: Advertised-size suffix → bytes (prompt 07 §2, only when the page states a size).
_SIZE_UNITS: Final[dict[str, int]] = {
    "gb": 1024**3,
    "mb": 1024**2,
    "kb": 1024,
    "go": 1024**3,
    "mo": 1024**2,
    "ko": 1024,
}


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
            if (
                not page_listings
                and page_number == 1
                and not self._config.get("allow_empty_listing")
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
                context={
                    "url": url,
                    "last_error": type(exc).__name__,
                    "correlation_id": get_correlation_id(),
                },
            ) from exc
        if response.status_code >= 400:
            raise SourceError(
                f"source unreachable: {url} (http {response.status_code})",
                error_code=SOURCE_UNREACHABLE,
                context={
                    "url": url,
                    "status_code": response.status_code,
                    "correlation_id": get_correlation_id(),
                },
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
            if tz is None:
                # No timezone evidence ⇒ never emit a naive timestamp (prompt 07 timezone
                # rule; docs/05 §5.2: a deadline is "UTC plus the original timezone string").
                # The original values stay available via deadline_raw / deadline_timezone.
                return None, tz_name or None, deadline_raw
            return deadline.replace(tzinfo=tz).astimezone(UTC), tz_name, deadline_raw
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

    # -- Detail page discovery (prompt 07, in scope) -------------------------

    def get_detail(self, tender_id: str) -> TenderDetail:
        """Fetch and parse the tender's detail page into the neutral ``TenderDetail``.

        Pure discovery (prompt 07 §1, §7): never persists, never downloads attachments,
        never invents a timezone. Parsing quirks live in ``parser_config``.
        """
        url = self._detail_url(tender_id)
        correlation_id = get_correlation_id()
        log.info(
            "fetching detail page for tender %s",
            tender_id,
            extra={"stage": "discovery", "status": "fetch", "correlation_id": correlation_id},
        )
        response = self._fetch(url)
        detail = self._parse_detail(response, tender_id)
        log.info(
            "detail discovery complete for tender %s",
            tender_id,
            extra={
                "stage": "discovery",
                "status": "done",
                "correlation_id": correlation_id,
                "attachment_count": len(detail.attachments),
            },
        )
        return detail

    def get_attachments(self, tender_id: str) -> list[TenderAttachment]:
        """Enumerate every document link on the detail page (prompt 07 §2, §3, §4).

        Returns metadata only — never downloads bytes, never enumerates ZIP contents,
        never fabricates checksums, and keeps every exposed link (including unfamiliar
        extensions).
        """
        url = self._detail_url(tender_id)
        correlation_id = get_correlation_id()
        log.info(
            "fetching detail page for tender %s",
            tender_id,
            extra={"stage": "discovery", "status": "fetch", "correlation_id": correlation_id},
        )
        response = self._fetch(url)
        soup = BeautifulSoup(response.text, "html.parser")
        if self._detail_title(soup) is None:
            raise SourceError(
                "detail page without a recognisable title; structure may have changed",
                error_code=PARSER_MISMATCH,
                context={"tender_id": tender_id, "url": response.final_url},
            )
        attachments = self._parse_attachments(soup, response.final_url, tender_id)
        log.info(
            "attachment discovery complete for tender %s",
            tender_id,
            extra={
                "stage": "discovery",
                "status": "done",
                "correlation_id": correlation_id,
                "attachment_count": len(attachments),
            },
        )
        return attachments

    def _detail_url(self, tender_id: str) -> str:
        url = urllib.parse.urljoin(self._base_url, f"/tenders/tenders/{tender_id}/list")
        return url

    def _parse_detail(self, response: HttpResponse, tender_id: str) -> TenderDetail:
        soup = BeautifulSoup(response.text, "html.parser")
        title = self._detail_title(soup)
        if title is None:
            raise SourceError(
                "detail page without a recognisable title; structure may have changed",
                error_code=PARSER_MISMATCH,
                context={"tender_id": tender_id, "url": response.final_url},
            )
        body_el = soup.select_one(self._config["detail_body_selector"])
        body_text = body_el.get_text(" ", strip=True) if body_el is not None else ""
        blocks = self._label_blocks(body_el) if body_el is not None else []

        published_at, published_raw = self._parse_published(body_text)
        deadline_at, deadline_tz, deadline_raw = self._parse_deadline(body_text)

        references = self._references(blocks, body_text)
        procuring_body = self._first_value(blocks, self._config["procuring_body_labels"])
        scope = self._first_value(blocks, self._config["scope_labels"])
        requirements = self._values(blocks, self._config["requirements_labels"])

        raw_metadata: dict[str, Any] = {
            "language": self._guess_language(body_text or title),
            "detail_page_url": response.final_url,
        }
        if published_raw:
            raw_metadata["published_raw"] = published_raw
        if deadline_raw:
            raw_metadata["deadline_raw"] = deadline_raw
        if references:
            raw_metadata["reference"] = references[0]

        listing = TenderListing(
            external_id=tender_id,
            title=title,
            url=response.final_url,
            published_at=published_at,
            deadline_at=deadline_at,
            deadline_timezone=deadline_tz,
            raw_metadata=raw_metadata,
        )
        attachments = self._parse_attachments(soup, response.final_url, tender_id)
        return TenderDetail(
            listing=listing,
            procuring_body=procuring_body,
            reference_numbers=references,
            scope=scope,
            requirements_hints=requirements,
            attachments=attachments,
            raw_html=response.text,
        )

    def _detail_title(self, soup: BeautifulSoup) -> str | None:
        for selector in self._config["detail_title_selectors"]:
            element = soup.select_one(selector)
            if element is None:
                continue
            text = element.get_text(" ", strip=True)
            if text:
                return text
        return None

    @staticmethod
    def _norm_label(raw: str) -> str:
        """Normalise a rich-text label for category matching (case/space/colon tolerant)."""
        return " ".join(raw.split()).strip().rstrip(":;").strip().lower()

    def _label_blocks(self, container: Tag) -> list[tuple[str, str]]:
        """Split the rich-text body into ``(label, value)`` pairs.

        Fixture convention: ``<div class="elementToProof"><strong>LABEL: value</strong></div>``.
        Empty values (e.g. an unfilled ``End Date:``) yield no block.
        """
        blocks: list[tuple[str, str]] = []
        for element in container.find_all(recursive=False):
            if not isinstance(element, Tag):
                continue
            strong = element.find("strong")
            if strong is None:
                continue
            label_text = " ".join(strong.get_text(" ", strip=True).split())
            value = " ".join(element.get_text(" ", strip=True).split())
            if value.lower().startswith(label_text.lower()):
                value = value[len(label_text) :].lstrip(":; ").strip()
            label = self._norm_label(label_text)
            if label and value:
                blocks.append((label, value))
        return blocks

    def _references(self, blocks: list[tuple[str, str]], body_text: str) -> list[str]:
        refs = self._values(blocks, self._config["reference_labels"])
        if not refs:
            fallback = self._reference_number(body_text)
            if fallback:
                refs.append(fallback)
        return list(dict.fromkeys(refs))

    def _first_value(self, blocks: list[tuple[str, str]], labels: list[str]) -> str | None:
        normalized = {self._norm_label(label) for label in labels}
        for label, value in blocks:
            if label in normalized:
                return value
        return None

    def _values(self, blocks: list[tuple[str, str]], labels: list[str]) -> list[str]:
        normalized = {self._norm_label(label) for label in labels}
        return [value for label, value in blocks if label in normalized]

    def _parse_attachments(
        self,
        soup: BeautifulSoup,
        page_url: str,
        tender_id: str,
    ) -> list[TenderAttachment]:
        excluded = [re.compile(p) for p in self._config["excluded_href_patterns"]]
        ext_re = re.compile(self._config["document_extensions"], re.IGNORECASE)
        containers = self._attachment_containers(soup)

        attachments: list[TenderAttachment] = []
        for link in soup.find_all("a", href=True):
            href = str(link.get("href", ""))
            if self._is_excluded_link(href, link, excluded):
                continue
            resolved = urllib.parse.urljoin(page_url, href)
            if not self._is_document_link(resolved, link, ext_re, containers):
                continue
            filename = self._attachment_filename(resolved, link, tender_id)
            mime = self._guess_mime(filename, resolved)
            attachments.append(
                TenderAttachment(
                    source_url=resolved,
                    filename=filename,
                    mime_type=mime,
                    advertised_size_bytes=self._advertised_size(link, page_url, ext_re),
                    is_zip=self._is_zip_link(filename, mime, resolved),
                )
            )
        return attachments

    def _attachment_containers(self, soup: BeautifulSoup) -> list[Tag]:
        """Every attachments/bundles block on the page (deduplicated by identity).

        A page may expose several attachment sections; links inside *any* of them are
        treated as documents (prompt 07 §2 — no silent restriction to one section).
        """
        containers: list[Tag] = []
        seen: set[int] = set()
        for selector in self._config["attachment_block_selectors"]:
            for element in soup.select(selector):
                if id(element) not in seen:
                    seen.add(id(element))
                    containers.append(element)
        return containers

    @staticmethod
    def _is_excluded_link(
        href: str,
        link: Tag,
        excluded: list[re.Pattern[str]],
    ) -> bool:
        rel = link.get("rel")
        if rel and "next" in [str(r).lower() for r in rel]:
            return True
        stripped = href.strip()
        return any(pattern.search(stripped) for pattern in excluded)

    def _is_document_link(
        self,
        resolved: str,
        link: Tag,
        ext_re: re.Pattern[str],
        containers: list[Tag] | None,
    ) -> bool:
        path = urllib.parse.urlsplit(resolved).path
        if ext_re.search(path):
            return True
        if any(marker in resolved for marker in self._config["attachment_path_markers"]):
            return True
        if containers is None:
            return False
        return any(container in link.parents for container in containers)

    def _attachment_filename(self, resolved: str, link: Tag, tender_id: str) -> str:
        path = urllib.parse.urlsplit(resolved).path.rstrip("/")
        basename = urllib.parse.unquote(path.rsplit("/", 1)[-1] if "/" in path else path)
        if "." in basename:
            return basename
        if link.get_text(strip=True):
            text = " ".join(link.get_text(" ", strip=True).split())
            if len(text) <= 200:
                return text
        return f"attachment-{tender_id}"

    @staticmethod
    def _guess_mime(filename: str, resolved: str) -> str | None:
        lowered = (filename + urllib.parse.urlsplit(resolved).path).lower()
        for ext, mime in _DOCUMENT_MIME.items():
            if lowered.endswith(ext):
                return mime
        return None

    @staticmethod
    def _is_zip_link(filename: str, mime: str | None, resolved: str) -> bool:
        if filename.lower().endswith(".zip") or resolved.lower().endswith(".zip"):
            return True
        return mime in _ZIP_MIMES

    def _advertised_size(
        self,
        link: Tag,
        page_url: str,
        ext_re: re.Pattern[str],
    ) -> int | None:
        """Parse a size announced for *this* link (e.g. ``1,2 MB``), if the page states one.

        Only the link's own text and the text of its smallest document-only wrappers are
        consulted (see :meth:`_single_link_wrappers`), so a group-level size — one
        ``<small>(5 MB)</small>`` shared by several files — is never mis-attributed to any
        single file (prompt 07 §2: metadata must reflect what the page states for that link).
        """
        pattern = self._config["advertised_size_pattern"]
        texts = [link.get_text(" ", strip=True)]
        for wrapper in self._single_link_wrappers(link, page_url, ext_re):
            texts.append(wrapper.get_text(" ", strip=True))
        for text in texts:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            try:
                amount = float(match.group("size").replace(",", "."))
            except ValueError:
                continue
            multiplier = _SIZE_UNITS.get(match.group("unit").lower())
            if multiplier is None:
                continue
            return int(amount * multiplier)
        return None

    def _single_link_wrappers(
        self,
        link: Tag,
        page_url: str,
        ext_re: re.Pattern[str],
    ) -> list[Tag]:
        """Closest ancestors whose *only* document link is *link*.

        These are the only containers whose text is safe to read a per-file size from:
        a wrapper that also contains other document links is inherently ambiguous, so the
        nearest such ancestor terminates the search (finding: flat-layout attribute leaks).
        """
        wrappers: list[Tag] = []
        for parent in link.parents:
            if not isinstance(parent, Tag):
                continue
            document_links = [
                other
                for other in parent.find_all("a", href=True)
                if self._is_document_link(
                    urllib.parse.urljoin(page_url, str(other.get("href", ""))),
                    other,
                    ext_re,
                    None,
                )
            ]
            if len(document_links) == 1 and document_links[0] is link:
                wrappers.append(parent)
                continue
            break
        return wrappers

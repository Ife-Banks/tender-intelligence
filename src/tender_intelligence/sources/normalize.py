"""Source-independent normalisation helpers shared by every discovery strategy.

Every source type — paginated HTML, filtered HTML, search-form, RSS/Atom, JSON API — must
return the *same* internal representation, a
:class:`~tender_intelligence.interfaces.source.TenderListing`, so the rest of the pipeline
never learns where a tender came from (docs/05 §5.2; PROJECT_RULES #10). This module is
where that shared representation's rules live, so the strategies contain only the parsing
that is genuinely specific to their transport.

Nothing here knows any site. There is no source name, hostname or site-specific selector in
this file, and none may be added: adding a site is a configuration change only.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from tender_intelligence.interfaces.source import TenderListing

#: Characters collapsed to a single space when normalising a title or label.
_WHITESPACE = re.compile(r"\s+")

#: Key under which every strategy records how a listing was produced. Together with the
#: source row id (held by the pipeline, not the adapter) this is the provenance a run report
#: needs to answer "which strategy, which URL, which filter produced this tender?".
PROVENANCE_KEY = "source_strategy"

#: ``raw_metadata`` key holding the exact page/endpoint/feed a listing was read from.
DISCOVERY_URL_KEY = "discovery_url"

#: ``raw_metadata`` key holding the response kind the listing was parsed out of.
RESPONSE_KIND_KEY = "response_kind"

#: ``raw_metadata`` key explaining which configured field produced ``external_id``.
IDENTITY_SOURCE_KEY = "identity_source"

#: Values written to ``PROVENANCE_KEY``. A downstream consumer can therefore tell a JSON-API
#: tender from an HTML one without the pipeline knowing anything about either transport.
RESPONSE_KIND_HTML = "html_listing"
RESPONSE_KIND_FEED = "syndication_feed"
RESPONSE_KIND_JSON = "json_api"

#: Tracking parameters stripped when deriving a stable identity from a URL.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
    }
)


def clean_text(value: Any) -> str:
    """Collapse whitespace in *value* and strip it; ``""`` for ``None``/non-strings."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _WHITESPACE.sub(" ", value).strip()


def selector_list(value: Any) -> list[str]:
    """Normalise a configured selector into a list of selectors.

    Configuration is written by operators, and a single CSS selector is the overwhelmingly
    common case; accepting both a bare string and a list keeps configurations short without
    forcing every site to be written as a one-element list.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def absolute_url(base: str, href: str) -> str:
    """Resolve *href* against *base*; ``""`` when either is missing.

    A listing row that carries no usable link cannot produce a dedupe identity, so callers
    treat ``""`` as "skip this row" rather than guessing a URL.
    """
    href = clean_text(href)
    if not href:
        return ""
    if not base:
        return href
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return ""


def apply_query_params(url: str, params: dict[str, Any] | None) -> str:
    """Return *url* with *params* merged into its query string.

    Configured keys replace any existing occurrence, so a facet value always wins over
    whatever the site's own links carried. Order is stable (existing pairs keep their order,
    new ones append) so a paginated crawl produces reproducible URLs — a requirement of the
    visited-URL loop guard.
    """
    if not params:
        return url
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        name = str(key)
        if value is None:
            query = [(existing, current) for existing, current in query if existing != name]
            continue
        query = [(existing, current) for existing, current in query if existing != name]
        if isinstance(value, bool):
            query.append((name, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            query.extend((name, str(item)) for item in value)
        else:
            query.append((name, str(value)))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def canonical_url(url: str) -> str:
    """Strip the fragment and tracking parameters from *url* for identity comparison.

    Used only to decide whether two URLs name the same item. The URL stored on the listing is
    always the original — rewriting it would lose the link a user can actually open.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(kept), "")
    )


def derive_external_id(url: str) -> str:
    """Derive a stable identity from *url* for sources that publish no id of their own.

    The URL path (plus any surviving query) is used rather than a hash of the whole URL, so
    the identity is human-readable and stable across runs. Returns ``""`` for an empty URL.
    """
    canonical = canonical_url(url)
    if not canonical:
        return ""
    parsed = urllib.parse.urlsplit(canonical)
    identity = f"{parsed.netloc}{parsed.path}"
    if parsed.query:
        identity = f"{identity}?{parsed.query}"
    return identity.strip("/?")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse *value* into an aware UTC :class:`datetime`, or ``None`` when unreadable.

    Understands the three shapes a source realistically publishes: ISO-8601 (with or without
    ``Z``, with or without an explicit offset), RFC 2822/1123 (``pubDate``), and epoch
    seconds/milliseconds. An unreadable value yields ``None`` — never a guessed timestamp and
    never a naive one, because a naive deadline is the exact defect this project exists to
    prevent (docs/05 §5.2, v1.1 §5.6).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = clean_text(value)
    if not text:
        return None

    iso_candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    try:
        rfc = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        rfc = None
    if rfc is not None:
        return rfc.astimezone(UTC) if rfc.tzinfo else rfc.replace(tzinfo=UTC)

    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return _from_epoch(float(text))
    return None


def _from_epoch(number: float) -> datetime | None:
    """Interpret a numeric timestamp as seconds, or milliseconds when implausibly large."""
    try:
        # Anything past ~year 33658 in seconds is a millisecond value in disguise.
        seconds = number / 1000.0 if abs(number) > 1e11 else number
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def dig(data: Any, path: str | None) -> Any:
    """Read a dotted/indexed *path* out of decoded JSON.

    ``"data.tenders"`` walks mappings, ``"links[0].href"`` walks sequences, and a path segment
    that is itself an integer indexes a sequence. Returns ``None`` when any step is missing or
    of the wrong shape — a missing optional field is normal, not an error.
    """
    if not path:
        return None
    current = data
    for segment in path.split("."):
        if not segment:
            continue
        name, indices = _split_segment(segment)
        if name:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
        for index in indices:
            if not isinstance(current, (list, tuple)):
                return None
            if index >= len(current):
                return None
            current = current[index]
    return current


def _split_segment(segment: str) -> tuple[str, list[int]]:
    """Split ``"links[0][1]"`` into ``("links", [0, 1])``."""
    bracket = segment.find("[")
    if bracket < 0:
        return segment, []
    name = segment[:bracket]
    indices: list[int] = []
    for token in re.findall(r"\[(\d+)\]", segment[bracket:]):
        indices.append(int(token))
    return name, indices


def as_list(value: Any) -> list[Any]:
    """Coerce a configured field value that may be a list or a single item into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def dig_all(data: Any, path: str | None) -> list[Any]:
    """Every value at *path*, flattening one level when the path yields a list."""
    value = dig(data, path)
    return as_list(value)


def dedupe_listings(
    listings: Iterable[TenderListing],
) -> tuple[list[TenderListing], int]:
    """Drop repeated ``external_id`` values, keeping the first occurrence.

    Returns ``(unique, duplicate_count)``. This is the *in-run* guard, deliberately separate
    from the pipeline's persisted ``(source_id, external_id)`` dedupe (prompt 05): a listing
    repeated across two pages, two facets or two feed pages is one tender, and re-reading it
    wastes a detail fetch. The pipeline's dedupe remains the authority on what is *new*.
    """
    unique: list[TenderListing] = []
    seen: set[str] = set()
    duplicates = 0
    for listing in listings:
        key = (listing.external_id or "").strip()
        if not key or key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(listing)
    return unique, duplicates


def with_metadata(
    listing: TenderListing,
    *,
    response_kind: str,
    discovery_url: str,
    **extra: Any,
) -> TenderListing:
    """Return *listing* with provenance recorded in its ``raw_metadata``.

    ``TenderListing`` is frozen, so provenance is attached by rebuilding it — every strategy
    goes through here so a run report can always say which response kind and which URL a
    tender was read from, whatever the transport. ``None`` values are not written, so an
    absent field leaves no misleading empty key behind.
    """
    metadata: dict[str, Any] = dict(listing.raw_metadata or {})
    metadata[PROVENANCE_KEY] = response_kind
    if discovery_url:
        metadata[DISCOVERY_URL_KEY] = discovery_url
    for key, value in extra.items():
        if value is not None:
            metadata[key] = value
    return TenderListing(
        external_id=listing.external_id,
        title=listing.title,
        url=listing.url,
        published_at=listing.published_at,
        deadline_at=listing.deadline_at,
        deadline_timezone=listing.deadline_timezone,
        raw_metadata=metadata,
    )

"""Unit tests for the RSS/RDF/Atom discovery strategy (v1.1 §5.1, docs/05).

Every feed below is synthetic and served from memory; nothing here reaches a live portal
(prompt 04 §6, §17). The three formats are exercised because portals publish all three, and
the point of each test is that *which* format a source uses is a configuration fact rather
than a code path anyone has to extend.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tender_intelligence.core.errors import PARSER_MISMATCH
from tender_intelligence.interfaces.source import SourceError
from tender_intelligence.sources.feed import FeedAdapter
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse

FEED_URL = "https://example.invalid/tenders/feed.xml"

RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Procurement notices</title>
    <item>
      <title>Supply of laboratory reagents</title>
      <link>/tenders/7001</link>
      <guid isPermaLink="false">7001</guid>
      <pubDate>Tue, 01 Sep 2026 08:30:00 +0000</pubDate>
      <description>&lt;p&gt;Reagents for two labs.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Cleaning services</title>
      <link>/tenders/7002</link>
      <guid isPermaLink="false">7002</guid>
      <pubDate>Wed, 02 Sep 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

RDF = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="https://example.invalid/tenders/feed.xml">
    <title>Procurement notices</title>
  </channel>
  <item rdf:about="https://example.invalid/tenders/7003">
    <title>Furniture supply</title>
    <link>https://example.invalid/tenders/7003</link>
    <dc:date>2026-09-03T10:15:00Z</dc:date>
    <description>Desks and chairs for the new office.</description>
  </item>
</rdf:RDF>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Procurement notices</title>
  <entry>
    <id>urn:tender:7004</id>
    <title type="html">Road &lt;b&gt;maintenance&lt;/b&gt; &amp; repairs</title>
    <link rel="self" href="https://example.invalid/tenders/feed.atom"/>
    <link rel="enclosure" href="https://example.invalid/tenders/7004/spec.pdf"/>
    <link rel="alternate" type="text/html" href="/tenders/7004"/>
    <published>2026-09-04T12:00:00+02:00</published>
    <updated>2026-09-05T12:00:00Z</updated>
    <summary>Two-year maintenance contract for the ring road.</summary>
  </entry>
</feed>"""

DETAIL_HTML = """<html><body>
  <div class="col-lg-12"><div class="card">
    <div class="card-header"><h1>Supply of laboratory reagents</h1></div>
    <div class="card-body"><div class="trix-content">
      <div class="elementToProof"><strong>REFERENCE :</strong> LAB-2026-014</div>
      <div class="elementToProof"><strong>Client :</strong> Synthetic Health Authority</div>
      <div class="elementToProof"><strong>Deadline for submission of applications:</strong>
        24 September 2026 at 1.00 pm GMT.</div>
      <div class="attachments"><div class="attachment">
        <a href="/uploads/tenders/7001/spec.pdf">Specification (PDF)</a>
      </div></div>
    </div></div>
  </div></div>
</body></html>"""


def _adapter(xml: str, *, fetcher=None, **config) -> FeedAdapter:
    def fetch(url: str) -> HttpResponse:
        if fetcher is not None:
            return fetcher(url)
        return HttpResponse(200, {"content-type": "application/xml"}, xml, url)

    return FeedAdapter(
        FEED_URL,
        fetcher=fetch,
        parser_config=config,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )


# ---------------------------------------------------------------------------
# The three formats
# ---------------------------------------------------------------------------


def test_rss2_entries_become_listings() -> None:
    listings = _adapter(RSS2).list_new_tenders()
    assert [c.external_id for c in listings] == ["7001", "7002"]
    first = listings[0]
    assert first.title == "Supply of laboratory reagents"
    assert first.url == "https://example.invalid/tenders/7001"
    assert first.published_at == datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    assert first.raw_metadata["published_raw"] == "Tue, 01 Sep 2026 08:30:00 +0000"


def test_rss1_rdf_entries_become_listings() -> None:
    """An RDF feed carries no guid, so identity falls back to the entry's own URL."""
    listings = _adapter(RDF).list_new_tenders()
    assert len(listings) == 1
    listing = listings[0]
    assert listing.title == "Furniture supply"
    assert listing.published_at == datetime(2026, 9, 3, 10, 15, tzinfo=UTC)
    assert listing.raw_metadata["identity_source"] == "derived_from_link"
    assert listing.external_id == "example.invalid/tenders/7003"


def test_atom_entries_become_listings() -> None:
    """Atom is read through its ``rel``-marked link, not through an enclosure."""
    listings = _adapter(ATOM).list_new_tenders()
    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_id == "urn:tender:7004"
    assert listing.url == "https://example.invalid/tenders/7004"
    # published, not updated: 12:00+02:00 is 10:00 UTC.
    assert listing.published_at == datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    # Escaped HTML in a title is text by the time it reaches the pipeline.
    assert listing.title == "Road maintenance & repairs"


def test_a_namespaceless_feed_is_read_the_same_way() -> None:
    """Namespaces are a presentation detail; a feed without one behaves identically."""
    plain = """<rss version="2.0"><channel><item>
        <title>No namespace</title><link>/tenders/9</link><guid>9</guid>
        <pubDate>2026-09-01T00:00:00Z</pubDate>
      </item></channel></rss>"""
    listings = _adapter(plain).list_new_tenders()
    assert [c.external_id for c in listings] == ["9"]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_configured_id_pattern_maps_a_feed_id_onto_a_tender_reference() -> None:
    """A feed whose ids embed the reference number is mapped by configuration, not code."""
    feed = """<rss version="2.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <channel><item>
        <title>Guided reading</title>
        <link>/tenders/2026-009</link>
        <guid isPermaLink="false">xsi:REF-2026-009-ATD</guid>
      </item></channel>
    </rss>"""
    listings = _adapter(feed, id_pattern=r"REF-(?P<id>\d{4}-\d{3})").list_new_tenders()
    assert [c.external_id for c in listings] == ["2026-009"]
    assert listings[0].raw_metadata["identity_source"].startswith("id_pattern:")


def test_repeated_entries_collapse_to_one_listing() -> None:
    """A feed re-listing the same guid yields it once."""
    feed = """<rss version="2.0"><channel>
        <item><title>One</title><link>/tenders/5</link><guid>5</guid></item>
        <item><title>One (updated)</title><link>/tenders/5</link><guid>5</guid></item>
      </channel></rss>"""
    assert [c.external_id for c in _adapter(feed).list_new_tenders()] == ["5"]


# ---------------------------------------------------------------------------
# Deadlines: absent unless the source says how to read them
# ---------------------------------------------------------------------------


def test_no_deadline_is_invented_from_prose() -> None:
    """A date in a summary is not a deadline, and the feed does not get to decide it is."""
    feed = """<rss version="2.0"><channel><item>
        <title>Consultancy</title><link>/tenders/11</link><guid>11</guid>
        <description>Proposals are due 30 November 2026 at 12.00 pm GMT.</description>
      </item></channel></rss>"""
    listing = _adapter(feed).list_new_tenders()[0]
    assert listing.deadline_at is None
    assert listing.deadline_timezone is None


def test_configured_deadline_patterns_are_honoured() -> None:
    """When a source configures patterns for its feed, they are applied as configured."""
    feed = """<rss version="2.0"><channel><item>
        <title>Consultancy</title><link>/tenders/11</link><guid>11</guid>
        <description>Proposals are due 30 November 2026 at 12.00 pm GMT.</description>
      </item></channel></rss>"""
    adapter = _adapter(
        feed,
        deadline_patterns=[
            r"due\s+(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})"
            r"(?:\s+at\s+(?P<hour>\d{1,2})[.:](?P<minute>\d{2})\s*(?P<ampm>[ap]m))?"
            r"\s*(?P<tz>[A-Z]{2,4})?"
        ],
    )
    listing = adapter.list_new_tenders()[0]
    assert listing.deadline_at is not None
    assert listing.deadline_at.hour == 12  # 12.00 pm GMT == 12:00 UTC
    assert listing.deadline_timezone == "GMT"
    assert listing.raw_metadata["deadline_raw"]


# ---------------------------------------------------------------------------
# Bounds and malformed input
# ---------------------------------------------------------------------------


def test_max_items_bounds_a_very_large_feed() -> None:
    items = "".join(
        f"<item><title>T{i}</title><link>/tenders/{i}</link><guid>{i}</guid></item>"
        for i in range(50)
    )
    feed = f"<rss version='2.0'><channel>{items}</channel></rss>"
    assert len(_adapter(feed, max_items=10).list_new_tenders()) == 10


def test_malformed_xml_is_reported_not_silently_empty() -> None:
    """A truncated or HTML-instead-of-XML response must not read as 'no tenders'."""
    with pytest.raises(SourceError) as excinfo:
        _adapter("<html><body>not a feed</body>").list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_a_feed_with_no_entries_is_reported_unless_allowed() -> None:
    feed = "<rss version='2.0'><channel><title>Nothing yet</title></channel></rss>"
    with pytest.raises(SourceError) as excinfo:
        _adapter(feed).list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH
    assert _adapter(feed, allow_empty_listing=True).list_new_tenders() == []


def test_an_entry_without_a_title_is_skipped_not_fatal() -> None:
    """One broken entry among good ones is skipped; only a wholly broken feed fails."""
    feed = """<rss version="2.0"><channel>
        <item><link>/tenders/6</link><guid>6</guid></item>
        <item><title>Good</title><link>/tenders/7</link><guid>7</guid></item>
      </channel></rss>"""
    assert [c.external_id for c in _adapter(feed).list_new_tenders()] == ["7"]


def test_a_feed_whose_entries_are_all_broken_is_reported() -> None:
    feed = """<rss version="2.0"><channel>
        <item><link>/tenders/6</link><guid>6</guid></item>
      </channel></rss>"""
    with pytest.raises(SourceError) as excinfo:
        _adapter(feed).list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


# ---------------------------------------------------------------------------
# Metadata and the handoff to HTML detail parsing
# ---------------------------------------------------------------------------


def test_entries_carry_feed_provenance() -> None:
    listing = _adapter(RSS2).list_new_tenders()[0]
    assert listing.raw_metadata["source_strategy"] == "syndication_feed"
    assert listing.raw_metadata["discovery_url"] == FEED_URL
    assert listing.raw_metadata["feed_url"] == FEED_URL
    assert listing.raw_metadata["feed_id"] == "7001"
    assert listing.raw_metadata["summary"] == "Reagents for two labs."


def test_summary_can_be_turned_off() -> None:
    listing = _adapter(RSS2, include_summary=False).list_new_tenders()[0]
    assert "summary" not in listing.raw_metadata


def test_detail_page_is_fetched_from_the_url_the_entry_linked_to() -> None:
    """Feed identities are GUIDs, so the discovered link — not a URL template — is followed."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        if url == FEED_URL:
            return HttpResponse(200, {"content-type": "application/xml"}, RSS2, url)
        return HttpResponse(200, {"content-type": "text/html"}, DETAIL_HTML, url)

    adapter = _adapter(RSS2, fetcher=fetch)
    listing = adapter.list_new_tenders()[0]
    detail = adapter.get_detail(listing.external_id)
    assert requested == [FEED_URL, "https://example.invalid/tenders/7001"]
    assert detail.reference_numbers == ["LAB-2026-014"]
    assert detail.procuring_body == "Synthetic Health Authority"
    assert detail.listing.deadline_at is not None
    assert detail.listing.deadline_timezone == "GMT"
    assert [a.source_url for a in adapter.get_attachments(listing.external_id)] == [
        "https://example.invalid/uploads/tenders/7001/spec.pdf"
    ]

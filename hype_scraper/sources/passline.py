"""Source #1 — Passline (https://www.passline.com).

Passline is behind Cloudflare (passive TLS fingerprint check) — see http.py for
how we get past it. Data comes from two places:

  1. A search listing endpoint filtered to San Luis Potosí, which gives us the
     set of event detail URLs.
  2. Each detail page carries a single <script type="application/ld+json"> of
     type schema.org/Event with almost everything we need: name, image (flyer),
     start/end datetime, location (venue name + street address), description,
     and offers (price -> price_label). AI fallback is only used when a field is
     still missing AND a flyer image exists (rare for Passline).

TIMEZONE NOTE: startDate is tagged with an Argentine offset (Passline is an
Argentine company) e.g. "2026-08-02T19:30:00-03:00", but the wall-clock time IS
the local SLP show time — verified against the listing page which shows the same
HH:MM. So we parse the NAIVE datetime and never convert.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.passline")

SOURCE = "passline"

# Search filtered to SLP. These events are titled "... en San Luis Potosí", so a
# text query is precise; we additionally sanity-check the venue address below.
_LISTING_URL = (
    "https://home.passline.com/eventos.php"
    "?q=san+luis+potosi&category=&region=&comuna=&mes=&pais=mexico&page={page}"
)
_MAX_PAGES = 5  # generous; SLP rarely fills one page

# Event detail links look like www.passline.com/eventos/<slug>. Require the www
# host and a slug made only of [a-z0-9-] so we don't also catch image URLs under
# /imagenes/eventos/...jpg. The slug must contain a letter (image files are like
# "-737216-rec.jpg" or "533369-rec"), which the [a-z] lookahead-free class + the
# trailing filter below enforce.
_EVENT_LINK_RE = re.compile(
    r"www\.passline\.com/eventos/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)", re.IGNORECASE
)
# Reject anything that is clearly an image/asset filename, not an event slug.
_ASSET_RE = re.compile(r"\.(jpe?g|png|gif|webp|svg)$|-rec$|-rec[-_]", re.IGNORECASE)
# Only accept locations that look like San Luis Potosí, to drop the odd
# cross-city match the text search might surface.
_SLP_RE = re.compile(r"san\s*luis\s*potos|s\.?l\.?p\.?", re.IGNORECASE)


def _slugs_from_html(html: str) -> list[str]:
    """Extract event slugs from listing HTML. Pure function — unit-testable."""
    slugs = []
    for slug in _EVENT_LINK_RE.findall(html):
        slug = slug.strip("/")
        if not slug or "eventos.php" in slug or _ASSET_RE.search(slug):
            continue
        slugs.append(slug)
    return sorted(set(slugs))


def _listing_slugs(page: int) -> list[str]:
    """Return event slugs (source_event_id) found on one listing page.

    The listing host (home.passline.com) is behind a Cloudflare JS challenge, so
    this goes through http.get_rendered (ScraperAPI). Detail pages below use the
    fast direct http.get — they aren't challenged.
    """
    resp = http.get_rendered(_LISTING_URL.format(page=page))
    return _slugs_from_html(resp.text)


def _detail_url(slug: str) -> str:
    return f"https://www.passline.com/eventos/{slug}"


def _parse_jsonld(html: str) -> Optional[dict]:
    """Return the schema.org/Event JSON-LD object from a detail page, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # May be a single object or a @graph list.
        candidates = data.get("@graph", [data]) if isinstance(data, dict) else data
        if isinstance(candidates, dict):
            candidates = [candidates]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "Event":
                return obj
    return None


def _split_datetime(iso: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'2026-08-02T19:30:00-03:00' -> ('2026-08-02', '19:30'). Offset ignored.

    Also tolerates a space separator and date-only strings.
    """
    if not iso:
        return None, None
    s = iso.strip().replace(" ", "T", 1) if " " in iso and "T" not in iso else iso.strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?", s)
    if not m:
        return None, None
    date = m.group(1)
    time = f"{m.group(2)}:{m.group(3)}" if m.group(2) else None
    return date, time


def _price_label(offers) -> Optional[str]:
    """Build a human price label from schema.org offers (single or Aggregate)."""
    if not isinstance(offers, dict):
        return None
    currency = offers.get("priceCurrency") or "MXN"
    low = offers.get("lowPrice") or offers.get("price")
    high = offers.get("highPrice")

    def money(v) -> Optional[str]:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return f"${int(n)}" if n.is_integer() else f"${n:.2f}"

    lo, hi = money(low), money(high)
    if lo and hi and lo != hi:
        return f"{lo} - {hi} {currency}"
    if lo:
        return f"{lo} {currency}"
    return None


def _venue_and_address(location) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(location, dict):
        return None, None
    name = location.get("name") or None
    addr = location.get("address")
    street = None
    if isinstance(addr, dict):
        street = addr.get("streetAddress") or None
    elif isinstance(addr, str):
        street = addr or None
    return name, street


def _parse_event(slug: str) -> Optional[ScrapedEvent]:
    """Fetch a detail page and map its JSON-LD into a ScrapedEvent."""
    resp = http.get(_detail_url(slug))
    obj = _parse_jsonld(resp.text)
    if not obj:
        log.warning("no Event JSON-LD on %s", slug)
        return None

    venue_name, address = _venue_and_address(obj.get("location"))

    # Sanity filter: keep only events that look like they're in SLP, checking the
    # address and the title (Passline titles include the city).
    haystack = " ".join(filter(None, [address or "", obj.get("name") or "", venue_name or ""]))
    if not _SLP_RE.search(haystack):
        log.info("dropping non-SLP event: %s", slug)
        return None

    date_start, time_start = _split_datetime(obj.get("startDate"))
    date_end, time_end = _split_datetime(obj.get("endDate"))
    # Only keep date_end if it differs from date_start (single-day otherwise).
    if date_end == date_start:
        date_end = None

    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    # Passline sometimes emits a bare directory URL (…/imagenes/eventos/) with no
    # filename when a flyer is missing — treat that as no image.
    if isinstance(image, str):
        image = image.strip()
        if not image or image.rstrip("/").endswith("/imagenes/eventos") or \
                not re.search(r"/[^/]+\.[a-z0-9]{2,4}$", image, re.IGNORECASE):
            image = None
    else:
        image = None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=slug,
        name=(obj.get("name") or "").strip(),
        venue_name=venue_name,
        address=address,
        date_start=date_start,
        date_end=date_end,
        time_start=time_start,
        time_end=time_end,
        price_label=_price_label(obj.get("offers")),
        ticket_url=_detail_url(slug),
        source_image_url=image if isinstance(image, str) else None,
        raw_description=(obj.get("description") or None),
    )


def scrape() -> list[ScrapedEvent]:
    """Collect SLP events from Passline. Raises on hard fetch failure so the
    runner can isolate it; individual bad detail pages are skipped, not fatal."""
    slugs: list[str] = []
    for page in range(1, _MAX_PAGES + 1):
        page_slugs = _listing_slugs(page)
        if not page_slugs:
            break
        new = [s for s in page_slugs if s not in slugs]
        slugs.extend(new)
        # If a page returns only slugs we've already collected, we've looped.
        if not new:
            break

    log.info("passline listing: %d candidate events", len(slugs))

    events: list[ScrapedEvent] = []
    for slug in slugs:
        try:
            ev = _parse_event(slug)
            if ev and ev.name:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad detail page shouldn't stop the source
            log.warning("failed to parse detail %s: %s", slug, e)

    log.info("passline: %d SLP events parsed", len(events))
    return events

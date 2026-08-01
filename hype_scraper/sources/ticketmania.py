"""Source #7 — Ticketmania (https://www.ticketmania.mx).

Ticketmania runs on the **Ticketplus** platform. It's server-rendered and
un-challenged, so plain curl_cffi works. Two steps, same shape as BoletoHub:

  1. The homepage lists events nationwide as cards linking to `/events/<slug>`
     (e.g. `/events/reyno-en-slp`). The slug is the stable source_event_id.
  2. Each event page carries a schema.org/Event JSON-LD with name, startDate,
     endDate, location (venue + street address + region), image, and an `offers`
     object we turn into a price. No AI fallback needed.

GEO SCOPE — a deliberate exception. Every other source filters on the CITY of San
Luis Potosí. Ticketmania's JSON-LD leaves `addressLocality` EMPTY and only fills
`addressRegion` (the state). Since the city isn't exposed in structured data, we
match on the region ("San Luis Potosí") instead. In practice Ticketmania's SLP
events are all in the capital, so this doesn't leak Matehuala/Xilitla-style
listings — but it is a looser filter than the others by necessity.

TIMEZONE: startDate has no offset ("2026-10-23T21:00") and is already the local
SLP wall-clock show time — we take it as-is, no conversion (like Passline, unlike
BoletoHub's UTC instants).

ENCODING: pages are UTF-8; we decode `.content` as UTF-8 explicitly. The JSON-LD
string fields carry HTML entities (e.g. `&quot;` in a quoted show title), so we
run them through html.unescape before use.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.ticketmania")

SOURCE = "ticketmania"

_BASE = "https://www.ticketmania.mx"
_SLUG_RE = re.compile(r"/events/([a-z0-9][a-z0-9\-]+)", re.IGNORECASE)
# Region-level match (see module docstring). Matching on "potos" (no trailing "í")
# keeps the filter robust to any accent/encoding drift.
_SLP_RE = re.compile(r"san\s*luis\s*potos", re.IGNORECASE)


def _clean(s) -> Optional[str]:
    """Unescape HTML entities and trim; '' -> None. JSON-LD titles contain
    entities like `&quot;` around quoted show names."""
    if not isinstance(s, str):
        return None
    out = html.unescape(s).strip().strip(",").strip()
    return out or None


def _slugs_from_html(page: str) -> list[str]:
    """Extract event slugs from the homepage HTML (pure, testable)."""
    soup = BeautifulSoup(page, "html.parser")
    slugs: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = _SLUG_RE.search(a["href"])
        if m:
            slugs.add(m.group(1).split("?")[0].split("#")[0])
    return sorted(slugs)


def _listing_slugs() -> list[str]:
    return _slugs_from_html(http.get(f"{_BASE}/").text)


def _event_jsonld(slug: str) -> Optional[dict]:
    """Return the schema.org/Event JSON-LD from an event page, or None."""
    page = http.get(f"{_BASE}/events/{slug}").content.decode("utf-8", "replace")
    soup = BeautifulSoup(page, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event":
            return data
    return None


def _split_iso(iso: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'2026-10-23T21:00' -> ('2026-10-23', '21:00'); no TZ conversion.

    A date-only value ('2026-10-23') yields (date, None) rather than a fabricated
    '00:00' — fromisoformat would otherwise happily parse it to midnight.
    """
    if not iso or not isinstance(iso, str):
        return None, None
    s = iso.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "").split("+")[0])
    except ValueError:
        return (s[:10] if len(s) >= 10 else None), None
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M") if "T" in s else None
    return date_str, time_str


def _venue_address(location) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """-> (venue_name, street_address, region)"""
    if not isinstance(location, dict):
        return None, None, None
    name = _clean(location.get("name"))
    addr = location.get("address")
    if isinstance(addr, dict):
        return (name,
                _clean(addr.get("streetAddress")),
                _clean(addr.get("addressRegion")))
    if isinstance(addr, str):
        return name, _clean(addr), None
    return name, None, None


def _price_label(offers) -> Optional[str]:
    """Ticketplus uses an AggregateOffer (single `price`) or a list of offers."""
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        return None
    prices = []
    currency = "MXN"
    for o in offers:
        if not isinstance(o, dict):
            continue
        currency = o.get("priceCurrency") or currency
        try:
            p = float(o.get("price"))
        except (TypeError, ValueError):
            continue
        if p > 0:
            prices.append(p)
    if not prices:
        return None
    lo, hi = min(prices), max(prices)
    fmt = lambda f: f"${int(f)}" if float(f).is_integer() else f"${f:.2f}"  # noqa: E731
    return f"{fmt(lo)} - {fmt(hi)} {currency}" if hi > lo else f"Desde {fmt(lo)} {currency}"


def _first_image(obj: dict) -> Optional[str]:
    img = obj.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    return img if isinstance(img, str) and img.strip() else None


def _to_scraped(slug: str) -> Optional[ScrapedEvent]:
    obj = _event_jsonld(slug)
    if not obj:
        log.warning("no Event JSON-LD for %s", slug)
        return None

    name = _clean(obj.get("name"))
    if not name:
        return None

    venue, address, region = _venue_address(obj.get("location"))

    # SLP filter on the REGION (city isn't exposed — see module docstring).
    if not _SLP_RE.search(region or ""):
        log.info("dropping non-SLP ticketmania event: %s (region=%r)", slug, region)
        return None

    date_start, time_start = _split_iso(obj.get("startDate"))
    date_end, time_end = _split_iso(obj.get("endDate"))
    if date_end == date_start:
        date_end = None
    if time_end == time_start:
        time_end = None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=slug,
        name=name,
        venue_name=venue,
        address=address,
        date_start=date_start,
        date_end=date_end,
        time_start=time_start,
        time_end=time_end,
        price_label=_price_label(obj.get("offers")),
        ticket_url=(obj.get("url") or f"{_BASE}/events/{slug}"),
        source_image_url=_first_image(obj),
    )


def scrape() -> list[ScrapedEvent]:
    slugs = _listing_slugs()
    log.info("ticketmania listing: %d candidate events", len(slugs))

    events: list[ScrapedEvent] = []
    for slug in slugs:
        try:
            ev = _to_scraped(slug)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad page shouldn't stop the source
            log.warning("failed to parse ticketmania %s: %s", slug, e)

    log.info("ticketmania: %d SLP events mapped", len(events))
    return events

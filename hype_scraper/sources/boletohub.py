"""Source #4 — BoletoHub (https://boletohub.com).

Server-rendered and un-challenged, so plain curl_cffi works. Two steps:

  1. The explore page, filtered by city, lists the SLP events:
         https://boletohub.com/explorar?city=san+luis+potosí
     NOTE the accented "í" — without it the filter returns nothing.
     Event links are `/evento/{code}` (e.g. ch-2cbc); the code is the stable
     source_event_id.

  2. Each event page carries a rich schema.org/Event JSON-LD: name, startDate,
     endDate, image, location (venue + FULL street address + locality), and an
     `offers` array we turn into a price range. No AI fallback needed.

TIMEZONE: unlike Passline, BoletoHub's startDate is a real UTC instant
("2026-08-07T02:00:00.000Z"), so we convert UTC -> America/Mexico_City. That
example is 2026-08-06 20:00 local, matching the listing's "AGO 6 · 08:00 P.M.".
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .. import config, http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.boletohub")

SOURCE = "boletohub"

_BASE = "https://boletohub.com"
# The accent matters — "potosi" without it returns 0 results.
_CITY = "san luis potosí"
_LISTING = f"{_BASE}/explorar?city=" + quote(_CITY)

_CODE_RE = re.compile(r"/evento/([a-z0-9][a-z0-9\-]{2,})", re.IGNORECASE)
_SLP_RE = re.compile(r"san\s*luis\s*potos", re.IGNORECASE)

_TZ = ZoneInfo(config.APP_TZ)


def _codes_from_html(html: str) -> list[str]:
    """Extract event codes from the explore listing (pure, testable)."""
    codes = {m.group(1) for m in _CODE_RE.finditer(html)}
    # Sub-pages like /evento/ch-2cbc/recinto leak in; keep the bare code.
    return sorted({c.split("/")[0] for c in codes if c})


def _listing_codes() -> list[str]:
    return _codes_from_html(http.get(_LISTING).text)


def _jsonld(code: str) -> Optional[dict]:
    soup = BeautifulSoup(http.get(f"{_BASE}/evento/{code}").text, "html.parser")
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


def _local(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO instant and convert to app-local time."""
    if not iso:
        return None
    s = iso.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:              # no offset given -> assume already local
        return dt
    return dt.astimezone(_TZ)


def _price_label(offers) -> Optional[str]:
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


def _venue_address(location) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """-> (venue_name, street_address, locality)"""
    if not isinstance(location, dict):
        return None, None, None
    name = (location.get("name") or "").strip() or None
    addr = location.get("address")
    if isinstance(addr, dict):
        return (name,
                (addr.get("streetAddress") or "").strip() or None,
                (addr.get("addressLocality") or "").strip() or None)
    if isinstance(addr, str):
        return name, addr.strip() or None, None
    return name, None, None


def _to_scraped(code: str) -> Optional[ScrapedEvent]:
    obj = _jsonld(code)
    if not obj:
        log.warning("no Event JSON-LD for %s", code)
        return None

    name = (obj.get("name") or "").strip()
    if not name:
        return None

    venue, address, locality = _venue_address(obj.get("location"))

    # SLP filter on the CITY/locality (consistent with the other sources).
    if not _SLP_RE.search(locality or ""):
        log.info("dropping non-city-SLP boletohub event: %s (locality=%r)", code, locality)
        return None

    start = _local(obj.get("startDate"))
    end = _local(obj.get("endDate"))
    date_start = start.strftime("%Y-%m-%d") if start else None
    time_start = start.strftime("%H:%M") if start else None
    date_end = end.strftime("%Y-%m-%d") if end else None
    time_end = end.strftime("%H:%M") if end else None
    if date_end == date_start:
        date_end = None

    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if not isinstance(image, str) or not image.strip():
        image = None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=code,
        name=name,
        venue_name=venue,
        address=address,
        date_start=date_start,
        date_end=date_end,
        time_start=time_start,
        time_end=time_end,
        price_label=_price_label(obj.get("offers")),
        ticket_url=f"{_BASE}/evento/{code}",
        source_image_url=image,
        raw_description=(obj.get("description") or None),
    )


def scrape() -> list[ScrapedEvent]:
    codes = _listing_codes()
    log.info("boletohub listing: %d candidate events", len(codes))

    events: list[ScrapedEvent] = []
    for code in codes:
        try:
            ev = _to_scraped(code)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad page shouldn't stop the source
            log.warning("failed to parse boletohub %s: %s", code, e)

    log.info("boletohub: %d SLP events mapped", len(events))
    return events

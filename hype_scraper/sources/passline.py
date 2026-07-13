"""Source #1 — Passline (https://www.passline.com).

Passline's public site is behind Cloudflare, but its BACKEND JSON API is not:

    POST https://api.passline.com/v1/event/GetBillboardByFilters

is reachable directly with a browser-impersonating client (curl_cffi) — no
Cloudflare challenge, no proxy, no headless browser. This is the same endpoint
the site's own `callEventsFilter-new.js` calls to render the listing. It returns
a clean JSON array with everything we need: slug, name, venue (`lugar`),
start/end date+time, min price + currency, flyer image, and ticket url.

The only field it lacks is the venue STREET ADDRESS. For that we fetch the event
detail page (on the un-challenged `www.` host) and read its schema.org/Event
JSON-LD — but only for NEW events (after dedup), so it's cheap. If that fetch
fails, we still create the draft without an address (the admin adds it, or the
matched venue supplies it).

TIMEZONE: the API returns naive local date/time (e.g. "19:30:00") already in SLP
wall-clock time — no conversion needed.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Optional

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.passline")

SOURCE = "passline"

_API_URL = "https://api.passline.com/v1/event/GetBillboardByFilters"
_SEARCH_TEXT = "san luis potosi"
_COUNTRY = "mexico"
_PAGE_LIMIT = 300  # the site itself requests 300; SLP returns ~12

# Keep only events whose region/venue text looks like San Luis Potosí, guarding
# against the odd cross-city match a free-text search might surface.
_SLP_RE = re.compile(r"san\s*luis\s*potos|s\.?l\.?p\.?", re.IGNORECASE)


def _api_events() -> list[dict]:
    """Call the Passline billboard API filtered to SLP. Returns raw records."""
    body = {
        "country": _COUNTRY,
        "region": None,
        "commune": "",
        "communeNum": None,
        "type": 0,
        "start_date": "",
        "end_date": "",
        "text": _SEARCH_TEXT,
        "tag_id": None,
        "tag": None,
        "upper_category_id": None,
        "limit": f"0,{_PAGE_LIMIT}",
        "offset": "1",
    }
    resp = http.session().post(
        _API_URL,
        json=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://home.passline.com",
            "Referer": "https://home.passline.com/",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _clean(s: Optional[str]) -> Optional[str]:
    """Decode HTML entities (API returns e.g. 'San Luis Potos&iacute;') and trim."""
    if s is None:
        return None
    s = html_lib.unescape(s).strip()
    return s or None


def _hhmm(t: Optional[str]) -> Optional[str]:
    """'19:30:00' -> '19:30'; empty/None -> None."""
    if not t:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})", t.strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _ymd(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    m = re.match(r"\d{4}-\d{2}-\d{2}", d.strip())
    return m.group(0) if m else None


def _price_label(rec: dict) -> Optional[str]:
    """Build 'Desde $500 MXN' from precio_min + currency symbol."""
    raw = rec.get("precio_min")
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    amount = f"${int(n)}" if n.is_integer() else f"${n:.2f}"
    # simbolo_moneda is like "MXN $" — take the currency code if present.
    sym = (rec.get("simbolo_moneda") or "").strip()
    code = re.sub(r"[^A-Z]", "", sym.upper()) or None
    label = f"Desde {amount}"
    return f"{label} {code}" if code else label


def _detail_address(slug: str) -> Optional[str]:
    """Fetch the (un-challenged) detail page and read streetAddress from JSON-LD.

    Best-effort: any failure returns None — the draft is still created.
    """
    try:
        resp = http.get(f"https://www.passline.com/eventos/{slug}")
        m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', resp.text)
        return _clean(m.group(1)) if m else None
    except Exception as e:  # noqa: BLE001
        log.warning("could not fetch address for %s: %s", slug, e)
        return None


def _flyer_url(rec: dict) -> Optional[str]:
    """Prefer the full-size 'recorte' flyer; ignore bare directory URLs."""
    for key in ("recorte", "miniatura"):
        u = (rec.get(key) or "").strip()
        # must have an actual filename with an image extension
        if u and re.search(r"/[^/]+\.(jpe?g|png|webp|gif)$", u, re.IGNORECASE):
            return u
    return None


def _to_scraped(rec: dict, *, with_address: bool) -> Optional[ScrapedEvent]:
    slug = _clean(rec.get("slug"))
    name = _clean(rec.get("nombre"))
    if not slug or not name:
        return None

    # SLP sanity filter across region + venue + name.
    region = _clean(rec.get("nombre_region")) or ""
    venue = _clean(rec.get("lugar"))
    if not _SLP_RE.search(" ".join([region, venue or "", name])):
        log.info("dropping non-SLP event: %s", slug)
        return None

    date_start = _ymd(rec.get("fecha_inicio"))
    date_end = _ymd(rec.get("fecha_termino"))
    if date_end == date_start:
        date_end = None

    url = _clean(rec.get("url")) or f"https://www.passline.com/eventos/{slug}"

    address = _detail_address(slug) if with_address else None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=slug,
        name=name,
        venue_name=venue,
        address=address,
        date_start=date_start,
        date_end=date_end,
        time_start=_hhmm(rec.get("hora_inicio")),
        time_end=_hhmm(rec.get("hora_termino")),
        price_label=_price_label(rec),
        ticket_url=url,
        source_image_url=_flyer_url(rec),
    )


def scrape(*, fetch_address: bool = True) -> list[ScrapedEvent]:
    """Collect SLP events from the Passline API.

    Raises on a hard API failure so the runner can isolate this source; a single
    malformed record is skipped, not fatal. `fetch_address=False` skips the
    per-event detail fetch (useful for a fast smoke test).
    """
    records = _api_events()
    log.info("passline API: %d records", len(records))

    events: list[ScrapedEvent] = []
    for rec in records:
        try:
            ev = _to_scraped(rec, with_address=fetch_address)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad record shouldn't stop the source
            log.warning("failed to map record %s: %s", rec.get("slug"), e)

    log.info("passline: %d SLP events mapped", len(events))
    return events

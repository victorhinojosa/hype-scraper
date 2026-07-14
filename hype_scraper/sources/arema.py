"""Source #2 — Arema (https://arema.mx).

Arema is a React SPA backed by a public JSON API. One POST returns every event
nationwide; we filter to San Luis Potosí by the `state` field:

    POST https://t3lb.arema.mx/public/events/list   (empty JSON body)
      -> { data: { events: [ {event_id, event_name, date, venue_name,
                              category_name, city, state, ...}, ... ] } }

Each record's `date` is a unix timestamp that INCLUDES the local show time
(America/Mexico_City). The flyer is at a predictable CDN path
`https://cdn.arema.dev/t3/events/{id}/800.webp`, and the ticket page is
`https://arema.mx/e/{id}`.

Not in the API: price (nullable — admin adds on review) and street address
(inherited from a matched venue, else left null). No AI fallback needed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import config, http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.arema")

SOURCE = "arema"

_LIST_URL = "https://t3lb.arema.mx/public/events/list"
_CDN_POSTER = "https://cdn.arema.dev/t3/events/{id}/800.webp"
_TICKET_URL = "https://arema.mx/e/{id}"

_TZ = ZoneInfo(config.APP_TZ)


def _list_events() -> list[dict]:
    resp = http.session().post(
        _LIST_URL,
        json={},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return ((payload or {}).get("data") or {}).get("events") or []


def _is_slp(rec: dict) -> bool:
    # Match on CITY only (not state), so events elsewhere in SLP state are
    # excluded — the app covers the San Luis Potosí metro area.
    return "potos" in (rec.get("city") or "").lower()


def _to_scraped(rec: dict) -> ScrapedEvent | None:
    eid = rec.get("event_id")
    name = (rec.get("event_name") or "").strip()
    if not eid or not name:
        return None

    ts = rec.get("date")
    date_start = time_start = None
    if isinstance(ts, (int, float)) and ts > 0:
        dt = datetime.fromtimestamp(ts, _TZ)
        date_start = dt.strftime("%Y-%m-%d")
        time_start = dt.strftime("%H:%M")

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=str(eid),
        name=name,
        venue_name=(rec.get("venue_name") or "").strip() or None,
        date_start=date_start,
        time_start=time_start,
        # price not in the list API -> admin adds on review.
        ticket_url=_TICKET_URL.format(id=eid),
        source_image_url=_CDN_POSTER.format(id=eid),
    )


def scrape() -> list[ScrapedEvent]:
    records = _list_events()
    slp = [r for r in records if _is_slp(r)]
    log.info("arema API: %d events total, %d in SLP", len(records), len(slp))

    events: list[ScrapedEvent] = []
    for rec in slp:
        try:
            ev = _to_scraped(rec)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to map arema record %s: %s", rec.get("event_id"), e)

    log.info("arema: %d SLP events mapped", len(events))
    return events

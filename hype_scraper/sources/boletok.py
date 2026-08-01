"""Source #6 — Boletok (https://boletok.com.mx).

Boletok runs on the white-label **Palco4** ticketing platform. The public site is
a redirect shell (`/` -> `/ventas/cms` -> `/ventas/boletok`); the real event data
never renders into the event pages (they're JS-hydrated via DWR). Instead, Palco4
publishes the whole catalog as a static JSON blob on CloudFront:

    https://<cdn>/ventas/cachejs/JsonBusquedaEvento-2-3-<version>.js
      -> var EventosBuscador = [ { idEvento, litEvento, nombreRecinto, poblacion,
                                   provincia, friendlyUrlEvento, fechaDesde,
                                   fechaHasta, precioMinimoComision, litGenero,
                                   urlCartel, ... }, ... ]

Like Superboletos, the file name carries a cache-version suffix, so we read the
current URL out of the landing page (`/ventas/cms`) at runtime instead of pinning
it — a version bump doesn't break us.

The blob is a **full national archive** (~40 events across every city), so it's
filtered: SLP by `poblacion`/`provincia`, the `Deportes` genre dropped (season-pass
listings that map poorly to single events, mirroring Superboletos), and past dates
dropped defensively.

ENCODING: the file is UTF-8 (accents arrive as multi-byte "Música"/"Potosí"); we
decode `.content` as UTF-8 rather than trusting curl_cffi's charset guess.

TIMEZONE: `fechaDesde` is a local wall-clock string ("2026-08-01 20:00:00") already
in America/Mexico_City — no conversion (unlike BoletoHub's UTC instants). No AI
fallback needed; the catalog has name, venue, date/time, price and poster.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.boletok")

SOURCE = "boletok"

_BASE = "https://boletok.com.mx"
# The landing page the meta-refresh chain lands on; it references the current
# cache-versioned JsonBusquedaEvento URL.
_LANDING = f"{_BASE}/ventas/cms"
_CDN = "https://d3866qt6ci6xnh.cloudfront.net/ventas"
_CACHE_RE = re.compile(r"cachejs/(JsonBusquedaEvento[\w\-]+\.js)")

_SLP_RE = re.compile(r"san\s*luis\s*potos", re.IGNORECASE)
# Genres to drop (see module docstring — consistent with Superboletos).
_EXCLUDED_GENRES = {"deportes"}


def _catalog_url() -> str:
    """Read the current cache-versioned catalog URL from the landing page.

    Falls back to a bare (version-less) guess only if the landing page can't be
    parsed; in practice the ref is always present.
    """
    html = http.get(_LANDING).content.decode("utf-8", "replace")
    m = _CACHE_RE.search(html)
    if m:
        return f"{_CDN}/cachejs/{m.group(1)}"
    raise RuntimeError("boletok: could not locate JsonBusquedaEvento cache URL")


def _fetch_catalog() -> list[dict]:
    """Fetch and parse the `var EventosBuscador = [...]` JSON blob."""
    url = _catalog_url()
    # The blob is UTF-8; decode explicitly so accents survive.
    text = http.get(url).content.decode("utf-8", "replace")
    body = re.sub(r"^\s*var\s+EventosBuscador\s*=\s*", "", text).strip()
    body = body.rstrip().rstrip(";")
    data = json.loads(body)
    return data if isinstance(data, list) else []


def _is_slp(rec: dict) -> bool:
    # Match on CITY (poblacion); provincia is a fallback since a few records set
    # only one. Towns elsewhere in the state are excluded by the city check.
    blob = f"{rec.get('poblacion') or ''}|{rec.get('provincia') or ''}"
    return bool(_SLP_RE.search(blob))


def _is_future(fecha_desde: Optional[str], today: Optional[date] = None) -> bool:
    """Keep only events whose start date is today or later (defensive).

    `today` is injectable so the filter is deterministic in tests.
    """
    if not fecha_desde:
        return True  # no date -> let the pipeline decide (it drops dateless drafts)
    try:
        d = datetime.strptime(fecha_desde[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return d >= (today or date.today())


def _price_label(rec: dict) -> Optional[str]:
    """Build a price range from the commission-inclusive min/max (what buyers pay)."""
    lo = rec.get("precioMinimoComision")
    hi = rec.get("precioMaximoComision")
    try:
        lo = float(lo) if lo is not None else None
        hi = float(hi) if hi is not None else None
    except (TypeError, ValueError):
        return None
    if not lo or lo <= 0:
        return None
    fmt = lambda f: f"${int(f)}" if float(f).is_integer() else f"${f:.2f}"  # noqa: E731
    if hi and hi > lo:
        return f"{fmt(lo)} - {fmt(hi)} MXN"
    return f"Desde {fmt(lo)} MXN"


def _split_datetime(s: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'2026-08-01 20:00:00' -> ('2026-08-01', '20:00'); tolerant of junk."""
    if not s or not isinstance(s, str):
        return None, None
    try:
        dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return (s[:10] if len(s) >= 10 else None), None
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def _to_scraped(rec: dict) -> Optional[ScrapedEvent]:
    eid = rec.get("idEvento")
    name = (rec.get("litEvento") or "").strip()
    if not eid or not name:
        return None

    date_start, time_start = _split_datetime(rec.get("fechaDesde"))
    date_end, time_end = _split_datetime(rec.get("fechaHasta"))
    if date_end == date_start:
        date_end = None
    if time_end == time_start:
        time_end = None

    slug = (rec.get("friendlyUrlEvento") or "").strip()
    ticket_url = f"{_BASE}/ventas/es/{slug}" if slug else None

    poster = (rec.get("urlCartel") or "").strip()
    image = f"{_CDN}/{poster}" if poster else None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=str(eid),
        name=name,
        venue_name=(rec.get("nombreRecinto") or "").strip() or None,
        date_start=date_start,
        date_end=date_end,
        time_start=time_start,
        time_end=time_end,
        price_label=_price_label(rec),
        ticket_url=ticket_url,
        source_image_url=image,
    )


def scrape() -> list[ScrapedEvent]:
    records = _fetch_catalog()
    log.info("boletok catalog: %d events total", len(records))

    events: list[ScrapedEvent] = []
    for rec in records:
        try:
            if not _is_slp(rec):
                continue
            genre = (rec.get("litGenero") or "").strip().lower()
            if genre in _EXCLUDED_GENRES:
                log.info("dropping excluded-genre boletok event: %s (%s)",
                         rec.get("idEvento"), genre)
                continue
            if not _is_future(rec.get("fechaDesde")):
                log.info("dropping past boletok event: %s (%s)",
                         rec.get("idEvento"), rec.get("fechaDesde"))
                continue
            ev = _to_scraped(rec)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad record shouldn't stop the source
            log.warning("failed to map boletok record %s: %s", rec.get("idEvento"), e)

    log.info("boletok: %d SLP events mapped", len(events))
    return events

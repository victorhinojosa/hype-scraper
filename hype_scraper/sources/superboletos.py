"""Source #5 — Superboletos (https://www.superboletos.com).

Superboletos is a statically-exported Next.js site whose data lives in a public
JSON cache on CloudFront — no auth, no Cloudflare, one request:

    {CDN}/{REPO}/{CONTENT}/{VERSION}/catalogos/search.json   (~1150 events)

The CDN base/repo/content/version are read dynamically out of the site's `_app`
chunk (they're plain NEXT_PUBLIC_* literals), so if Superboletos bumps the cache
version we follow it automatically instead of 404ing on a hardcoded path.

FILTERING — this feed is a full historical archive of the whole country, so we
narrow it hard:
  * city  : nombreCiudad must be San Luis Potosí (city, not state)
  * status: claveEstatusFechaEvento == NORMAL (drops CANCELADO/expired)
  * date  : fechaPrimeraPresentacion must parse and be in the future
  * type  : claveTipoEvento in KEEP_CATEGORIES — concerts, teatro y musicales,
            familiares, expos. Deportes is excluded (season-pass listings that
            map poorly to single events).
  * venue : cinema venues are excluded. Cineteca Alameda / Sala Lupe Velez sell
            *movie tickets* which are categorised "Familiares" alongside real
            events, so category alone can't separate them — the venue can.

`eventoId` is the stable source_event_id.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.superboletos")

SOURCE = "superboletos"

_HOME = "https://www.superboletos.com"

# Event types we want. "Deportes" deliberately omitted.
KEEP_CATEGORIES = {
    "Conciertos",
    "CONCIERTO",           # a single record uses this spelling
    "Teatro y musicales",
    "Familiares",
    "Expos y conferencia",
    "Festivales",
    "Comedia",
    "Palenques",
}

# Venues that sell cinema tickets rather than run events. Compared normalized
# (lowercase, accents/punctuation-insensitive) — see _is_cinema_venue.
CINEMA_VENUES = {
    "cineteca alameda",
    "sala lupe velez",
}

_SLP_RE = re.compile(r"san\s*luis\s*potos", re.IGNORECASE)


def _cdn_search_url() -> str:
    """Discover the current jsonCache URL from the site's _app chunk."""
    home = http.get(f"{_HOME}/").text
    m = re.search(r'src="(/_next/static/chunks/pages/_app-[^"]+\.js)"', home)
    if not m:
        raise RuntimeError("superboletos: could not locate _app chunk")
    js = http.get(_HOME + m.group(1)).text

    def cfg(key: str) -> str:
        mm = re.search(rf'{key}:"([^"]+)"', js)
        if not mm:
            raise RuntimeError(f"superboletos: missing {key} in _app chunk")
        return mm.group(1)

    return (
        f"{cfg('NEXT_PUBLIC_CDN_BASE_URL')}/{cfg('NEXT_PUBLIC_CDN_REPO')}"
        f"/{cfg('NEXT_PUBLIC_CDN_CONTENT')}/{cfg('NEXT_PUBLIC_CDN_CONTENT_VERSION')}"
        "/catalogos/search.json"
    )


def _all_events() -> list[dict]:
    url = _cdn_search_url()
    log.info("superboletos cache: %s", url)
    data = http.get(url).json()
    return data if isinstance(data, list) else []


def _norm(s: Optional[str]) -> str:
    """Lowercase + strip accents/punctuation for venue comparison."""
    import unicodedata

    s = "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    ).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _is_cinema_venue(venue: Optional[str]) -> bool:
    n = _norm(venue)
    return any(_norm(c) == n for c in CINEMA_VENUES)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """'18/10/2026 21:30:00' -> datetime (naive, already local time)."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _price_label(rec: dict) -> Optional[str]:
    def num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    lo, hi = num(rec.get("precioMinimo")), num(rec.get("precioMaximo"))
    fmt = lambda f: f"${int(f)}" if float(f).is_integer() else f"${f:.2f}"  # noqa: E731
    if lo and hi and hi > lo:
        return f"{fmt(lo)} - {fmt(hi)} MXN"
    if lo:
        return f"Desde {fmt(lo)} MXN"
    return None


def _to_scraped(rec: dict, *, now: datetime) -> Optional[ScrapedEvent]:
    eid = (rec.get("eventoId") or "").strip()
    name = (rec.get("nombreEvento") or "").strip()
    if not eid or not name:
        return None

    if not _SLP_RE.search(rec.get("nombreCiudad") or ""):
        return None
    if (rec.get("claveEstatusFechaEvento") or "").upper() != "NORMAL":
        return None
    if rec.get("claveTipoEvento") not in KEEP_CATEGORIES:
        return None

    venue = (rec.get("nombreRecinto") or "").strip() or None
    if _is_cinema_venue(venue):
        log.info("dropping cinema listing: %s @ %s", name[:40], venue)
        return None

    dt = _parse_dt(rec.get("fechaPrimeraPresentacion"))
    if not dt or dt < now:
        return None  # undated or already past

    image = (rec.get("rutaImagenMain") or rec.get("rutaImagenThumb") or "").strip() or None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=eid,
        name=name,
        venue_name=venue,
        date_start=dt.strftime("%Y-%m-%d"),
        time_start=dt.strftime("%H:%M"),
        price_label=_price_label(rec),
        ticket_url=f"{_HOME}/landing-evento/{eid}",
        source_image_url=image,
    )


def scrape() -> list[ScrapedEvent]:
    records = _all_events()
    now = datetime.now()
    log.info("superboletos: %d records in cache", len(records))

    events: list[ScrapedEvent] = []
    for rec in records:
        try:
            ev = _to_scraped(rec, now=now)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to map superboletos %s: %s", rec.get("eventoId"), e)

    log.info("superboletos: %d upcoming SLP events mapped", len(events))
    return events

"""Source #3 — Solcet (https://solcet.mx).

Solcet is a San Luis Potosí–focused ticketing site (most events are already in
SLP). It is server-rendered — no Cloudflare challenge. Two steps:

  1. Homepage lists all events as cards linking to `https://solcet.mx/<slug>`.
     Each card shows: name, `venue · city`, and price. We parse these and keep
     the SLP ones (by the city on the card).
  2. Each event page carries a schema.org/Event JSON-LD with the venue, city,
     and the flyer image — but NOT the start date/time (those live only on the
     flyer and in prose). So we set the flyer image and let the pipeline's AI
     fallback fill date/time (and confirm price) from it.

`slug` (the URL path) is the stable source_event_id. The ticket URL is the event
page itself.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .. import http
from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.solcet")

SOURCE = "solcet"
_BASE = "https://solcet.mx"

# Cards/links we must never treat as events.
_NON_EVENT = re.compile(
    r"contacto|terminos|privacidad|soporte|mailto:|wa\.me|facebook|instagram|tiktok|twitter",
    re.IGNORECASE,
)
_SLP_RE = re.compile(r"san\s*luis\s*potos|s\.?l\.?p\.?", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$\s*[\d,]+(?:\.\d+)?")


def _cards_from_html(html: str) -> list[dict]:
    """Parse homepage HTML into {slug, name, venue, city, price} dicts (pure)."""
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _NON_EVENT.search(href):
            continue
        # normalize to an absolute solcet.mx/<slug>
        if href.startswith("/"):
            href = _BASE + href
        if not href.startswith(_BASE + "/"):
            continue
        slug = href[len(_BASE) + 1:].strip("/")
        if not slug or "/" in slug or slug in seen:
            continue

        parts = [t.strip() for t in a.stripped_strings if t.strip()]
        if not parts:
            continue
        seen.add(slug)

        # The "venue · city" line is the part containing "·".
        venue = city = None
        for p in parts:
            if "·" in p:
                left, _, right = p.partition("·")
                venue, city = left.strip() or None, right.strip() or None
                break
        price = next((m.group(0) for p in parts for m in [_PRICE_RE.search(p)] if m), None)
        # Name: first part that isn't the date/venue/price/"Próximamente".
        name = None
        for p in parts:
            if "·" in p or _PRICE_RE.search(p):
                continue
            if re.search(r"^\s*(pr[oó]ximamente|\d)", p, re.IGNORECASE):
                continue
            name = p
            break

        cards.append({"slug": slug, "name": name, "venue": venue,
                      "city": city, "price": price})
    return cards


def _event_slugs_and_cards() -> list[dict]:
    """Fetch the homepage and parse its event cards."""
    return _cards_from_html(http.get(f"{_BASE}/").text)


def _detail_jsonld(slug: str) -> Optional[dict]:
    """Return the schema.org/Event JSON-LD from an event page, or None."""
    resp = http.get(f"{_BASE}/{slug}")
    soup = BeautifulSoup(resp.text, "html.parser")
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


def _first_image(obj: dict) -> Optional[str]:
    img = obj.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    return img if isinstance(img, str) and img.strip() else None


def _to_scraped(card: dict) -> Optional[ScrapedEvent]:
    slug = card["slug"]
    obj = _detail_jsonld(slug) or {}

    # Prefer JSON-LD venue/city; fall back to the card.
    loc = obj.get("location") if isinstance(obj.get("location"), dict) else {}
    venue = card.get("venue") or (loc.get("name") if isinstance(loc, dict) else None)
    addr = loc.get("address") if isinstance(loc, dict) else None
    city = card.get("city")
    if isinstance(addr, dict):
        city = city or addr.get("addressLocality")

    # SLP filter: match on CITY/locality only (not state/region) — the app covers
    # the San Luis Potosí metro area, so towns elsewhere in the state (Xilitla,
    # Matehuala, …) are excluded. addressRegion is deliberately NOT checked.
    if not _SLP_RE.search(city or ""):
        log.info("dropping non-city-SLP solcet event: %s (city=%r)", slug, city)
        return None

    name = (obj.get("name") or card.get("name") or "").strip()
    if not name:
        return None

    return ScrapedEvent(
        source=SOURCE,
        source_event_id=slug,
        name=name,
        venue_name=venue,
        price_label=card.get("price"),
        ticket_url=(obj.get("url") or f"{_BASE}/{slug}"),
        # Date/time are not in structured data — the pipeline's AI fallback reads
        # them off this flyer image (missing_fields() will include date/time).
        source_image_url=_first_image(obj),
    )


def scrape() -> list[ScrapedEvent]:
    cards = _event_slugs_and_cards()
    log.info("solcet homepage: %d candidate events", len(cards))

    events: list[ScrapedEvent] = []
    for card in cards:
        try:
            ev = _to_scraped(card)
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 — one bad event shouldn't stop the source
            log.warning("failed to parse solcet event %s: %s", card.get("slug"), e)

    log.info("solcet: %d SLP events mapped", len(events))
    return events

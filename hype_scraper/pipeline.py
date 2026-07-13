"""The shared pipeline every source feeds into.

For each ScrapedEvent:
  1. Dedup: skip if (source, source_event_id) already in scraped_events. This is
     checked BEFORE anything else and survives the admin deleting/rejecting the
     event, so removed events never reappear.
  2. Venue match against saved venues; on match set venue_id and inherit
     neighborhood/address/google_maps_url from the venue row.
  3. AI fallback ONLY if fields are still missing AND a flyer image exists.
  4. Re-host the flyer image into event-covers -> cover_image_url.
  5. Map to the events row (status='draft', description='', category/tags null).
  6. Insert, then log to scraped_events so we never re-create it.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import ai_fallback, db, images
from .models import ScrapedEvent
from .venues import match_venue

log = logging.getLogger("hype_scraper.pipeline")


def _apply_ai_fallback(ev: ScrapedEvent, venues: list[dict]) -> None:
    """Fill only the fields still missing, from the flyer image, in place."""
    missing = ev.missing_fields()
    if not missing or not ev.source_image_url:
        return
    log.info("  AI fallback for %s (missing: %s)", ev.source_event_id, ", ".join(missing))
    data = ai_fallback.extract_from_flyer(
        ev.source_image_url, [v.get("name", "") for v in venues]
    )
    if not data:
        return

    def take(field: str, value):
        cur = getattr(ev, field)
        if (cur is None or cur == "") and value not in (None, ""):
            setattr(ev, field, value)

    take("name", data.get("name"))
    take("venue_name", data.get("venue_name"))
    take("address", data.get("address"))
    take("date_start", data.get("date_start"))
    take("date_end", data.get("date_end"))
    take("time_start", data.get("time_start"))
    take("time_end", data.get("time_end"))
    take("price_label", data.get("price_label"))
    take("ticket_url", data.get("ticket_url"))
    take("instagram_url", data.get("instagram_url"))
    # venue_matched from the model is handled by the deterministic matcher below;
    # we let the matcher re-run on the (possibly newly filled) venue_name.


def _to_row(ev: ScrapedEvent, venue: Optional[dict], cover_url: Optional[str]) -> dict:
    """Map a normalized event + matched venue into an `events` insert row."""
    row = {
        "name": ev.name,
        "venue_name": ev.venue_name or (venue.get("name") if venue else "") or "",
        # neighborhood is NOT NULL. Prefer the matched venue's; else "" for the
        # admin to fill on review. (category_id/tags stay null per project scope.)
        "neighborhood": (venue.get("neighborhood") if venue else None) or "",
        "address": (venue.get("address") if venue else None) or ev.address,
        "google_maps_url": (venue.get("google_maps_url") if venue else None),
        "date_start": ev.date_start,
        "date_end": ev.date_end,
        "time_start": ev.time_start,
        "time_end": ev.time_end,
        "price_label": ev.price_label,
        "description": "",              # generated manually on review
        "cover_image_url": cover_url,
        "instagram_url": ev.instagram_url,
        "ticket_url": ev.ticket_url,
        "category_id": None,            # set manually on review
        "venue_id": venue.get("id") if venue else None,
        "status": "draft",
        # slug is auto-populated by the DB trigger — do not set it.
    }
    return row


def process(events: list[ScrapedEvent]) -> dict:
    """Run the full pipeline over one source's events. Returns run stats."""
    venues = db.fetch_venues()
    stats = {"seen": 0, "skipped": 0, "created": 0, "errors": 0}

    for ev in events:
        stats["seen"] += 1
        try:
            if db.already_scraped(ev.source, ev.source_event_id):
                stats["skipped"] += 1
                log.info("  skip (already scraped): %s", ev.source_event_id)
                continue

            # AI fallback first (may fill venue_name), then deterministic match.
            _apply_ai_fallback(ev, venues)
            venue = match_venue(ev.venue_name, venues)
            if venue:
                log.info("  venue matched: %r -> %s", ev.venue_name, venue["name"])

            cover_url = None
            if ev.source_image_url:
                cover_url = images.rehost(
                    ev.source_image_url,
                    source=ev.source,
                    source_event_id=ev.source_event_id,
                )

            row = _to_row(ev, venue, cover_url)
            if not row["name"] or not row["date_start"]:
                # Without a name+date the draft is useless; still log it so we
                # don't keep re-trying a broken listing every run.
                log.warning("  incomplete (name/date missing), logging without insert: %s",
                            ev.source_event_id)
                db.log_scraped(ev.source, ev.source_event_id, None)
                stats["skipped"] += 1
                continue

            event_id = db.insert_event(row)
            db.log_scraped(ev.source, ev.source_event_id, event_id)
            stats["created"] += 1
            log.info("  created draft: %s (%s)", ev.name, event_id or "dry-run")

        except Exception as e:  # noqa: BLE001 — isolate per-event failures
            stats["errors"] += 1
            log.exception("  error processing %s: %s", ev.source_event_id, e)

    return stats

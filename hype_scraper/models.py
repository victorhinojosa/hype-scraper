"""Normalized event shape that every source produces and the pipeline consumes.

This is deliberately decoupled from the Supabase `events` row: sources fill what
they can, the pipeline handles venue matching, image upload, AI fallback, and the
final mapping to the DB row (see pipeline.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScrapedEvent:
    # Identity — required. source_event_id must be STABLE across runs for a given
    # event (for Passline: the URL slug). It's the dedup key in scraped_events.
    source: str
    source_event_id: str

    # Core fields (map 1:1 to events columns). Fill what the source has; leave the
    # rest None. The pipeline never invents values.
    name: str = ""
    venue_name: Optional[str] = None
    address: Optional[str] = None
    google_maps_url: Optional[str] = None
    date_start: Optional[str] = None   # YYYY-MM-DD
    date_end: Optional[str] = None     # YYYY-MM-DD or None (single day)
    time_start: Optional[str] = None   # HH:MM 24h or None
    time_end: Optional[str] = None     # HH:MM 24h or None
    price_label: Optional[str] = None
    instagram_url: Optional[str] = None
    ticket_url: Optional[str] = None

    # The flyer/cover image URL AT THE SOURCE. The pipeline downloads this and
    # re-hosts it in the event-covers bucket, then sets the row's cover_image_url
    # to the Supabase public URL.
    source_image_url: Optional[str] = None

    # Free text from the source (used only to help the AI fallback / debugging).
    # NOT written to events.description — drafts store '' per project decision.
    raw_description: Optional[str] = None

    def missing_fields(self) -> list[str]:
        """Fields the AI fallback may try to fill when a flyer image is available."""
        out = []
        if not self.name:
            out.append("name")
        if not self.venue_name:
            out.append("venue_name")
        if not self.date_start:
            out.append("date_start")
        if not self.time_start:
            out.append("time_start")
        if not self.price_label:
            out.append("price_label")
        return out

"""Source #2 — Arema. STUB.

Reuses the exact same pipeline as Passline: implement `scrape()` to return a
list[ScrapedEvent] filled from Arema's structured data (JSON-LD if present, else
HTML parse + AI fallback), and everything downstream — dedup, venue matching,
image re-hosting, draft insert — is already handled by pipeline.process().

To implement:
  1. Find Arema's SLP event listing URL and detail-page structure.
  2. Set source="arema" and a STABLE source_event_id per event.
  3. Mirror passline.py's shape.
"""
from __future__ import annotations

import logging

from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.arema")

SOURCE = "arema"


def scrape() -> list[ScrapedEvent]:
    log.info("arema source not implemented yet — skipping")
    return []

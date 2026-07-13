"""Source #3 — Solcet. STUB.

Reuses the exact same pipeline as Passline: implement `scrape()` to return a
list[ScrapedEvent] and pipeline.process() handles dedup, venue matching, image
re-hosting, and the draft insert. See arema.py for the same notes.
"""
from __future__ import annotations

import logging

from ..models import ScrapedEvent

log = logging.getLogger("hype_scraper.solcet")

SOURCE = "solcet"


def scrape() -> list[ScrapedEvent]:
    log.info("solcet source not implemented yet — skipping")
    return []

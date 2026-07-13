"""Supabase access: venues, the scraped_events dedup log, and event inserts.

Uses the service role key (bypasses RLS). All DB side effects funnel through
here so DRY_RUN can short-circuit writes in one place.
"""
from __future__ import annotations

import logging
from typing import Optional

from supabase import Client, create_client

from . import config

log = logging.getLogger("hype_scraper.db")

_client: Optional[Client] = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY
        )
    return _client


# ── Venues ────────────────────────────────────────────────────────────────

def fetch_venues() -> list[dict]:
    """All saved venues, used for matching scraped venue names."""
    res = (
        client()
        .table("venues")
        .select("id, name, neighborhood, address, google_maps_url")
        .execute()
    )
    return res.data or []


# ── Dedup log (scraped_events) ──────────────────────────────────────────────

def already_scraped(source: str, source_event_id: str) -> bool:
    """True if we've seen this source item before (survives event deletion)."""
    res = (
        client()
        .table("scraped_events")
        .select("id")
        .eq("source", source)
        .eq("source_event_id", source_event_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def log_scraped(source: str, source_event_id: str, event_id: Optional[str]) -> None:
    """Record that we processed this source item. Idempotent via unique constraint."""
    if config.DRY_RUN:
        log.info("[dry-run] would log scraped_events %s/%s -> %s",
                 source, source_event_id, event_id)
        return
    client().table("scraped_events").insert(
        {
            "source": source,
            "source_event_id": source_event_id,
            "event_id": event_id,
        }
    ).execute()


# ── Events ──────────────────────────────────────────────────────────────────

def insert_event(row: dict) -> Optional[str]:
    """Insert a draft event row. Returns the new event id (or None in dry-run)."""
    if config.DRY_RUN:
        log.info("[dry-run] would insert event: %s @ %s (%s)",
                 row.get("name"), row.get("venue_name"), row.get("date_start"))
        return None
    res = client().table("events").insert(row).execute()
    return (res.data or [{}])[0].get("id")

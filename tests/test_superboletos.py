"""Offline tests for the Superboletos filtering rules.

`superboletos_search.json` is a real slice of the CDN cache: all 53 SLP records
(which include cinema listings, Deportes, and CANCELADO rows) plus a few
non-SLP records, so every filter branch is exercised.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import superboletos

RECORDS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "superboletos_search.json").read_text(
        encoding="utf-8"
    )
)
# Fixed "now" so the future-date filter is deterministic as the fixture ages.
NOW = datetime(2026, 7, 20)


def _mapped():
    return [e for e in (superboletos._to_scraped(r, now=NOW) for r in RECORDS) if e]


def test_excludes_cinema_venues():
    names = {e.name for e in _mapped()}
    for film in ("MOANA", "EVIL DEAD: EN LLAMAS", "LA ODISEA", "RESURECTION",
                 "EL SONIDO AL CAER"):
        assert film not in names, f"cinema listing leaked: {film}"


def test_is_cinema_venue_normalizes():
    assert superboletos._is_cinema_venue("Cineteca Alameda")
    assert superboletos._is_cinema_venue("CINETECA  ALAMEDA")
    assert superboletos._is_cinema_venue("Sala Lupe Velez")
    assert superboletos._is_cinema_venue("Sala Lupe Vélez")   # accent-insensitive
    assert not superboletos._is_cinema_venue("Teatro de la Paz SLP")


def test_excludes_deportes_and_cancelled_and_past():
    for ev, rec in ((superboletos._to_scraped(r, now=NOW), r) for r in RECORDS):
        if ev is None:
            continue
        assert rec.get("claveTipoEvento") != "Deportes"
        assert (rec.get("claveEstatusFechaEvento") or "").upper() == "NORMAL"
        assert ev.date_start >= NOW.strftime("%Y-%m-%d")


def test_keeps_only_slp_city():
    for ev, rec in ((superboletos._to_scraped(r, now=NOW), r) for r in RECORDS):
        if ev is not None:
            assert "potos" in (rec.get("nombreCiudad") or "").lower()


def test_maps_core_fields():
    evs = {e.name: e for e in _mapped()}
    alan = next((e for n, e in evs.items() if n.startswith("ALAN PARSON")), None)
    assert alan is not None
    assert alan.source == "superboletos"
    assert alan.source_event_id
    assert alan.date_start == "2026-10-18"
    assert alan.time_start == "21:30"
    assert alan.ticket_url.startswith("https://www.superboletos.com/landing-evento/")


def test_parse_dt():
    assert superboletos._parse_dt("18/10/2026 21:30:00") == datetime(2026, 10, 18, 21, 30)
    assert superboletos._parse_dt("") is None
    assert superboletos._parse_dt("garbage") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

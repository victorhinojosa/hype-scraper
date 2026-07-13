"""Offline tests for the Passline source mapping + venue matching.

Run: python tests/test_passline.py  (or: python -m pytest tests/)
`passline_api.json` is a real captured response from Passline's billboard API.
The address-detail fetch is network and not exercised here (with_address=False).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import passline
from hype_scraper.venues import match_venue, normalize

RECORDS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "passline_api.json").read_text(
        encoding="utf-8"
    )
)


def _map_all():
    return [passline._to_scraped(r, with_address=False) for r in RECORDS]


def test_all_records_map_to_slp_events():
    events = [e for e in _map_all() if e]
    assert len(events) == len(RECORDS), "some records dropped unexpectedly"
    for e in events:
        assert e.source == "passline"
        assert e.source_event_id and " " not in e.source_event_id  # slug
        assert e.name
        assert e.date_start and len(e.date_start) == 10
        assert e.ticket_url.startswith("https://www.passline.com/eventos/")


def test_entities_decoded_and_times_normalized():
    by_slug = {e.source_event_id: e for e in _map_all() if e}
    griss = by_slug.get("griss-romero-en-san-luis-potosi")
    assert griss is not None
    assert griss.venue_name == "Alboa The Park"
    assert griss.time_start == "19:30"      # from "19:30:00"
    assert griss.date_start == "2026-08-02"
    assert griss.date_end is None           # same-day -> None
    assert griss.price_label == "Desde $500 MXN"
    # entity decoding: no raw &iacute; etc. leaks into any field
    for e in _map_all():
        if e:
            assert "&" not in (e.name or "") or ";" not in (e.name or "")


def test_flyer_url_requires_filename():
    assert passline._flyer_url({"recorte": "https://imagenes.passline.com/eventos/-1-rec.jpg"})
    assert passline._flyer_url({"recorte": "https://imagenes.passline.com/eventos/"}) is None
    assert passline._flyer_url({}) is None


def test_price_label():
    assert passline._price_label({"precio_min": "500.00", "simbolo_moneda": "MXN $"}) == "Desde $500 MXN"
    assert passline._price_label({"precio_min": "0", "simbolo_moneda": "MXN $"}) is None
    assert passline._price_label({"precio_min": None}) is None


def test_venue_matching_strips_codes():
    venues = [
        {"id": "1", "name": "Centro Cultural Universitario Bicentenario",
         "neighborhood": "Centro", "address": "X", "google_maps_url": "Y"},
        {"id": "2", "name": "Teatro de la Paz"},
    ]
    assert match_venue("CC223 Centro Cultural Universitario Bicentenario", venues)["id"] == "1"
    assert match_venue("teatro de la paz", venues)["id"] == "2"
    assert match_venue("Bar Reymar", venues) is None


def test_normalize():
    assert normalize("CC223 Centro Cultural") == "centro cultural"
    assert normalize("Teatro  de  la  Paz!") == "teatro de la paz"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

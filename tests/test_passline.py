"""Offline tests for the Passline source parsing + venue matching.

Run: python -m pytest tests/ (or just: python tests/test_passline.py)
The listing fixture is real HTML captured from a solved Passline listing page.
Detail-page tests are covered by the live smoke run in test_smoke() (network).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import passline
from hype_scraper.venues import match_venue, normalize

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "passline_listing.html"


def test_listing_slugs_no_image_junk():
    html = FIXTURE.read_text(encoding="utf-8")
    slugs = passline._slugs_from_html(html)
    assert len(slugs) == 12, slugs
    # No image filenames or asset junk leaked in.
    assert all(not s.endswith(".jpg") for s in slugs)
    assert "griss-romero-en-san-luis-potosi" in slugs
    assert not any("-rec" in s for s in slugs)


def test_split_datetime_ignores_offset():
    # Argentine offset must be ignored; wall-clock time kept.
    assert passline._split_datetime("2026-08-02T19:30:00-03:00") == ("2026-08-02", "19:30")
    assert passline._split_datetime("2026-05-24 10:50:27") == ("2026-05-24", "10:50")
    assert passline._split_datetime(None) == (None, None)
    assert passline._split_datetime("2026-08-02") == ("2026-08-02", None)


def test_price_label():
    assert passline._price_label(
        {"lowPrice": "500.00", "highPrice": "500.00", "priceCurrency": "MXN"}
    ) == "$500 MXN"
    assert passline._price_label(
        {"lowPrice": "150", "highPrice": "400", "priceCurrency": "MXN"}
    ) == "$150 - $400 MXN"
    assert passline._price_label({"price": "0", "priceCurrency": "MXN"}) == "$0 MXN"
    assert passline._price_label(None) is None


def test_venue_matching_strips_codes():
    venues = [
        {"id": "1", "name": "Centro Cultural Universitario Bicentenario",
         "neighborhood": "Centro", "address": "X", "google_maps_url": "Y"},
        {"id": "2", "name": "Teatro de la Paz"},
    ]
    # Code/prefix on the scraped name must not defeat the match.
    m = match_venue("CC223 Centro Cultural Universitario Bicentenario", venues)
    assert m and m["id"] == "1"
    # Accent/case-insensitive.
    assert match_venue("teatro de la paz", venues)["id"] == "2"
    # No spurious match.
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

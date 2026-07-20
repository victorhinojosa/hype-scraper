"""Offline tests for BoletoHub listing parsing + UTC->local conversion.
Detail-page JSON-LD fetching is network and not exercised here."""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import boletohub

HTML = (pathlib.Path(__file__).parent / "fixtures" / "boletohub_listing.html").read_text(
    encoding="utf-8"
)


def test_listing_codes():
    codes = boletohub._codes_from_html(HTML)
    assert len(codes) == 4, codes
    assert "ch-2cbc" in codes
    # no sub-paths like ch-2cbc/recinto leaked in
    assert all("/" not in c for c in codes)


def test_utc_is_converted_to_local():
    # 2026-08-07T02:00:00Z == 2026-08-06 20:00 in America/Mexico_City
    dt = boletohub._local("2026-08-07T02:00:00.000Z")
    assert dt.strftime("%Y-%m-%d %H:%M") == "2026-08-06 20:00"


def test_naive_datetime_left_alone():
    dt = boletohub._local("2026-08-06T20:00:00")
    assert dt == datetime(2026, 8, 6, 20, 0)
    assert boletohub._local(None) is None
    assert boletohub._local("nope") is None


def test_price_range_from_offers():
    offers = [
        {"price": "330.00", "priceCurrency": "MXN"},
        {"price": "660.00", "priceCurrency": "MXN"},
    ]
    assert boletohub._price_label(offers) == "$330 - $660 MXN"
    assert boletohub._price_label([{"price": "330.00", "priceCurrency": "MXN"}]) == "Desde $330 MXN"
    assert boletohub._price_label([{"price": "0"}]) is None
    assert boletohub._price_label(None) is None


def test_venue_address_extraction():
    loc = {
        "@type": "Place",
        "name": "Teatro Carlos Amador",
        "address": {
            "streetAddress": "Parque Tangamanga I, 78294 San Luis Potosí, S.L.P.",
            "addressLocality": "San Luis Potosí",
        },
    }
    venue, addr, locality = boletohub._venue_address(loc)
    assert venue == "Teatro Carlos Amador"
    assert "Tangamanga" in addr
    assert locality == "San Luis Potosí"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

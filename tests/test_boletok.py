"""Offline tests for Boletok's Palco4-catalog parsing + filtering rules.

`boletok_catalog.json` is a real slice of the CloudFront `EventosBuscador` blob:
3 SLP música events, 3 non-SLP, plus a synthesized past-dated SLP event and a
Deportes-in-SLP event so every filter branch is exercised. The catalog fetch and
cache-URL discovery are network and not exercised here (only `_CACHE_RE` is).
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import boletok

FIX = pathlib.Path(__file__).parent / "fixtures"
RECORDS = json.loads((FIX / "boletok_catalog.json").read_text(encoding="utf-8"))
# Fixed "today" so the future-date filter is deterministic as the fixture ages.
TODAY = date(2026, 8, 1)


def _kept():
    """Records that survive all of scrape()'s filters, mapped to events."""
    out = []
    for rec in RECORDS:
        if not boletok._is_slp(rec):
            continue
        if (rec.get("litGenero") or "").strip().lower() in boletok._EXCLUDED_GENRES:
            continue
        if not boletok._is_future(rec.get("fechaDesde"), today=TODAY):
            continue
        ev = boletok._to_scraped(rec)
        if ev:
            out.append(ev)
    return out


def test_keeps_only_slp():
    names = {e.name for e in _kept()}
    assert "THR En San Luis Potosí" in names
    # non-SLP records dropped
    assert "THR En Zacatecas" not in names
    assert "FNTXY En Xalapa" not in names
    assert "Masterclass con Rafael Lechowski" not in names  # Cuauhtémoc


def test_excludes_deportes():
    names = {e.name for e in _kept()}
    assert "Partido SLP" not in names


def test_excludes_past_dates():
    names = {e.name for e in _kept()}
    assert "Evento Pasado SLP" not in names


def test_is_future_is_injectable_and_inclusive():
    assert boletok._is_future("2026-08-01 20:00:00", today=TODAY)  # today counts
    assert boletok._is_future("2026-12-31 20:00:00", today=TODAY)
    assert not boletok._is_future("2026-07-31 20:00:00", today=TODAY)
    assert boletok._is_future(None, today=TODAY)      # dateless -> pipeline decides
    assert boletok._is_future("garbage", today=TODAY)  # unparseable -> keep


def test_accents_decode_as_utf8():
    # The catalog is UTF-8; names/venues must carry real accented characters.
    thr = next(e for e in _kept() if e.name.startswith("THR"))
    assert thr.name == "THR En San Luis Potosí"
    fntxy = next(e for e in _kept() if e.name.startswith("FNTXY"))
    assert fntxy.venue_name == "Estación Wadley"


def test_maps_core_fields():
    thr = next(e for e in _kept() if e.name.startswith("THR"))
    assert thr.source == "boletok"
    assert thr.source_event_id == "7"
    assert thr.date_start == "2026-08-01"
    assert thr.time_start == "20:00"
    assert thr.date_end is None          # same-day collapses
    assert thr.ticket_url == "https://boletok.com.mx/ventas/es/entradas-musica-thr-en-san-luis-potosi"
    assert thr.source_image_url.startswith("https://d3866qt6ci6xnh.cloudfront.net/ventas/img_web/")
    assert thr.missing_fields() == []    # complete -> no AI fallback


def test_price_label():
    assert boletok._price_label(
        {"precioMinimoComision": 392.0, "precioMaximoComision": 616.0}
    ) == "$392 - $616 MXN"
    assert boletok._price_label(
        {"precioMinimoComision": 350.0, "precioMaximoComision": 350.0}
    ) == "Desde $350 MXN"
    assert boletok._price_label(
        {"precioMinimoComision": 199.5, "precioMaximoComision": 199.5}
    ) == "Desde $199.50 MXN"
    assert boletok._price_label({"precioMinimoComision": 0}) is None
    assert boletok._price_label({}) is None


def test_split_datetime():
    assert boletok._split_datetime("2026-08-01 20:00:00") == ("2026-08-01", "20:00")
    assert boletok._split_datetime("") == (None, None)
    assert boletok._split_datetime(None) == (None, None)
    # tolerant of a date-only / malformed tail
    assert boletok._split_datetime("2026-08-01") == ("2026-08-01", None)


def test_cache_url_regex_matches_landing_snippet():
    snippet = (FIX / "boletok_landing_snippet.html").read_text(encoding="utf-8")
    m = boletok._CACHE_RE.search(snippet)
    assert m and m.group(1).startswith("JsonBusquedaEvento")
    assert m.group(1).endswith(".js")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

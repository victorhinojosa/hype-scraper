"""Offline tests for the Arema source. `arema_list.json` is a real captured
slice of the billboard API (SLP events + a couple of non-SLP for the filter)."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import arema

RECORDS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "arema_list.json").read_text(
        encoding="utf-8"
    )
)["data"]["events"]


def test_slp_filter_is_city_not_state():
    slp = [r for r in RECORDS if arema._is_slp(r)]
    # every kept record's city is San Luis Potosí; none outside are kept
    assert slp, "expected some SLP events in fixture"
    for r in slp:
        assert "potos" in (r.get("city") or "").lower()
    for r in RECORDS:
        if "potos" not in (r.get("city") or "").lower():
            assert not arema._is_slp(r)


def test_maps_date_and_time_from_unix():
    slp = [r for r in RECORDS if arema._is_slp(r)]
    ev = arema._to_scraped(slp[0])
    assert ev is not None
    assert ev.source == "arema"
    assert ev.source_event_id == str(slp[0]["event_id"])
    assert ev.date_start and len(ev.date_start) == 10
    assert ev.time_start and len(ev.time_start) == 5  # HH:MM
    assert ev.ticket_url == f"https://arema.mx/e/{slp[0]['event_id']}"
    assert ev.source_image_url.endswith(f"/{slp[0]['event_id']}/800.webp")
    # price is intentionally not set by this source
    assert ev.price_label is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

"""Offline tests for the Solcet homepage card parser. `solcet_home.html` is the
real homepage. Detail-page JSON-LD + AI fallback are network and not tested here.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import solcet

HTML = (pathlib.Path(__file__).parent / "fixtures" / "solcet_home.html").read_text(
    encoding="utf-8"
)


def test_cards_parsed():
    cards = solcet._cards_from_html(HTML)
    by_slug = {c["slug"]: c for c in cards}
    # known events from the homepage
    assert "legendaria" in by_slug
    rock = by_slug["legendaria"]
    assert rock["name"] == "Dia del Rock"
    assert rock["venue"] == "La Legendaria"
    assert "potos" in (rock["city"] or "").lower()
    assert rock["price"] == "$ 349"


def test_non_event_links_excluded():
    slugs = {c["slug"] for c in solcet._cards_from_html(HTML)}
    for junk in ("contacto", "terminos", "privacidad", "soporte"):
        assert junk not in slugs


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

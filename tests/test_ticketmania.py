"""Offline tests for Ticketmania's listing parsing + JSON-LD mapping helpers.

Only pure functions are exercised here; the per-event JSON-LD fetch is network.
`ticketmania_listing.html` is a small hand-built page mirroring the real slug
shapes (query strings, fragments, absolute URLs, non-event links, duplicates).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hype_scraper.sources import ticketmania

HTML = (pathlib.Path(__file__).parent / "fixtures" / "ticketmania_listing.html").read_text(
    encoding="utf-8"
)


def test_listing_slugs():
    slugs = ticketmania._slugs_from_html(HTML)
    # query strings, fragments, absolute URLs normalised; dups collapsed; non-events dropped
    assert slugs == [
        "alexa-zuart-en-san-luis-potosi",
        "enjambre-en-morelia",
        "reyno-en-slp",
        "yng-naz-en-slp",
    ], slugs


def test_split_iso_no_tz_conversion():
    # Ticketmania's startDate has no offset and is already local wall-clock time.
    assert ticketmania._split_iso("2026-10-23T21:00") == ("2026-10-23", "21:00")
    assert ticketmania._split_iso("2026-10-23T21:00:00") == ("2026-10-23", "21:00")
    assert ticketmania._split_iso("") == (None, None)
    assert ticketmania._split_iso(None) == (None, None)
    assert ticketmania._split_iso("2026-10-23") == ("2026-10-23", None)


def test_clean_unescapes_entities_and_trims():
    assert ticketmania._clean('Isabel Fernandez &quot;Fuera de Lugar&quot; en SLP') == \
        'Isabel Fernandez "Fuera de Lugar" en SLP'
    assert ticketmania._clean("Tomasa Estévez, ") == "Tomasa Estévez"
    assert ticketmania._clean("  ") is None
    assert ticketmania._clean(None) is None


def test_venue_address_uses_region():
    loc = {
        "@type": "Place",
        "name": "Teatro del IMSS - SLP",
        "address": {
            "streetAddress": "Tomasa Estévez, ",
            "addressLocality": "",              # Ticketmania always leaves this empty
            "addressRegion": "San Luis Potosí",
        },
    }
    venue, addr, region = ticketmania._venue_address(loc)
    assert venue == "Teatro del IMSS - SLP"
    assert addr == "Tomasa Estévez"
    assert region == "San Luis Potosí"


def test_slp_filter_matches_region():
    # The scrape filter checks _SLP_RE against the region string.
    assert ticketmania._SLP_RE.search("San Luis Potosí")
    assert ticketmania._SLP_RE.search("SAN LUIS POTOSI")
    assert not ticketmania._SLP_RE.search("Jalisco")
    assert not ticketmania._SLP_RE.search("")


def test_price_label():
    assert ticketmania._price_label({"price": "355.5", "priceCurrency": "MXN"}) == "Desde $355.50 MXN"
    assert ticketmania._price_label([
        {"price": "355.5", "priceCurrency": "MXN"},
        {"price": "784", "priceCurrency": "MXN"},
    ]) == "$355.50 - $784 MXN"
    assert ticketmania._price_label({"price": "0"}) is None
    assert ticketmania._price_label(None) is None


def test_first_image():
    assert ticketmania._first_image({"image": "https://x/y.jpg"}) == "https://x/y.jpg"
    assert ticketmania._first_image({"image": ["https://x/a.jpg", "b"]}) == "https://x/a.jpg"
    assert ticketmania._first_image({"image": []}) is None
    assert ticketmania._first_image({}) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all passed")

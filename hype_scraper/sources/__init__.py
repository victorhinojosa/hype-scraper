"""Source registry.

Each source is a callable `scrape() -> list[ScrapedEvent]`. The runner iterates
this registry with per-source failure isolation, so one source changing its
layout can never crash the whole run.
"""
from __future__ import annotations

from typing import Callable

from ..models import ScrapedEvent
from . import arema, boletohub, passline, solcet, superboletos

# name -> scrape function
REGISTRY: dict[str, Callable[[], list[ScrapedEvent]]] = {
    "passline": passline.scrape,
    "arema": arema.scrape,
    "solcet": solcet.scrape,
    "boletohub": boletohub.scrape,
    "superboletos": superboletos.scrape,
}

"""Match a scraped venue name against the saved venues.

Mirrors the flyer feature's matching intent (src/lib/prompts/flyer-extract.ts):
"ignore codes/prefixes on the flyer, e.g. 'CC223 Centro Cultural Universitario
Bicentenario' matches 'Centro Cultural Universitario Bicentenario'".

Here we do it deterministically in Python (the AI does it for flyers). On match we
return the venue dict so the pipeline can copy neighborhood/address/maps from the DB.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


# Leading venue codes/prefixes seen on listings, e.g. "CC223 ", "A-12 ", "#7 ".
_LEADING_CODE = re.compile(r"^\s*[#]?[a-z]{0,4}[-\s]?\d{1,5}[-\s]+", re.IGNORECASE)


def normalize(name: str) -> str:
    """Lower, strip accents, drop a leading code/prefix, collapse non-alphanumerics."""
    n = _strip_accents(name or "").lower().strip()
    n = _LEADING_CODE.sub("", n)          # "cc223 centro..." -> "centro..."
    n = re.sub(r"[^a-z0-9]+", " ", n)     # punctuation -> space
    return re.sub(r"\s+", " ", n).strip()


def match_venue(scraped_name: Optional[str], venues: list[dict]) -> Optional[dict]:
    """Return the matching venue dict, or None.

    Strategy (most to least strict):
      1. exact normalized equality
      2. one normalized name is a substring of the other (handles the flyer
         showing extra words like a hall/room, or the saved name being shorter)
    """
    if not scraped_name:
        return None
    target = normalize(scraped_name)
    if not target:
        return None

    # 1. exact
    for v in venues:
        if normalize(v.get("name", "")) == target:
            return v

    # 2. substring either direction (guard against trivially short tokens)
    for v in venues:
        vn = normalize(v.get("name", ""))
        if not vn:
            continue
        if (vn in target or target in vn) and min(len(vn), len(target)) >= 4:
            return v

    return None

"""AI fallback for fields structured data (JSON-LD) didn't provide.

DESIGN: this is intentionally DECOUPLED from the web app. We do NOT call the
Vercel /api/flyer-extract endpoint — the whole scraper's premise is that its
integration point is the database, not Vercel, so it must not depend on Vercel
being deployed/awake. The prompt below is a copy of the one in the web app at
`hype/src/lib/prompts/flyer-extract.ts` (flyerExtractPrompt). If you tweak one,
consider whether the other should change too — but they're free to diverge.

We only invoke this when a source both (a) is missing fields AND (b) gave us a
flyer image URL to read. For Passline, JSON-LD is rich, so this rarely fires.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import anthropic

from . import config, http

log = logging.getLogger("hype_scraper.ai")

_client: Optional[anthropic.Anthropic] = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# Ported from hype/src/lib/prompts/flyer-extract.ts — keep semantically in sync.
def _prompt(today: str, venue_names: list[str]) -> str:
    venue_list = ""
    if venue_names:
        listed = "\n".join(f"- {n}" for n in venue_names)
        venue_list = f"\n\nMis venues guardados:\n{listed}"
    return (
        f"Today is {today} (America/Mexico_City). Extract event data from this "
        f"flyer image.{venue_list}\n\n"
        "Return ONLY a JSON object (no prose, no markdown) with keys:\n"
        "- name: event title\n"
        "- venue_matched: if the flyer's venue clearly corresponds to one in my "
        "saved list, return that venue's EXACT name from the list (ignore "
        'codes/prefixes on the flyer, e.g. "CC223 Centro Cultural Universitario '
        'Bicentenario" matches "Centro Cultural Universitario Bicentenario"). '
        "Otherwise null.\n"
        "- venue_name: the venue name as written on the flyer\n"
        "- address: street address if shown, else null\n"
        "- date_start, date_end: YYYY-MM-DD; if year missing use next upcoming "
        "date; date_end null if single day\n"
        "- time_start, time_end: 24h HH:MM or null\n"
        "- price_label: literal price text or null\n"
        "- ticket_url, instagram_url: or null\n"
        "Use null for anything not shown. Do not invent values."
    )


_MEDIA_BY_MAGIC = [
    (b"\x89PNG", "image/png"),
    (b"GIF8", "image/gif"),
    (b"\xff\xd8\xff", "image/jpeg"),
]


def _media_type(data: bytes) -> str:
    for magic, mime in _MEDIA_BY_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def extract_from_flyer(image_url: str, venue_names: list[str]) -> dict:
    """Return the parsed JSON dict, or {} on any failure (never raises)."""
    import base64

    try:
        data = http.get(image_url).content
        media_type = _media_type(data)
        today = datetime.now(ZoneInfo(config.APP_TZ)).strftime("%Y-%m-%d")

        msg = _anthropic().messages.create(
            model="claude-haiku-4-5",
            max_tokens=600,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(data).decode(),
                            },
                        },
                        {"type": "text", "text": _prompt(today, venue_names)},
                    ],
                }
            ],
        )
        text = ""
        for block in msg.content:
            if block.type == "text":
                text = block.text
                break
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text or "{}")
    except Exception as e:  # noqa: BLE001 — fallback must never break a run
        log.warning("AI fallback failed for %s: %s", image_url, e)
        return {}

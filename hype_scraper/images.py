"""Download a source flyer and re-host it in the event-covers bucket.

We re-host rather than hot-linking the source so the app owns the asset (source
URLs rot / hotlink-block) and so it lives next to admin-uploaded covers.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import time
from typing import Optional

from . import config, http
from .db import client

log = logging.getLogger("hype_scraper.images")

# Detect media type from magic bytes (same approach as the flyer-extract route).
_EXT_BY_MAGIC = [
    (b"\x89PNG", "png", "image/png"),
    (b"GIF8", "gif", "image/gif"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
]


def _sniff(data: bytes, url: str) -> tuple[str, str]:
    for magic, ext, mime in _EXT_BY_MAGIC:
        if data.startswith(magic):
            return ext, mime
    # WEBP: "RIFF....WEBP"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    # fall back to the URL extension, else jpg
    m = re.search(r"\.(jpe?g|png|gif|webp)(?:\?|$)", url, re.IGNORECASE)
    if m:
        ext = m.group(1).lower().replace("jpeg", "jpg")
        return ext, mimetypes.types_map.get(f".{ext}", "image/jpeg")
    return "jpg", "image/jpeg"


def rehost(source_url: str, *, source: str, source_event_id: str) -> Optional[str]:
    """Download `source_url`, upload to event-covers, return the public URL.

    Returns None (and logs) on any failure — a missing image must never abort the
    rest of the event; the admin can add one on review.
    """
    try:
        resp = http.get(source_url)
        data = resp.content
        if not data:
            log.warning("empty image body for %s", source_url)
            return None
    except Exception as e:  # noqa: BLE001
        log.warning("failed to download image %s: %s", source_url, e)
        return None

    ext, mime = _sniff(data, source_url)
    # Deterministic-ish, collision-resistant name namespaced by source.
    safe_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", source_event_id)[:80]
    filename = f"{source}/{safe_id}-{int(time.time())}.{ext}"

    if config.DRY_RUN:
        log.info("[dry-run] would upload %d bytes -> %s (%s)", len(data), filename, mime)
        return f"[dry-run]{filename}"

    try:
        client().storage.from_(config.EVENT_COVERS_BUCKET).upload(
            filename,
            data,
            {"content-type": mime, "upsert": "true"},
        )
        public = client().storage.from_(config.EVENT_COVERS_BUCKET).get_public_url(
            filename
        )
        return public
    except Exception as e:  # noqa: BLE001
        log.warning("failed to upload image %s: %s", filename, e)
        return None

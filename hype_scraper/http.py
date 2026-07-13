"""A shared HTTP session that gets past passive TLS fingerprinting.

Some sources (and Passline's asset/detail hosts) sit behind Cloudflare's passive
fingerprint check, which 403s a plain `requests` client. curl_cffi impersonates a
real Chrome TLS handshake + header order, which clears it. Used for the Passline
JSON API, its flyer images, and detail pages. (The Passline listing *page* is
behind a full JS challenge, but we avoid it entirely by calling the JSON API.)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from curl_cffi import requests as cffi_requests

log = logging.getLogger("hype_scraper.http")

# A recent desktop Chrome profile understood by curl_cffi.
_IMPERSONATE = "chrome124"

_session: Optional[cffi_requests.Session] = None


def session() -> cffi_requests.Session:
    global _session
    if _session is None:
        _session = cffi_requests.Session(impersonate=_IMPERSONATE, timeout=30)
    return _session


def get(url: str, *, retries: int = 3, **kwargs) -> cffi_requests.Response:
    """GET with impersonation and a small backoff on transient failures."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = session().get(url, **kwargs)
            if resp.status_code == 200:
                return resp
            # 403/503 from Cloudflare are sometimes transient right after a
            # challenge; back off and retry a couple times before giving up.
            if resp.status_code in (403, 429, 503) and attempt < retries:
                log.warning("GET %s -> %s (attempt %d/%d), retrying",
                            url, resp.status_code, attempt, retries)
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001 — network layer, log and retry
            last_exc = e
            if attempt < retries:
                log.warning("GET %s failed (%s), retry %d/%d", url, e, attempt, retries)
                time.sleep(2 * attempt)
            else:
                raise
    assert last_exc is not None
    raise last_exc

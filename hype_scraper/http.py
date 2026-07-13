"""A shared HTTP session that gets past Cloudflare's TLS fingerprinting.

Passline (and likely other ticketing sites) sit behind Cloudflare, which 403s a
plain `requests` client. curl_cffi impersonates a real Chrome TLS handshake +
header order, which clears the passive fingerprint check. If a site ever serves
the interactive JS challenge instead, that source should fall back to a headless
browser — out of scope for now, and Passline's passive check is what we hit.
"""
from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import quote_plus

from curl_cffi import requests as cffi_requests

from . import config

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


def get_rendered(url: str, **kwargs) -> cffi_requests.Response:
    """Fetch a URL that may be behind a Cloudflare JS challenge.

    If SCRAPERAPI_KEY is set, route through ScraperAPI with JS rendering (it
    solves the challenge and returns the final HTML). Otherwise fall back to a
    direct `get()` — which works for un-challenged hosts but will 403 on a
    challenged one (surfaced to the caller so the source can log and continue).
    """
    if not config.SCRAPERAPI_KEY:
        log.warning("SCRAPERAPI_KEY not set — fetching %s directly (may be blocked)", url)
        return get(url, **kwargs)

    proxied = (
        "https://api.scraperapi.com/"
        f"?api_key={config.SCRAPERAPI_KEY}"
        f"&render=true&country_code=mx&url={quote_plus(url)}"
    )
    # ScraperAPI renders + retries internally; give it a longer timeout and no
    # impersonation (we're talking to ScraperAPI, not the target).
    kwargs.setdefault("timeout", 70)
    return get(proxied, retries=2, **kwargs)

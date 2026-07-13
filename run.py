"""Entry point for the scheduled scrape.

Runs each registered source with FAILURE ISOLATION: if one source raises (e.g.
it changed its layout), we log it and continue with the others — a single broken
source never aborts the whole run. Exit code is non-zero only if EVERY source
failed, so Render surfaces a fully broken run but not a partial one.

Usage:
    python run.py                 # run all enabled sources
    DRY_RUN=1 python run.py       # parse + log, write nothing
    SOURCES=passline python run.py
"""
from __future__ import annotations

import logging
import sys

from hype_scraper import config, pipeline
from hype_scraper.sources import REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hype_scraper.run")


def _selected_sources() -> list[str]:
    if config.ENABLED_SOURCES:
        unknown = [s for s in config.ENABLED_SOURCES if s not in REGISTRY]
        if unknown:
            log.warning("ignoring unknown sources: %s", ", ".join(unknown))
        return [s for s in config.ENABLED_SOURCES if s in REGISTRY]
    return list(REGISTRY.keys())


def main() -> int:
    if config.DRY_RUN:
        log.info("DRY_RUN enabled — no writes to Supabase or storage")

    sources = _selected_sources()
    log.info("running sources: %s", ", ".join(sources))

    totals = {"seen": 0, "skipped": 0, "created": 0, "errors": 0}
    source_failures = 0

    for name in sources:
        scrape = REGISTRY[name]
        log.info("── source: %s ──────────────────────────────", name)
        try:
            events = scrape()
        except Exception as e:  # noqa: BLE001 — per-source isolation
            source_failures += 1
            log.exception("source %s failed to scrape: %s", name, e)
            continue

        try:
            stats = pipeline.process(events)
            for k in totals:
                totals[k] += stats.get(k, 0)
            log.info("%s: seen=%d created=%d skipped=%d errors=%d",
                     name, stats["seen"], stats["created"],
                     stats["skipped"], stats["errors"])
        except Exception as e:  # noqa: BLE001
            source_failures += 1
            log.exception("source %s failed in pipeline: %s", name, e)

    log.info("══ run complete: seen=%d created=%d skipped=%d errors=%d ══",
             totals["seen"], totals["created"], totals["skipped"], totals["errors"])

    # Fail the run only if every source blew up.
    if sources and source_failures == len(sources):
        log.error("all %d sources failed", len(sources))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

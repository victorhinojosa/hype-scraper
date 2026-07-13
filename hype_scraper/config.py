"""Environment/config loading. Fails fast if required secrets are missing."""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()  # loads .env if present (local dev); no-op on Render where envs are set

# The IANA timezone the app operates in. Passline tags its ISO datetimes with an
# Argentine offset, but the wall-clock time is the local SLP show time — see
# sources/passline.py. We never actually convert; this is here for documentation
# and for building "today" when prompting the AI fallback.
APP_TZ = "America/Mexico_City"

# Storage bucket that already exists in the Hype Supabase project.
EVENT_COVERS_BUCKET = "event-covers"


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"[config] Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = _require("SUPABASE_SERVICE_ROLE_KEY")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")

# Optional. A Cloudflare-solving proxy (ScraperAPI) used ONLY for pages behind a
# JS challenge — e.g. Passline's listing host. If unset, http.get_rendered()
# falls back to a direct fetch (fine for un-challenged hosts / local dev of
# detail parsing, but the Passline listing will 403 without it).
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip() or None

DRY_RUN = _flag("DRY_RUN")

# Optional allow-list of sources to run, e.g. SOURCES=passline
_sources_raw = os.environ.get("SOURCES", "").strip()
ENABLED_SOURCES = [s.strip() for s in _sources_raw.split(",") if s.strip()] or None
